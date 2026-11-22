"""Saves analyses, finds a recent one for the same menu and requirements (the 24-hour cache), and
keeps the shared rate-limit counts."""

import hashlib
import json
from datetime import timedelta
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert

from . import db
from .analyzer import MenuAnalysis
from .models import Analysis, RateLimit

CACHE_TTL = timedelta(hours=24)


def cache_key(menu_url: str, preferences: list[str], other: str) -> str:
    """The same link and requirements give the same key, whatever the checkbox order, letter case
    or spacing in "other"."""
    parsed = urlparse(menu_url)
    url = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()).geturl()
    canonical = json.dumps(
        {"url": url, "preferences": sorted(set(preferences)), "other": " ".join(other.casefold().split())},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def find_recent(key: str) -> MenuAnalysis | None:
    """The newest analysis with dishes for `key` from the last 24 hours, or None.

    Analyses without dishes (usually a blocked or empty page) are never served from the cache,
    so a temporary problem doesn't stick for a whole day.
    """
    if not db.enabled():
        return None
    query = (
        select(Analysis)
        .where(
            Analysis.cache_key == key,
            Analysis.created_at > func.now() - CACHE_TTL,
            func.jsonb_array_length(Analysis.dishes) > 0,
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    async with db.session() as session:
        row = await session.scalar(query)
    if row is None:
        return None
    return MenuAnalysis(restaurant_name=row.restaurant_name, location=row.location, dishes=row.dishes)


async def save(key: str, menu_url: str, preferences: list[str], other: str, analysis: MenuAnalysis) -> int | None:
    """Stores an analysis and returns its id, or None when the database is off."""
    if not db.enabled():
        return None
    row = Analysis(
        cache_key=key,
        menu_url=menu_url,
        preferences=preferences,
        other=other or None,
        restaurant_name=analysis.restaurant_name,
        location=analysis.location,
        dishes=[dish.model_dump(mode="json") for dish in analysis.dishes],
    )
    async with db.session() as session:
        session.add(row)
        await session.commit()
    return row.id


WINDOWS = {"minute": timedelta(minutes=1), "day": timedelta(days=1)}


async def hit_rate_limit(key: str, limit: int, window: Literal["minute", "day"]) -> float | None:
    """Counts one use of `key` in the current UTC minute or day. Returns None while the count is within
    `limit`, or the seconds until the window ends once it's over.

    One atomic statement, so copies of the app running at the same moment can't both slip through.
    """
    start = func.date_trunc(literal(window), func.now(), literal("UTC"))
    statement = (
        insert(RateLimit)
        .values(key=key, window_start=start, hits=1)
        .on_conflict_do_update(index_elements=[RateLimit.key, RateLimit.window_start], set_={"hits": RateLimit.hits + 1})
        .returning(RateLimit.hits, RateLimit.window_start, func.now())
    )
    async with db.session() as session:
        hits, window_start, now = (await session.execute(statement)).one()
        if hits == 1 and window == "day":
            # The first check of a new day clears out counts that no longer matter.
            await session.execute(delete(RateLimit).where(RateLimit.window_start < window_start - timedelta(days=1)))
        await session.commit()
    if hits <= limit:
        return None
    return max((window_start + WINDOWS[window] - now).total_seconds(), 1.0)

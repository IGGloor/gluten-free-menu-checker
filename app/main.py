"""Forkcast: rates a restaurant menu against your allergies and dietary requirements."""

import logging
import math
from contextlib import asynccontextmanager
import os
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.pool import NullPool

from . import db, store
from .analyzer import AnalysisError, GeminiBusyError, MenuAnalysis, Verdict, analyze_menu
from .menu_reader import JinaRateLimitError, MenuFetchError, fetch_menu_text
from .rate_limit import DailyCap, SlidingWindowLimiter

# Reads GEMINI_API_KEY etc. from a local .env file. On Vercel the variables are set in the project settings.
load_dotenv()

BASE_DIR = Path(__file__).parent
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
logger = logging.getLogger("forkcast")



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optional: without DATABASE_URL, analyses aren't saved or cached.
    # On Vercel an instance can be frozen or replaced between requests, so pooled connections could go
    # stale: open a fresh connection per use instead (Neon's pooled endpoint makes that cheap).
    db.configure(os.getenv("DATABASE_URL"), poolclass=NullPool if os.getenv("VERCEL") else None)
    yield
    await db.dispose()


app = FastAPI(title="Forkcast", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Checkbox value -> how the requirement is described to Gemini
PREFERENCES = {
    "gluten": ("Gluten intolerance", "gluten-sensitive (not coeliac)", "gluten-free"),
    "lactose": ("Lactose intolerance", "dairy intolerant", "lactose-free"),
    "vegetarian": ("Vegetarian", "vegetarian", "vegetarian"),
    "vegan": ("Vegan", "vegan", "vegan"),
}
MAX_OTHER_CHARS = 200

VERDICT_LABELS = {Verdict.clear: "Clear", Verdict.ask: "Ask first", Verdict.avoid: "Avoid"}
VERDICT_ORDER = {Verdict.clear: 0, Verdict.ask: 1, Verdict.avoid: 2}

# Limits on /analyze, because every menu check uses Jina Reader and Gemini quota. 0 turns a limit off.
ip_limiter = SlidingWindowLimiter(limit=int(os.getenv("RATE_LIMIT_PER_MINUTE", "5")), window=60)
daily_cap = DailyCap(limit=int(os.getenv("DAILY_ANALYSIS_CAP", "200")))


def render_form(request: Request, *, menu_url="", preferences=(), other="", error=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "preferences": PREFERENCES,
            "selected": set(preferences),
            "menu_url": menu_url,
            "other": other,
            "error": error,
            "max_other_chars": MAX_OTHER_CHARS,
        },
        status_code=status_code,
    )


def client_ip(request: Request) -> str:
    """The visitor's IP address, for the per-IP rate limit.

    On Vercel, request.client isn't the visitor, so use x-real-ip: Vercel sets it to the visitor's IP
    and overwrites any value sent by the visitor. Elsewhere the header could be faked, so it's ignored.
    """
    if os.getenv("VERCEL"):
        forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if forwarded.strip():
            return forwarded.strip()
    return request.client.host if request.client else "unknown"


async def ip_wait(ip: str) -> float | None:
    """Counts a menu check for `ip`. Returns None if allowed, or the seconds to wait.

    With a database the count is shared by every copy of the app (exact on Vercel). Without one, or if
    the database fails, each copy counts in its own memory.
    """
    if db.enabled() and ip_limiter.limit > 0:
        try:
            return await store.hit_rate_limit(f"ip:{ip}", ip_limiter.limit, "minute")
        except Exception:
            logger.exception("Shared rate limit failed; using this instance's own count")
    return ip_limiter.hit(ip)


async def take_daily_slot() -> bool:
    """Uses one of today's menu checks for the whole app. False when none are left."""
    if db.enabled() and daily_cap.limit > 0:
        try:
            return await store.hit_rate_limit("daily", daily_cap.limit, "day") is None
        except Exception:
            logger.exception("Shared daily cap failed; using this instance's own count")
    return daily_cap.take()


def guess_restaurant_name(menu_url: str) -> str:
    """Fallback when Gemini finds no name: "https://www.ramenhead.co.za/menu" -> "Ramenhead"."""
    host = (urlparse(menu_url).hostname or "").removeprefix("www.")
    name = host.split(".")[0].replace("-", " ").replace("_", " ").strip()
    return name.title() if name else "Restaurant"


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    menu_url: str = "",
    preferences: Annotated[list[str], Query()] = [],
    other: str = "",
):
    # The query parameters let "Try another menu" bring back the previous answers.
    return render_form(request, menu_url=menu_url, preferences=preferences, other=other)


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    menu_url: Annotated[str, Form()] = "",
    preferences: Annotated[list[str], Form()] = [],
    other: Annotated[str, Form()] = "",
):
    menu_url = menu_url.strip().rstrip(").,!?")
    preferences = [p for p in preferences if p in PREFERENCES]
    other = other.strip()[:MAX_OTHER_CHARS]
    form = {"menu_url": menu_url, "preferences": preferences, "other": other}

    if not preferences and not other:
        return render_form(
            request, **form, status_code=400,
            error="Please choose at least one requirement or describe your allergies.",
        )
    parsed = urlparse(menu_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return render_form(
            request, **form, status_code=400,
            error="That link doesn't look right. It should start with http:// or https://",
        )

    # Only valid submissions count towards the limits, so a mistyped link doesn't use up a check.
    ip = client_ip(request)
    wait = await ip_wait(ip)
    if wait is not None:
        logger.warning("Rate limit reached for %s", ip)
        response = render_form(
            request, **form, status_code=429,
            error="You're checking menus very quickly. Please wait a minute and try again.",
        )
        response.headers["Retry-After"] = str(math.ceil(wait))
        return response

    key = store.cache_key(menu_url, preferences, other)
    try:
        cached = await store.find_recent(key)
    except Exception:
        # The cache is a shortcut: if the database is down, rate the menu as usual.
        logger.exception("Cache lookup failed for %s", menu_url)
        cached = None
    if cached is not None:
        logger.info("Served %s from the cache", menu_url)
        return render_results(request, cached, form)

    if not await take_daily_slot():
        logger.warning("Daily cap of %d menu checks reached", daily_cap.limit)
        response = render_form(
            request, **form, status_code=429,
            error="Forkcast has reached its limit of menu checks for today. Please try again tomorrow.",
        )
        response.headers["Retry-After"] = str(daily_cap.seconds_until_reset())
        return response

    requirements = [PREFERENCES[p][1] for p in preferences]
    if other:
        requirements.append(other)

    try:
        menu_text = await fetch_menu_text(menu_url)
    except JinaRateLimitError as error:
        logger.warning("Menu fetch rate-limited for %s: %s", menu_url, error)
        return render_form(
            request, **form, status_code=503,
            error="Too many menus are being read right now. Please wait a minute and try again.",
        )
    except MenuFetchError as error:
        logger.warning("Menu fetch failed for %s: %s", menu_url, error)
        return render_form(
            request, **form, status_code=502,
            error="We couldn't read that page. Check the link, or try the restaurant's menu page directly.",
        )

    try:
        analysis = await analyze_menu(menu_text, menu_url, requirements)
    except GeminiBusyError as error:
        logger.warning("Gemini busy for %s: %s", menu_url, error)
        return render_form(
            request, **form, status_code=503,
            error="The menu reader is very busy right now. Please wait a minute and try again.",
        )
    except AnalysisError as error:
        logger.error("Menu analysis failed for %s: %s", menu_url, error)
        return render_form(
            request, **form, status_code=502,
            error="The menu reader is having trouble right now. Please try again in a minute.",
        )

    logger.info("Read %d characters from %s, found %d dishes", len(menu_text), menu_url, len(analysis.dishes))
    if not analysis.dishes:
        # Usually means Jina returned a block page, a cookie banner or an empty shell instead of the menu.
        logger.warning("No dishes for %s. Page text starts: %r", menu_url, menu_text[:500])

    try:
        analysis_id = await store.save(key, menu_url, preferences, other, analysis)
        if analysis_id is not None:
            logger.info("Saved analysis %d for %s", analysis_id, menu_url)
    except Exception:
        # Saving is an extra: the diner still gets their results.
        logger.exception("Couldn't save the analysis for %s", menu_url)

    return render_results(request, analysis, form)


def render_results(request: Request, analysis: MenuAnalysis, form: dict):
    menu_url, preferences, other = form["menu_url"], form["preferences"], form["other"]
    dishes = sorted(analysis.dishes, key=lambda d: VERDICT_ORDER[d.verdict])
    short_prefs = [PREFERENCES[p][2] for p in preferences] + ([other] if other else [])

    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "restaurant": analysis.restaurant_name or guess_restaurant_name(menu_url),
            "location": analysis.location,
            "menu_url": menu_url,
            "dishes": dishes,
            "clear_count": sum(d.verdict == Verdict.clear for d in dishes),
            "labels": VERDICT_LABELS,
            "matched_for": ", ".join(short_prefs),
            "today": date.today().strftime("%d %B %Y").lstrip("0"),
            "retry_query": urlencode(form, doseq=True),
        },
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

"""Saving analyses and the 24-hour cache, against a real PostgreSQL test database.

Start one with `docker compose up -d` (it creates "forkcast_test"), or point TEST_DATABASE_URL at
another database whose name contains "test". The tests delete everything in the analyses table.

Without a reachable test database these tests are skipped locally, and fail on CI.
Jina Reader and Gemini are faked.
"""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import db, main, store
from app.analyzer import Dish, MenuAnalysis, Verdict
from app.rate_limit import DailyCap, SlidingWindowLimiter

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://forkcast:forkcast@localhost:5432/forkcast_test"
)
ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"

MENU_URL = "https://www.ramen-head.co.za/menu"
FORM = {"menu_url": MENU_URL, "preferences": ["gluten", "vegan"], "other": "peanut allergy"}

client = TestClient(main.app)


# --- Helpers and fixtures -----------------------------------------------------------------------


def sql(statement, **params):
    """Runs one SQL statement against the test database and returns its rows (if any)."""

    async def run():
        url, connect_args = db.to_async_url(TEST_DATABASE_URL)
        engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return result.mappings().all() if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def alembic(*commands):
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = TEST_DATABASE_URL
    config.attributes["configure_logger"] = False
    for name, revision in commands:
        getattr(command, name)(config, revision)


@pytest.fixture(scope="session")
def test_database():
    """Checks the test database is safe and reachable, then migrates it (down and up again, so the
    downgrade is tested too)."""
    name = make_url(TEST_DATABASE_URL).database or ""
    if "test" not in name:
        pytest.fail(f"TEST_DATABASE_URL must point at a test database (got {name!r}); the tests delete its rows.")
    try:
        sql("SELECT 1")
    except Exception as error:
        message = f"No test database at {make_url(TEST_DATABASE_URL).render_as_string()}: {error!r}"
        if os.getenv("CI"):
            pytest.fail(message)
        pytest.skip(message + ". Start one with `docker compose up -d`.")
    alembic(("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head"))
    return TEST_DATABASE_URL


@pytest.fixture
def database(test_database, monkeypatch):
    """Empty tables, with the app connected to them."""
    sql("TRUNCATE analyses, rate_limits RESTART IDENTITY")
    # NullPool: TestClient runs each request in its own event loop, so connections can't be reused.
    db.configure(test_database, poolclass=NullPool)
    monkeypatch.setattr(main, "ip_limiter", SlidingWindowLimiter(limit=0, window=60))
    yield
    asyncio.run(db.dispose())


class FakeGemini:
    def __init__(self):
        self.calls = []
        self.dishes = [
            Dish(name="Vegetable Gyoza <b>", verdict=Verdict.avoid, note="Wheat wrappers"),
            Dish(name="Edamame", verdict=Verdict.clear, note="Steamed with sea salt"),
            Dish(name="Shiitake Ramen", verdict=Verdict.ask, note="Ask for rice noodles"),
        ]

    async def fetch(self, url):
        return "Edamame ... Shiitake Ramen ... Vegetable Gyoza"

    async def analyze(self, menu_text, menu_url, requirements):
        self.calls.append((menu_url, requirements))
        return MenuAnalysis(restaurant_name=None, location="12 Bree St, Cape Town", dishes=list(self.dishes))


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(main, "fetch_menu_text", fake.fetch)
    monkeypatch.setattr(main, "analyze_menu", fake.analyze)
    return fake


# --- Saving -------------------------------------------------------------------------------------


def test_saves_each_analysis(database, gemini):
    assert client.post("/analyze", data=FORM).status_code == 200

    [row] = sql("SELECT * FROM analyses")
    assert row["menu_url"] == MENU_URL
    assert row["preferences"] == ["gluten", "vegan"]
    assert row["other"] == "peanut allergy"
    assert row["restaurant_name"] is None  # the page guesses "Ramen Head"; the raw answer is kept
    assert row["location"] == "12 Bree St, Cape Town"
    assert row["dishes"][0] == {"name": "Vegetable Gyoza <b>", "verdict": "avoid", "note": "Wheat wrappers"}
    assert len(row["dishes"]) == 3
    assert row["cache_key"] == store.cache_key(MENU_URL, ["gluten", "vegan"], "peanut allergy")
    assert row["created_at"] is not None


def test_failed_analyses_are_not_saved(database, gemini, monkeypatch):
    async def failing_fetch(url):
        raise main.MenuFetchError("boom")

    monkeypatch.setattr(main, "fetch_menu_text", failing_fetch)
    assert client.post("/analyze", data=FORM).status_code == 502
    assert sql("SELECT count(*) AS n FROM analyses")[0]["n"] == 0


# --- The 24-hour cache --------------------------------------------------------------------------


def test_same_request_is_served_from_the_cache(database, gemini):
    first = client.post("/analyze", data=FORM)
    second = client.post("/analyze", data=FORM)

    assert second.status_code == 200
    assert second.text == first.text  # the cached page is identical
    assert "Ramen Head" in second.text
    assert "Vegetable Gyoza &lt;b&gt;" in second.text
    assert second.text.index("Edamame") < second.text.index("Shiitake Ramen") < second.text.index("Vegetable Gyoza")
    assert len(gemini.calls) == 1
    assert sql("SELECT count(*) AS n FROM analyses")[0]["n"] == 1  # cache hits aren't saved again


def test_cache_ignores_checkbox_order_case_and_spacing(database, gemini):
    client.post("/analyze", data={"menu_url": MENU_URL, "preferences": ["vegan", "gluten"], "other": "Peanut  Allergy"})
    client.post(
        "/analyze",
        data={"menu_url": "https://WWW.Ramen-Head.co.za/menu", "preferences": ["gluten", "vegan"], "other": "peanut allergy"},
    )
    assert len(gemini.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"menu_url": "https://www.ramen-head.co.za/drinks"},
        {"preferences": ["gluten"]},
        {"other": "shellfish allergy"},
    ],
    ids=["other menu", "other preferences", "other allergies"],
)
def test_different_request_is_not_served_from_the_cache(database, gemini, change):
    client.post("/analyze", data=FORM)
    client.post("/analyze", data={**FORM, **change})
    assert len(gemini.calls) == 2


def test_cache_lasts_24_hours(database, gemini):
    client.post("/analyze", data=FORM)

    sql("UPDATE analyses SET created_at = now() - interval '23 hours 59 minutes'")
    client.post("/analyze", data=FORM)
    assert len(gemini.calls) == 1

    sql("UPDATE analyses SET created_at = now() - interval '24 hours 1 minute'")
    client.post("/analyze", data=FORM)
    assert len(gemini.calls) == 2
    assert sql("SELECT count(*) AS n FROM analyses")[0]["n"] == 2


def test_menu_without_dishes_is_saved_but_not_cached(database, gemini):
    gemini.dishes = []
    client.post("/analyze", data=FORM)
    client.post("/analyze", data=FORM)
    assert len(gemini.calls) == 2
    assert sql("SELECT count(*) AS n FROM analyses")[0]["n"] == 2


def test_cache_hits_do_not_use_the_daily_cap(database, gemini, monkeypatch):
    monkeypatch.setattr(main, "daily_cap", DailyCap(limit=1))
    assert client.post("/analyze", data=FORM).status_code == 200
    assert client.post("/analyze", data=FORM).status_code == 200  # from the cache
    other_menu = client.post("/analyze", data={**FORM, "menu_url": "https://www.ramen-head.co.za/drinks"})
    assert other_menu.status_code == 429


# --- Shared rate limits -------------------------------------------------------------------------


def test_ip_limit_is_shared_between_app_instances(database, gemini, monkeypatch):
    limiter = SlidingWindowLimiter(limit=2, window=60)
    monkeypatch.setattr(main, "ip_limiter", limiter)
    first, second = TestClient(main.app, client=("203.0.113.1", 1)), TestClient(main.app, client=("203.0.113.2", 1))

    assert first.post("/analyze", data=FORM).status_code == 200
    assert first.post("/analyze", data=FORM).status_code == 200
    limiter.reset()  # as if the next request reached another copy of the app, with empty memory
    response = first.post("/analyze", data=FORM)
    assert response.status_code == 429
    assert "checking menus very quickly" in response.text
    assert 1 <= int(response.headers["Retry-After"]) <= 60

    assert second.post("/analyze", data=FORM).status_code == 200  # other visitors aren't affected
    rows = {r["key"]: r["hits"] for r in sql("SELECT key, hits FROM rate_limits")}
    assert rows["ip:203.0.113.1"] == 3 and rows["ip:203.0.113.2"] == 1


def test_daily_cap_is_shared_between_app_instances(database, gemini, monkeypatch):
    cap = DailyCap(limit=2)
    monkeypatch.setattr(main, "daily_cap", cap)
    for page in ("menu", "drinks"):
        assert client.post("/analyze", data={**FORM, "menu_url": f"https://www.ramen-head.co.za/{page}"}).status_code == 200
    cap.reset()  # another copy of the app
    response = client.post("/analyze", data={**FORM, "menu_url": "https://www.ramen-head.co.za/desserts"})
    assert response.status_code == 429
    assert "limit of menu checks for today" in response.text
    assert len(gemini.calls) == 2


def test_old_rate_limit_rows_are_cleared_each_day(database):
    sql("INSERT INTO rate_limits VALUES ('ip:203.0.113.9', now() - interval '3 days', 4), ('daily', now() - interval '3 days', 9)")

    async def first_check_of_the_day():
        await store.hit_rate_limit("daily", 200, "day")

    db.configure(TEST_DATABASE_URL, poolclass=NullPool)
    asyncio.run(first_check_of_the_day())
    assert [r["hits"] for r in sql("SELECT hits FROM rate_limits")] == [1]


# --- Without a working database (no test database needed) ---------------------------------------


def test_database_errors_do_not_break_the_page(gemini):
    db.configure("postgresql://nobody:secret@127.0.0.1:1/nothing", poolclass=NullPool)
    try:
        response = client.post("/analyze", data=FORM)
    finally:
        asyncio.run(db.dispose())
    assert response.status_code == 200
    assert "Edamame" in response.text
    assert len(gemini.calls) == 1


def test_app_connects_on_startup_only_when_database_url_is_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://forkcast:forkcast@localhost:5432/forkcast")
    with TestClient(main.app):
        assert db.enabled()
    assert not db.enabled()

    monkeypatch.delenv("DATABASE_URL")
    with TestClient(main.app):
        assert not db.enabled()


def test_on_vercel_connections_are_not_pooled(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://forkcast:forkcast@localhost:5432/forkcast")
    with TestClient(main.app):
        assert not isinstance(db._engine.pool, NullPool)  # a normal server keeps a pool
    monkeypatch.setenv("VERCEL", "1")
    with TestClient(main.app):
        assert isinstance(db._engine.pool, NullPool)


def test_neon_style_urls_are_converted_for_asyncpg():
    url, connect_args = db.to_async_url(
        "postgresql://user:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "ep-x-pooler.eu-central-1.aws.neon.tech"
    assert dict(url.query) == {}
    assert connect_args == {"ssl": "require"}

    url, connect_args = db.to_async_url("postgres://u:p@localhost/forkcast")
    assert url.drivername == "postgresql+asyncpg" and connect_args == {}


def test_cache_key():
    key = store.cache_key(MENU_URL, ["gluten", "vegan"], "peanut allergy")
    assert key == store.cache_key("HTTPS://www.Ramen-Head.co.za/menu", ["vegan", "gluten", "vegan"], " Peanut   ALLERGY ")
    assert key != store.cache_key(MENU_URL + "/", ["gluten", "vegan"], "peanut allergy")
    assert key != store.cache_key(MENU_URL, ["gluten"], "peanut allergy")

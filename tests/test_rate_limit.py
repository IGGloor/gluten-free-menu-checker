"""Per-IP rate limit and daily cap on /analyze. Jina Reader and Gemini are faked."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.analyzer import Dish, MenuAnalysis, Verdict
from app.rate_limit import DailyCap, SlidingWindowLimiter

MENU_URL = "https://www.ramen-head.co.za/menu"
FORM = {"menu_url": MENU_URL, "preferences": ["vegan"], "other": "peanuts"}


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def client_for(ip):
    return TestClient(main.app, client=(ip, 50000))


@pytest.fixture
def analyses(monkeypatch):
    """Fakes Jina and Gemini, and counts how many menus were actually analysed."""
    calls = []

    async def fake_fetch(url):
        return "Edamame"

    async def fake_analyze(menu_text, menu_url, requirements):
        calls.append(menu_url)
        return MenuAnalysis(dishes=[Dish(name="Edamame", verdict=Verdict.clear)])

    monkeypatch.setattr(main, "fetch_menu_text", fake_fetch)
    monkeypatch.setattr(main, "analyze_menu", fake_analyze)
    return calls


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(main, "ip_limiter", SlidingWindowLimiter(limit=2, window=60, clock=clock))
    return clock


# --- Per-IP limit -------------------------------------------------------------------------------


def test_ip_limit_blocks_after_limit(analyses, clock):
    client = client_for("203.0.113.1")
    assert client.post("/analyze", data=FORM).status_code == 200
    assert client.post("/analyze", data=FORM).status_code == 200

    response = client.post("/analyze", data=FORM)
    assert response.status_code == 429
    assert "checking menus very quickly" in response.text
    assert response.headers["Retry-After"] == "60"
    assert f'value="{MENU_URL}"' in response.text  # inputs are kept
    assert 'value="vegan" checked' in response.text
    assert len(analyses) == 2  # the blocked request never reached Jina or Gemini


def test_ip_limit_frees_up_after_the_window(analyses, clock):
    client = client_for("203.0.113.1")
    client.post("/analyze", data=FORM)
    clock.now += 30
    client.post("/analyze", data=FORM)
    assert client.post("/analyze", data=FORM).status_code == 429

    clock.now += 31  # the first request is now more than 60 seconds old
    assert client.post("/analyze", data=FORM).status_code == 200


def test_ip_limit_is_per_ip(analyses, clock):
    first, second = client_for("203.0.113.1"), client_for("203.0.113.2")
    first.post("/analyze", data=FORM)
    first.post("/analyze", data=FORM)
    assert first.post("/analyze", data=FORM).status_code == 429
    assert second.post("/analyze", data=FORM).status_code == 200


def test_invalid_submissions_do_not_count(analyses, clock):
    client = client_for("203.0.113.1")
    for _ in range(5):
        assert client.post("/analyze", data={"menu_url": "not a link", "preferences": ["vegan"]}).status_code == 400
        assert client.post("/analyze", data={"menu_url": MENU_URL}).status_code == 400
    assert client.post("/analyze", data=FORM).status_code == 200


def test_form_page_is_not_limited(clock):
    client = client_for("203.0.113.1")
    for _ in range(5):
        assert client.get("/").status_code == 200


# --- Daily cap ----------------------------------------------------------------------------------


def test_daily_cap_blocks_everyone(analyses, monkeypatch):
    now = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "daily_cap", DailyCap(limit=2, now=lambda: now))

    assert client_for("203.0.113.1").post("/analyze", data=FORM).status_code == 200
    assert client_for("203.0.113.2").post("/analyze", data=FORM).status_code == 200

    response = client_for("203.0.113.3").post("/analyze", data=FORM)
    assert response.status_code == 429
    assert "limit of menu checks for today" in response.text
    assert response.headers["Retry-After"] == str(6 * 60 * 60)  # until midnight UTC
    assert f'value="{MENU_URL}"' in response.text
    assert len(analyses) == 2


def test_daily_cap_resets_at_midnight_utc():
    now = datetime(2026, 11, 21, 23, 59, tzinfo=timezone.utc)
    cap = DailyCap(limit=1, now=lambda: now)
    assert cap.take()
    assert not cap.take()

    now = datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc)
    assert cap.take()


def test_zero_turns_limits_off():
    limiter = SlidingWindowLimiter(limit=0, window=60)
    cap = DailyCap(limit=0)
    for _ in range(100):
        assert limiter.hit("203.0.113.1") is None
        assert cap.take()


def test_stale_ips_are_forgotten(monkeypatch):
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window=60, clock=clock)
    monkeypatch.setattr(SlidingWindowLimiter, "_SWEEP_AT", 3)
    for i in range(3):
        limiter.hit(f"203.0.113.{i}")
    clock.now += 61
    limiter.hit("203.0.113.99")  # crosses the sweep threshold
    assert list(limiter._hits) == ["203.0.113.99"]


# --- Visitor IP on Vercel -----------------------------------------------------------------------


def test_on_vercel_the_ip_comes_from_x_real_ip(analyses, clock, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    client = client_for("10.0.0.1")  # on Vercel this is infrastructure, shared by every visitor
    first = {"x-real-ip": "203.0.113.1"}
    client.post("/analyze", data=FORM, headers=first)
    client.post("/analyze", data=FORM, headers=first)
    assert client.post("/analyze", data=FORM, headers=first).status_code == 429
    # A different visitor through the same Vercel address isn't blocked
    assert client.post("/analyze", data=FORM, headers={"x-real-ip": "203.0.113.2"}).status_code == 200
    assert client.post("/analyze", data=FORM, headers={"x-forwarded-for": "203.0.113.3"}).status_code == 200


def test_off_vercel_ip_headers_are_ignored(analyses, clock, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    client = client_for("203.0.113.1")
    client.post("/analyze", data=FORM, headers={"x-real-ip": "198.51.100.1"})
    client.post("/analyze", data=FORM, headers={"x-real-ip": "198.51.100.2"})
    # Faking the header doesn't get round the limit
    assert client.post("/analyze", data=FORM, headers={"x-real-ip": "198.51.100.3"}).status_code == 429

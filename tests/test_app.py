"""Route tests. Jina Reader and Gemini are replaced with fakes, so no network or API key is needed."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.analyzer import AnalysisError, Dish, GeminiBusyError, MenuAnalysis, Verdict
from app.menu_reader import JinaRateLimitError, MenuFetchError

client = TestClient(main.app)

MENU_URL = "https://www.ramen-head.co.za/menu"


@pytest.fixture
def fake_services(monkeypatch):
    calls = {}

    async def fake_fetch(url):
        calls["url"] = url
        return "Edamame ... Shiitake Ramen ... Vegetable Gyoza"

    async def fake_analyze(menu_text, menu_url, requirements):
        calls["requirements"] = requirements
        return MenuAnalysis(
            restaurant_name=None,
            location="12 Bree St, Cape Town",
            dishes=[
                Dish(name="Vegetable Gyoza <b>", verdict=Verdict.avoid, note="Wheat wrappers"),
                Dish(name="Edamame", verdict=Verdict.clear, note="Steamed with sea salt"),
                Dish(name="Shiitake Ramen", verdict=Verdict.ask, note="Ask for rice noodles"),
            ],
        )

    monkeypatch.setattr(main, "fetch_menu_text", fake_fetch)
    monkeypatch.setattr(main, "analyze_menu", fake_analyze)
    return calls


def test_form_page():
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="menu_url"' in response.text


def test_form_is_prefilled_from_query():
    response = client.get("/", params={"menu_url": MENU_URL, "preferences": ["vegan"], "other": "peanuts"})
    assert f'value="{MENU_URL}"' in response.text
    assert 'value="vegan" checked' in response.text
    assert 'value="peanuts"' in response.text


def test_results_page(fake_services):
    response = client.post(
        "/analyze",
        data={"menu_url": MENU_URL, "preferences": ["gluten", "vegan"], "other": "peanut allergy"},
    )
    assert response.status_code == 200
    html = response.text

    assert fake_services["url"] == MENU_URL
    assert fake_services["requirements"] == ["gluten-sensitive (not coeliac)", "vegan", "peanut allergy"]
    assert "1 of 3 dishes look clear" in html
    assert "Ramen Head" in html  # name guessed from the hostname
    assert "12 Bree St, Cape Town" in html
    assert "Vegetable Gyoza &lt;b&gt;" in html  # escaped
    # Sorted Clear, Ask first, Avoid
    assert html.index("Edamame") < html.index("Shiitake Ramen") < html.index("Vegetable Gyoza")


def test_requires_a_requirement(fake_services):
    response = client.post("/analyze", data={"menu_url": MENU_URL})
    assert response.status_code == 400
    assert "at least one requirement" in response.text


def test_rejects_bad_url(fake_services):
    response = client.post("/analyze", data={"menu_url": "ramenhead", "preferences": ["vegan"]})
    assert response.status_code == 400
    assert "doesn&#39;t look right" in response.text


def test_menu_fetch_error(monkeypatch):
    async def failing_fetch(url):
        raise MenuFetchError("boom")

    monkeypatch.setattr(main, "fetch_menu_text", failing_fetch)
    response = client.post("/analyze", data={"menu_url": MENU_URL, "preferences": ["vegan"]})
    assert response.status_code == 502
    assert "couldn&#39;t read that page" in response.text
    assert f'value="{MENU_URL}"' in response.text  # inputs are kept


def test_menu_fetch_rate_limited(monkeypatch):
    async def limited_fetch(url):
        raise JinaRateLimitError("429")

    monkeypatch.setattr(main, "fetch_menu_text", limited_fetch)
    response = client.post("/analyze", data={"menu_url": MENU_URL, "preferences": ["vegan"]})
    assert response.status_code == 503
    assert "Too many menus" in response.text


def test_analysis_error(fake_services, monkeypatch):
    async def failing_analyze(*args):
        raise AnalysisError("boom")

    monkeypatch.setattr(main, "analyze_menu", failing_analyze)
    response = client.post("/analyze", data={"menu_url": MENU_URL, "preferences": ["vegan"]})
    assert response.status_code == 502
    assert "having trouble" in response.text


def test_gemini_busy(fake_services, monkeypatch):
    async def busy_analyze(*args):
        raise GeminiBusyError("503 UNAVAILABLE")

    monkeypatch.setattr(main, "analyze_menu", busy_analyze)
    response = client.post("/analyze", data={"menu_url": MENU_URL, "preferences": ["vegan"]})
    assert response.status_code == 503
    assert "very busy" in response.text


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}

"""Forkcast: rates a restaurant menu against your allergies and dietary requirements."""

import logging
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .analyzer import AnalysisError, GeminiBusyError, Verdict, analyze_menu
from .menu_reader import MenuFetchError, fetch_menu_text

# Reads GEMINI_API_KEY etc. from a local .env file. On Render the variables are set in the dashboard.
load_dotenv()

BASE_DIR = Path(__file__).parent
logger = logging.getLogger("forkcast")

app = FastAPI(title="Forkcast", docs_url=None, redoc_url=None)
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

    requirements = [PREFERENCES[p][1] for p in preferences]
    if other:
        requirements.append(other)

    try:
        menu_text = await fetch_menu_text(menu_url)
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

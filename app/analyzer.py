"""Asks Google Gemini to sort a menu's dishes into Clear, Ask first or Avoid."""

import os
import re
from enum import Enum
from typing import Optional

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

DEFAULT_MODEL = "gemini-3.8-flash"
# Tried when the main model is still overloaded after the retries below.
DEFAULT_FALLBACK_MODEL = "gemini-3.6-flash"

# "Too many requests" and "overloaded" errors are usually temporary, so retry them with backoff.
BUSY_STATUS_CODES = [429, 500, 503, 504]
RETRY_OPTIONS = types.HttpRetryOptions(attempts=3, initial_delay=2, max_delay=10, http_status_codes=BUSY_STATUS_CODES)

SYSTEM_PROMPT = """\
You help people with allergies and dietary requirements choose dishes from a restaurant menu.

Rate every dish on the menu against the diner's requirements, and only those requirements:
- "clear": suits every requirement as described on the menu.
- "ask": could suit them with a simple change (e.g. no croutons, sauce on the side), or the menu \
doesn't say enough to be sure.
- "avoid": clearly doesn't suit them and can't easily be changed.

For each dish give a short note (one sentence at most) explaining the verdict or the change to ask for.
Include drinks and sides only if they are a real part of the menu. Skip navigation, opening \
hours and other non-menu text.

Also report the restaurant's name and its location. Only use an address that appears in the page \
content; never guess or invent one. If the page lists several branches, use "Multiple locations". \
Leave the location empty if none is given.\
"""


class Verdict(str, Enum):
    clear = "clear"
    ask = "ask"
    avoid = "avoid"


class Dish(BaseModel):
    name: str
    verdict: Verdict
    note: str = ""


class MenuAnalysis(BaseModel):
    restaurant_name: Optional[str] = None
    location: Optional[str] = None
    dishes: list[Dish] = Field(default_factory=list)


class AnalysisError(Exception):
    """Gemini couldn't produce a usable answer."""


class GeminiBusyError(AnalysisError):
    """Every model was overloaded or rate-limited; trying again later usually works."""


# Treat "unknown" / "N/A" / "not listed" as no value
_EMPTY_VALUE = re.compile(r"^(unknown|n/?a|none|not (found|listed|provided|available))\.?$", re.I)


def _clean(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text if text and not _EMPTY_VALUE.match(text) else None


async def analyze_menu(menu_text: str, menu_url: str, requirements: list[str]) -> MenuAnalysis:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise AnalysisError("GEMINI_API_KEY is not set")

    prompt = (
        f"Restaurant URL: {menu_url}\n"
        f"Dietary requirements: {', '.join(requirements)}\n\n"
        "Page content below (may include non-menu text such as navigation or opening hours; "
        "ignore that):\n\n"
        f"{menu_text}"
    )

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(retry_options=RETRY_OPTIONS))
    models = [os.getenv("GEMINI_MODEL", DEFAULT_MODEL), os.getenv("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)]
    models = list(dict.fromkeys(m for m in models if m))  # drop blanks and duplicates

    for model in models:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=MenuAnalysis,
                ),
            )
            break
        except errors.APIError as error:
            if error.code not in BUSY_STATUS_CODES:
                raise AnalysisError(f"Gemini request failed ({model}): {error}") from error
            busy_error = error  # still busy after the retries: try the next model
        except Exception as error:  # network problems, timeouts
            raise AnalysisError(f"Gemini request failed ({model}): {error}") from error
    else:
        raise GeminiBusyError(f"Gemini is busy ({', '.join(models)}): {busy_error}") from busy_error

    analysis = response.parsed
    if not isinstance(analysis, MenuAnalysis):
        try:
            analysis = MenuAnalysis.model_validate_json(response.text or "")
        except ValidationError as error:
            raise AnalysisError("Gemini returned an answer in an unexpected shape") from error

    analysis.restaurant_name = _clean(analysis.restaurant_name)
    analysis.location = _clean(analysis.location)
    analysis.dishes = [d for d in analysis.dishes if d.name.strip()]
    return analysis

"""Fetches a restaurant's web page as plain text through Jina Reader."""

import os

import httpx

JINA_READER_URL = "https://r.jina.ai/"
# Menu pages can be long; Gemini doesn't need more than this to find the dishes.
MAX_MENU_CHARS = 40_000


class MenuFetchError(Exception):
    """The menu page couldn't be read."""


async def fetch_menu_text(url: str) -> str:
    headers = {"X-Return-Format": "markdown"}
    # Optional: Jina works without a key for light use, a key raises the rate limit.
    jina_key = os.getenv("JINA_API_KEY")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(JINA_READER_URL + url, headers=headers)
    except httpx.HTTPError as error:
        raise MenuFetchError(f"Could not reach Jina Reader: {error}") from error

    if response.status_code != 200:
        raise MenuFetchError(f"Jina Reader responded with {response.status_code}")

    text = response.text.strip()
    if not text:
        raise MenuFetchError("The page came back empty")
    return text[:MAX_MENU_CHARS]

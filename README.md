# Forkcast — Menu Matcher

*Your food forecast before you eat out.*

Forkcast tells you what you can eat at a restaurant. Choose your dietary requirements (and type in any other allergies), paste a link to the restaurant's menu, and you get a list of dishes rated **Clear**, **Ask first** or **Avoid**. The restaurant's location is shown too when its website lists one. You can print the results or save them as a PDF.

It's a Python [FastAPI](https://fastapi.tiangolo.com/) app with server-rendered [Jinja2](https://jinja.palletsprojects.com/) pages. It replaces the earlier n8n workflow, which you can still find in the git history.

## How it works

1. You submit the form on `/`, and it posts to `/analyze`.
2. The server fetches the menu page as text through [Jina Reader](https://jina.ai/reader/) (`https://r.jina.ai/<menu link>`).
3. It sends the text and your requirements to the **Google Gemini API**. Gemini returns structured JSON: the restaurant's name, its location, and each dish with a verdict (`clear` / `ask` / `avoid`) and a short note.
4. The server renders the results page, with dishes sorted Clear → Ask first → Avoid.

If something fails (a bad link, a page that can't be read, a Gemini error), the form shows again with a friendly message and your inputs still filled in. When Gemini is overloaded (`503`) or rate-limited (`429`), the app retries with backoff and then tries a fallback model before giving up.

The real error is always printed in the terminal running `uvicorn`, so check there first if the page shows an error.

> Put your key in **`.env`**, not `.env.example`. The app only reads `.env`, and `.env.example` is committed to git.

## Project files

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI routes: form, `/analyze`, `/healthz` |
| `app/menu_reader.py` | Fetches the menu page through Jina Reader |
| `app/analyzer.py` | Gemini prompt, response schema and clean-up |
| `app/templates/` | Jinja2 templates: `base.html`, `index.html` (form), `results.html` |
| `app/static/style.css` | Monochrome "Classical" design, including print styles |
| `app/static/loading.js` | Optional loading overlay while the menu is read. The form works without it. |
| `tests/test_app.py` | Route tests with Jina and Gemini faked (no network or key needed) |
| `render.yaml` | Render Blueprint for a free web service |
| `.env.example` | Template for your local `.env` |

## Run it locally

You need Python 3.10 or newer.

```bash
# 1. Create a virtual environment and install the dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

# 2. Add your Gemini API key
copy .env.example .env          # Windows (macOS / Linux: cp .env.example .env)
#    then open .env and paste your key after GEMINI_API_KEY=

# 3. Start the app
uvicorn app.main:app --reload
```

Then open <http://localhost:8000>.

Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Your Google Gemini API key |
| `GEMINI_MODEL` | No | The Gemini model to use. Default: `gemini-3.8-flash` |
| `GEMINI_FALLBACK_MODEL` | No | Used when the main model is still overloaded after 3 tries. Default: `gemini-3.6-flash` |
| `JINA_API_KEY` | No | A Jina Reader key. Jina works without one for light use; a key raises the rate limit. |

> **Never commit your API key.** `.env` is in `.gitignore`. Only `.env.example` (which has no values) belongs in git. On Render, set the key in the dashboard.

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploy to Render (free)

The repo includes a `render.yaml` Blueprint, so Render can set everything up from it.

1. Push this repository to GitHub (or GitLab / Bitbucket).
2. In the [Render dashboard](https://dashboard.render.com/), choose **New → Blueprint** and pick the repository.
3. Render reads `render.yaml` and asks for the secret values:
   - `GEMINI_API_KEY`: paste your key.
   - `JINA_API_KEY`: optional. Leave it empty if you don't have one.
4. Click **Apply**. Render installs `requirements.txt` and starts the app with
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. When the deploy finishes, open the `https://forkcast-….onrender.com` address it gives you.

Prefer to set it up by hand? Choose **New → Web Service** instead, with:

| Setting | Value |
| --- | --- |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Instance type | Free |
| Environment | `GEMINI_API_KEY` = your key, `PYTHON_VERSION` = `3.12.8` |
| Health check path | `/healthz` |

**About the free plan:** the service goes to sleep after about 15 minutes without visits. The next visit wakes it up, which can take up to a minute before the page loads.

Each push to the connected branch redeploys automatically.

## Design notes

- Monochrome palette (`#111111` on `#FFFFFF`, greys for dividers and tags). Cormorant Garamond for headings, Lora for body text.
- The **Avoid** tag has an × glyph and the dish name is greyed out, so it is easy to tell apart from **Clear** in black and white.
- The loading overlay moves through 5 steps, then keeps rotating extra messages until the results page arrives, so it never looks stuck.
- **Print or save as PDF** uses the browser's print dialog. Print styles hide the buttons and add the menu link and date.
- **Try another menu** goes back to the form with your previous answers filled in.

## Future ideas

- **Menu PDF upload:** accept a PDF, extract its text on the server and send that to Gemini instead of the Jina text.
- **A backup location lookup:** when the restaurant's page doesn't list an address, look it up with the Google Places API.

## Disclaimer

The suggestions are generated by AI as part of a fun project and may not be accurate. Always confirm ingredients and allergens with the restaurant, especially if you have an allergy or coeliac disease.

# Forkcast — Menu Matcher

[![Tests](https://github.com/IGGloor/gluten-free-menu-checker/actions/workflows/tests.yml/badge.svg)](https://github.com/IGGloor/gluten-free-menu-checker/actions/workflows/tests.yml)

*Your food forecast before you eat out.*

Forkcast tells you what you can eat at a restaurant. Choose your dietary requirements (and type in any other allergies), paste a link to the restaurant's menu, and you get a list of dishes rated **Clear**, **Ask first** or **Avoid**. The restaurant's location is shown too when its website lists one. You can print the results or save them as a PDF.

It's a Python [FastAPI](https://fastapi.tiangolo.com/) app with server-rendered [Jinja2](https://jinja.palletsprojects.com/) pages, hosted on [Vercel](https://vercel.com/) with an optional [Neon](https://neon.com/) PostgreSQL database. It replaces the earlier n8n workflow, which you can still find in the git history.

## How it works

1. You submit the form on `/`, and it posts to `/analyze`.
2. The server fetches the menu page as text through [Jina Reader](https://jina.ai/reader/) (`https://r.jina.ai/<menu link>`).
3. It sends the text and your requirements to the **Google Gemini API**. Gemini returns structured JSON: the restaurant's name, its location, and each dish with a verdict (`clear` / `ask` / `avoid`) and a short note.
4. The server renders the results page, with dishes sorted Clear → Ask first → Avoid.

**Saving and the 24-hour cache (optional).** With `DATABASE_URL` set, every analysis is saved in PostgreSQL. If someone asks about the same menu with the same requirements within 24 hours, they get the saved result straight away, without calling Jina or Gemini. Checkbox order, letter case and spacing don't matter. Results with no dishes (usually a blocked page) are saved but never served from the cache, and cache hits don't count towards the daily cap. Without `DATABASE_URL` the app works as before, and if the database has a problem the menu is rated as usual.

To protect the Gemini and Jina quotas, `/analyze` is rate-limited: each IP address can check 5 menus a minute, and the whole app 200 menus a day (midnight to midnight UTC). Over a limit, the form shows again with a friendly message and your inputs kept. Only valid submissions count. With a database, the counts are kept in its `rate_limits` table, so every copy of the app (Vercel can run several) shares them. Without one, or if the database fails, each copy counts in its own memory, which resets when it restarts.

If something fails (a bad link, a page that can't be read, a Gemini error), the form shows again with a friendly message and your inputs still filled in. When Gemini is overloaded (`503`) or rate-limited (`429`), the app retries with backoff and then tries a fallback model before giving up.

The real error is always logged: in the terminal running `uvicorn` locally, and under **Logs** in the Vercel dashboard. Check there first if the page shows an error.

> Put your key in **`.env`**, not `.env.example`. The app only reads `.env`, and `.env.example` is committed to git.

## Project files

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI routes: form, `/analyze`, `/healthz` |
| `app/menu_reader.py` | Fetches the menu page through Jina Reader |
| `app/analyzer.py` | Gemini prompt, response schema and clean-up |
| `app/rate_limit.py` | Per-IP rate limit and daily cap for `/analyze` |
| `app/db.py` | Optional PostgreSQL connection (SQLAlchemy async engine, asyncpg driver) |
| `app/models.py` | The `analyses` and `rate_limits` tables |
| `app/store.py` | Saves analyses, looks up the 24-hour cache, and keeps the shared rate-limit counts |
| `alembic/`, `alembic.ini` | Database migrations (`alembic upgrade head`) |
| `compose.yaml` | Local PostgreSQL in Docker, with a dev and a test database |
| `app/templates/` | Jinja2 templates: `base.html`, `index.html` (form), `results.html` |
| `app/static/style.css` | Monochrome "Classical" design, including print styles |
| `app/static/loading.js` | Optional loading overlay while the menu is read. The form works without it. |
| `tests/test_app.py` | Route tests with Jina and Gemini faked (no network or key needed) |
| `tests/test_rate_limit.py` | Tests for the rate limit and daily cap |
| `tests/test_database.py` | Saving, caching and shared rate-limit tests against a PostgreSQL test database |
| `.github/workflows/tests.yml` | GitHub Actions: runs `pytest` on every push |
| `vercel.json` | Vercel settings: keeps tests and other non-app files out of the deployed function |
| `neon.ts`, `package.json` | Neon CLI project settings (not part of the web app) |
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

### With a database (optional)

**Option A: a Neon dev branch (no install needed).** With the [Neon CLI](https://neon.com/docs/reference/neon-cli) signed in (`npm i -g neon`, then `neon login`), this creates a `dev` branch of the database and writes its `DATABASE_URL` and `DATABASE_URL_UNPOOLED` into `.env`:

```bash
neon checkout dev --create
alembic upgrade head
```

New branches expire after 7 days (see `neon.ts`). Run the same two commands again to get a fresh one.

**Option B: Docker.** You need [Docker](https://docs.docker.com/get-docker/). This starts PostgreSQL with two databases, `forkcast` for the app and `forkcast_test` for the tests:

```bash
docker compose up -d
```

Add `DATABASE_URL=postgresql://forkcast:forkcast@localhost:5432/forkcast` to `.env`, then create the tables and start the app:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

After changing `app/models.py`, add a migration with `alembic revision --autogenerate -m "what changed"` and check the new file in `alembic/versions/`.

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Your Google Gemini API key |
| `GEMINI_MODEL` | No | The Gemini model to use. Default: `gemini-3.8-flash` |
| `GEMINI_FALLBACK_MODEL` | No | Used when the main model is still overloaded after 3 tries. Default: `gemini-3.6-flash` |
| `JINA_API_KEY` | No | A Jina Reader key. Jina works without one for light use; a key raises the rate limit. |
| `RATE_LIMIT_PER_MINUTE` | No | Menu checks allowed per IP address per minute. Default: `5`. `0` turns it off. |
| `DAILY_ANALYSIS_CAP` | No | Menu checks allowed per day for the whole app. Default: `200`. `0` turns it off. |
| `DATABASE_URL` | No | PostgreSQL connection string. Turns on saving and the 24-hour cache. For Neon, use the pooled string. |
| `DATABASE_URL_UNPOOLED` | No | A direct (non-pooled) connection string. Alembic uses it for migrations when set, as Neon recommends. |
| `TEST_DATABASE_URL` | No | The database the tests use. Default: the `forkcast_test` database from `compose.yaml`. Its name must contain `test`. |

> **Never commit your keys or connection strings.** `.env` is in `.gitignore`. Only `.env.example` (which has no values) belongs in git. On Vercel, set them in the project's environment variables.

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The database tests in `tests/test_database.py` need a test database: the `forkcast_test` database from `docker compose up -d`, or one set in `TEST_DATABASE_URL` (for example a `forkcast_test` database on the Neon `dev` branch: `neon databases create --branch dev --name forkcast_test`, then `neon connection-string dev --database-name forkcast_test --pooled`). Against Neon they take a few minutes. Without one they are skipped, and the other tests still run. They empty the `analyses` table, so they refuse any database whose name doesn't contain `test`.

GitHub Actions runs the same tests on every push and pull request, on Python 3.10 and 3.12, with a PostgreSQL service for the database tests (`.github/workflows/tests.yml`). On CI a missing database fails the run instead of skipping. The badge at the top of this README shows the result for the latest run.

## Deploy to Vercel

The GitHub repository is connected to Vercel, so it deploys on every push:

- a push to `main` updates the **production** site;
- a push to any other branch (or a pull request) gets its own **preview** link, without touching production.

Vercel detects the FastAPI app in `app/main.py` and installs `requirements.txt`, so no build or start command is needed. In the Vercel project's **Settings → Build and Deployment**, the Framework Preset should say **FastAPI**. `vercel.json` keeps the tests and other non-app files out of the deployment.

### Environment variables

Add these in the Vercel project under **Settings → Environment Variables**. Never put them in git.

| Variable | Production | Preview | Notes |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | ✅ Required | ✅ Required | Mark it **Sensitive** |
| `DATABASE_URL` | Neon `production` branch, **pooled** string | Neon `dev` branch, **pooled** string | Optional. Turns on saving and the 24-hour cache. Mark it **Sensitive** |
| `JINA_API_KEY` | Optional | Optional | Raises Jina's rate limit |
| `RATE_LIMIT_PER_MINUTE`, `DAILY_ANALYSIS_CAP` | Optional | Optional | Only to change the defaults (5 and 200) |
| `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL` | Optional | Optional | Only to change the defaults |

Get the Neon pooled strings with `neon connection-string production --pooled` and `neon connection-string dev --pooled`. The app doesn't need `DATABASE_URL_UNPOOLED` or `TEST_DATABASE_URL` on Vercel. After changing a variable, redeploy for it to take effect.

### Database migrations

Vercel doesn't run migrations. Before deploying a change that adds or changes a table, run Alembic against the database yourself, using Neon's direct (non-pooled) string. In PowerShell:

```powershell
$env:DATABASE_URL_UNPOOLED = (neon connection-string production)
alembic upgrade head
Remove-Item Env:DATABASE_URL_UNPOOLED
```

Try it on the `dev` branch first: with `.env` pointing at `dev`, plain `alembic upgrade head` does that.

### Neon branches

| Branch | Used by |
| --- | --- |
| `production` | The live site |
| `dev` | Local development and Vercel previews. Its `forkcast_test` database is for the tests. Expires 7 days after it's created. |

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

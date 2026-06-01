# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (default port 8000)
uvicorn main:app --reload

# Force a cache refresh (bust the 7-day cache)
curl http://localhost:8000/api/refresh

# Check cache age
curl http://localhost:8000/api/health
```

No test suite or linter is configured. Pylance (pyright) is used for static analysis in the IDE — keep it at zero warnings.

## Architecture

Single-file FastAPI backend (`main.py`) + two static HTML pages served from `static/`.

**Data flow:**
1. On first request (or after 7-day TTL), `fetch_pricing()` calls `_fetch_openrouter_all()` — one HTTP request to `https://openrouter.ai/api/v1/models`
2. `_openrouter_rows()` filters the response by provider prefix (`anthropic/`, `openai/`, `google/`) and converts per-token prices → $/MTok
3. Each provider has a `_*_tier()` function that classifies models by name slug into `flagship | standard | lite | reasoning`
4. If OpenRouter is unreachable, `FALLBACK_DATA` (hardcoded in `main.py`) is used per-provider
5. The result is written to `pricing_cache.json` and served from `/api/pricing`

**Cache:** File-based (`pricing_cache.json`), 7-day TTL. `GET /api/refresh` deletes the file and re-fetches immediately. On Render the disk is mounted at `/opt/render/project/src` so the cache persists across deploys.

**Frontend pages:**
- `static/index.html` — main pricing tracker (card grid + sortable table, provider filter buttons, force-refresh button). Reads live data from `/api/pricing` on load.
- `static/summary-slide.html` — cost impact & model selection guide. Static curated table (task → tier → cost saving), plus a live header bar that fetches `/api/pricing` for freshness metadata (updated time, next refresh, model count, source status dot, force-refresh button).

**API routes:**
- `GET /api/pricing` — returns cached/fresh pricing payload
- `GET /api/refresh` — busts cache, returns `{status, model_count, fetched_at_iso}`
- `GET /api/health` — returns `{status, cache_age_hours}`
- `GET /` — serves `static/index.html`
- `GET /static/*` — static file mount

## Deployment

Hosted on Render (free tier). Defined in `render.yaml`. Python 3.11, start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`. The 1 GB persistent disk keeps `pricing_cache.json` alive between deploys.

Live URLs:
- Tracker: `https://llm-pricing-tracker.onrender.com`
- Summary slide: `https://llm-pricing-tracker.onrender.com/static/summary-slide.html`

## Key decisions

- **OpenRouter as sole data source** — all three provider pricing pages are JS-rendered and unscrapeable with plain httpx. OpenRouter's public `/api/v1/models` endpoint returns pricing for all providers in one call with no auth required.
- `beautifulsoup4` and `lxml` remain in `requirements.txt` but are no longer used — safe to remove in a future cleanup.
- Tier classification is done by model ID slug matching, not by API-provided metadata, so new models from OpenRouter may need the tier functions updated if naming conventions change.

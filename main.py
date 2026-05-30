"""
LLM Pricing Tracker — FastAPI backend
Fetches pricing from OpenRouter API for all providers, caches for 7 days,
falls back to hardcoded data if the API is unreachable.
"""

import json
import time
import logging
from pathlib import Path
from datetime import datetime

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LLM Pricing Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── Cache config ───────────────────────────────────────────────────────────────
CACHE_FILE = Path("pricing_cache.json")
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# ── Fallback hardcoded data (updated May 2026) ─────────────────────────────────
FALLBACK_DATA = [
    # Anthropic
    {"provider": "Anthropic", "model": "Claude Opus 4.8",       "tier": "flagship",  "input": 5.00,  "output": 25.00, "note": ""},
    {"provider": "Anthropic", "model": "Claude Opus 4.7",       "tier": "flagship",  "input": 5.00,  "output": 25.00, "note": ""},
    {"provider": "Anthropic", "model": "Claude Opus 4.6",       "tier": "flagship",  "input": 5.00,  "output": 25.00, "note": ""},
    {"provider": "Anthropic", "model": "Claude Sonnet 4.6",     "tier": "standard",  "input": 3.00,  "output": 15.00, "note": ""},
    {"provider": "Anthropic", "model": "Claude Sonnet 4.5",     "tier": "standard",  "input": 3.00,  "output": 15.00, "note": ""},
    {"provider": "Anthropic", "model": "Claude Haiku 4.5",      "tier": "lite",      "input": 1.00,  "output": 5.00,  "note": ""},
    {"provider": "Anthropic", "model": "Claude Haiku 3.5",      "tier": "lite",      "input": 0.80,  "output": 4.00,  "note": "Retired (Bedrock/Vertex only)"},
    {"provider": "Anthropic", "model": "Claude Opus 4.1",       "tier": "flagship",  "input": 15.00, "output": 75.00, "note": "Legacy"},
    # OpenAI
    {"provider": "OpenAI",    "model": "GPT-4.1",               "tier": "standard",  "input": 2.00,  "output": 8.00,  "note": ""},
    {"provider": "OpenAI",    "model": "GPT-4o",                "tier": "standard",  "input": 2.50,  "output": 10.00, "note": ""},
    {"provider": "OpenAI",    "model": "GPT-4o mini",           "tier": "lite",      "input": 0.15,  "output": 0.60,  "note": ""},
    {"provider": "OpenAI",    "model": "o3",                    "tier": "reasoning", "input": 2.00,  "output": 8.00,  "note": "Reasoning tokens billed at output rate"},
    {"provider": "OpenAI",    "model": "o4-mini",               "tier": "reasoning", "input": 1.10,  "output": 4.40,  "note": "Reasoning tokens billed at output rate"},
    # Google
    {"provider": "Google",    "model": "Gemini 3.5 Flash",      "tier": "flagship",  "input": 1.50,  "output": 9.00,  "note": ""},
    {"provider": "Google",    "model": "Gemini 3.1 Pro Preview","tier": "flagship",  "input": 2.00,  "output": 12.00, "note": "≤200k ctx; $4/$18 for >200k"},
    {"provider": "Google",    "model": "Gemini 3.1 Flash-Lite", "tier": "lite",      "input": 0.25,  "output": 1.50,  "note": ""},
    {"provider": "Google",    "model": "Gemini 3 Flash Preview","tier": "standard",  "input": 0.50,  "output": 3.00,  "note": ""},
]

# ── Scraper helpers ────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LLMPricingBot/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}


def _anthropic_tier(model_id: str) -> str:
    slug = model_id.lower()
    if "opus" in slug:
        return "flagship"
    if "sonnet" in slug:
        return "standard"
    return "lite"  # haiku, etc.


def _fetch_openrouter_all() -> list[dict]:
    """Fetch all models from OpenRouter API once; returns raw list or []."""
    url = "https://openrouter.ai/api/v1/models"
    try:
        with httpx.Client(timeout=20, headers=HEADERS, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        logger.warning(f"OpenRouter fetch failed: {e}")
    return []


def _openrouter_rows(all_models: list, prefix: str, provider: str, tier_fn) -> list[dict]:
    """Filter OpenRouter model list by id prefix, convert pricing to $/MTok."""
    rows = []
    for m in all_models:
        model_id = m.get("id", "")
        if not model_id.startswith(prefix):
            continue
        name = m.get("name", model_id.split("/")[-1])
        pricing = m.get("pricing", {})
        try:
            inp_per_token = float(pricing.get("prompt") or 0)
            out_per_token = float(pricing.get("completion") or 0)
        except (ValueError, TypeError):
            continue
        if inp_per_token == 0 and out_per_token == 0:
            continue
        rows.append({
            "provider": provider,
            "model": name,
            "tier": tier_fn(model_id),
            "input": round(inp_per_token * 1_000_000, 4),
            "output": round(out_per_token * 1_000_000, 4),
            "note": "",
        })
    return rows


def _openai_tier(model_id: str) -> str:
    slug = model_id.lower()
    if any(x in slug for x in ("o1", "o3", "o4")):
        return "reasoning"
    if any(x in slug for x in ("mini", "nano", "3.5-turbo")):
        return "lite"
    return "standard"


def _google_tier(model_id: str) -> str:
    slug = model_id.lower()
    if "thinking" in slug:
        return "reasoning"
    if any(x in slug for x in ("lite", "nano")):
        return "lite"
    if any(x in slug for x in ("pro", "ultra")):
        return "flagship"
    return "standard"


# ── Cache logic ────────────────────────────────────────────────────────────────

def load_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        age = time.time() - data.get("fetched_at", 0)
        if age < CACHE_TTL_SECONDS:
            logger.info(f"Cache hit — age {age/3600:.1f}h")
            return data
        logger.info("Cache expired")
    except Exception as e:
        logger.warning(f"Cache read error: {e}")
    return None


def save_cache(payload: dict):
    try:
        CACHE_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        logger.warning(f"Cache write error: {e}")


def fetch_pricing() -> dict:
    cached = load_cache()
    if cached:
        return cached

    logger.info("Fetching fresh pricing data…")
    openrouter_all   = _fetch_openrouter_all()
    anthropic_live   = _openrouter_rows(openrouter_all, "anthropic/", "Anthropic", _anthropic_tier)
    openai_live      = _openrouter_rows(openrouter_all, "openai/",    "OpenAI",    _openai_tier)
    google_live      = _openrouter_rows(openrouter_all, "google/",    "Google",    _google_tier)

    for provider, rows in [("Anthropic", anthropic_live), ("OpenAI", openai_live), ("Google", google_live)]:
        if rows:
            logger.info(f"OpenRouter {provider} fetch: got {len(rows)} models")

    # Merge: prefer live data, fall back per-provider
    anthropic = anthropic_live or [d for d in FALLBACK_DATA if d["provider"] == "Anthropic"]
    openai    = openai_live    or [d for d in FALLBACK_DATA if d["provider"] == "OpenAI"]
    google    = google_live    or [d for d in FALLBACK_DATA if d["provider"] == "Google"]

    models = anthropic + openai + google
    sources_used = {
        "Anthropic": "openrouter.ai/api/v1/models" if anthropic_live else "fallback",
        "OpenAI":    "openrouter.ai/api/v1/models" if openai_live    else "fallback",
        "Google":    "openrouter.ai/api/v1/models" if google_live    else "fallback",
    }

    payload = {
        "models": models,
        "fetched_at": time.time(),
        "fetched_at_iso": datetime.utcnow().isoformat() + "Z",
        "next_refresh_iso": datetime.utcfromtimestamp(time.time() + CACHE_TTL_SECONDS).isoformat() + "Z",
        "sources": sources_used,
    }
    save_cache(payload)
    return payload


# ── API routes ─────────────────────────────────────────────────────────────────

@app.get("/api/pricing")
def get_pricing():
    data = fetch_pricing()
    return JSONResponse(content=data)


@app.get("/api/refresh")
def force_refresh():
    """Force a cache bust and re-fetch immediately."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    data = fetch_pricing()
    return JSONResponse(content={"status": "refreshed", "model_count": len(data["models"]), "fetched_at_iso": data["fetched_at_iso"]})


@app.get("/api/health")
def health():
    cache_age_hours = None
    if CACHE_FILE.exists():
        try:
            d = json.loads(CACHE_FILE.read_text())
            cache_age_hours = round((time.time() - d.get("fetched_at", 0)) / 3600, 1)
        except Exception:
            pass
    return {"status": "ok", "cache_age_hours": cache_age_hours}


# ── Serve frontend ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

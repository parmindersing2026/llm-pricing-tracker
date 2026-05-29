"""
LLM Pricing Tracker — FastAPI backend
Fetches pricing from official provider docs, caches for 7 days,
falls back to hardcoded data if scraping fails.
"""

import json
import os
import time
import logging
from pathlib import Path
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
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


def _parse_price(text: str) -> float | None:
    """Extract a float dollar amount from a string like '$3 / MTok' or '$1.50'."""
    import re
    m = re.search(r"\$\s*([\d,]+\.?\d*)", text.replace(",", ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def scrape_anthropic() -> list[dict]:
    """
    Scrape Anthropic's pricing page.
    The page is server-rendered markdown converted to HTML, so plain httpx works.
    """
    url = "https://platform.claude.com/docs/en/about-claude/pricing"
    try:
        with httpx.Client(timeout=20, headers=HEADERS, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Find the pricing table — rows look like: Model | Base Input | ... | Output
        rows = []
        for table in soup.find_all("table"):
            headers_row = table.find("tr")
            if not headers_row:
                continue
            headers = [th.get_text(strip=True) for th in headers_row.find_all(["th", "td"])]
            if "Model" not in headers and "model" not in [h.lower() for h in headers]:
                continue
            col_model  = next((i for i, h in enumerate(headers) if "model" in h.lower()), 0)
            col_input  = next((i for i, h in enumerate(headers) if "input" in h.lower() and "base" in h.lower()), None)
            col_output = next((i for i, h in enumerate(headers) if "output" in h.lower()), None)
            if col_input is None or col_output is None:
                # fallback: second col = input, last col = output
                col_input  = 1
                col_output = len(headers) - 1
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if len(cells) <= max(col_model, col_input, col_output):
                    continue
                model_name = cells[col_model]
                inp  = _parse_price(cells[col_input])
                out  = _parse_price(cells[col_output])
                if not model_name or inp is None or out is None:
                    continue
                deprecated = "deprecated" in model_name.lower() or "deprecated" in cells[col_input].lower()
                note = "Deprecated" if deprecated else ""
                tier = (
                    "flagship" if "opus" in model_name.lower() else
                    "standard" if "sonnet" in model_name.lower() else
                    "lite"
                )
                rows.append({
                    "provider": "Anthropic",
                    "model": model_name.replace(" (deprecated)", "").strip(),
                    "tier": tier,
                    "input": inp,
                    "output": out,
                    "note": note,
                })
        if rows:
            logger.info(f"Anthropic scraper: got {len(rows)} models")
            return rows
    except Exception as e:
        logger.warning(f"Anthropic scrape failed: {e}")
    return []


def scrape_google() -> list[dict]:
    """
    Scrape Google Gemini API pricing page.
    """
    url = "https://ai.google.dev/gemini-api/docs/pricing"
    try:
        with httpx.Client(timeout=20, headers=HEADERS, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        rows = []
        import re
        # Each model section is an h2; we look for tables following them
        for h2 in soup.find_all("h2"):
            model_name = h2.get_text(strip=True)
            if not model_name or len(model_name) < 4:
                continue
            # Find the first table after this h2
            sibling = h2.find_next_sibling()
            table = None
            while sibling:
                if sibling.name == "table":
                    table = sibling
                    break
                if sibling.name == "h2":
                    break
                sibling = sibling.find_next_sibling()
            if not table:
                continue
            # Look for "Input price" and "Output price" rows
            inp_val = out_val = None
            for tr in table.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                label = cells[0].lower()
                paid_cell = cells[2] if len(cells) > 2 else cells[1]
                if "input price" in label:
                    inp_val = _parse_price(paid_cell)
                elif "output price" in label:
                    out_val = _parse_price(paid_cell)
            if inp_val is None or out_val is None:
                continue
            tier = (
                "lite"     if "lite" in model_name.lower() else
                "flagship" if "pro" in model_name.lower() or "3.5" in model_name else
                "standard"
            )
            rows.append({
                "provider": "Google",
                "model": model_name,
                "tier": tier,
                "input": inp_val,
                "output": out_val,
                "note": "",
            })
        if rows:
            logger.info(f"Google scraper: got {len(rows)} models")
            return rows
    except Exception as e:
        logger.warning(f"Google scrape failed: {e}")
    return []


def scrape_openai() -> list[dict]:
    """
    OpenAI's pricing page is JS-rendered, so we use hardcoded data
    and note the source URL for transparency.
    OpenAI does not publish a parseable static pricing page.
    """
    logger.info("OpenAI: using hardcoded data (JS-rendered page)")
    return [
        {"provider": "OpenAI", "model": "GPT-4.1",     "tier": "standard",  "input": 2.00,  "output": 8.00,  "note": ""},
        {"provider": "OpenAI", "model": "GPT-4o",      "tier": "standard",  "input": 2.50,  "output": 10.00, "note": ""},
        {"provider": "OpenAI", "model": "GPT-4o mini", "tier": "lite",      "input": 0.15,  "output": 0.60,  "note": ""},
        {"provider": "OpenAI", "model": "o3",          "tier": "reasoning", "input": 2.00,  "output": 8.00,  "note": "Reasoning tokens billed at output rate"},
        {"provider": "OpenAI", "model": "o4-mini",     "tier": "reasoning", "input": 1.10,  "output": 4.40,  "note": "Reasoning tokens billed at output rate"},
    ]


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
    anthropic = scrape_anthropic()
    google    = scrape_google()
    openai    = scrape_openai()

    # Merge: prefer scraped, fall back per-provider
    anthropic = anthropic or [d for d in FALLBACK_DATA if d["provider"] == "Anthropic"]
    google    = google    or [d for d in FALLBACK_DATA if d["provider"] == "Google"]
    # openai always uses hardcoded (JS-rendered)

    models = anthropic + openai + google
    sources_used = {
        "Anthropic": "scraped" if anthropic and anthropic != [d for d in FALLBACK_DATA if d["provider"] == "Anthropic"] else "fallback",
        "OpenAI":    "hardcoded (JS page)",
        "Google":    "scraped" if google and google != [d for d in FALLBACK_DATA if d["provider"] == "Google"] else "fallback",
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

# LLM Pricing Tracker

A self-hosted web app that tracks API pricing for Anthropic Claude, OpenAI GPT, and Google Gemini models. Scrapes official provider docs and caches results for 7 days.

## Stack

- **Backend:** Python + FastAPI + BeautifulSoup (scraping)
- **Frontend:** Vanilla HTML/JS with Grid.js
- **Hosting:** Render.com

## Project structure

```
llm-pricing-app/
├── main.py            # FastAPI app + scraper + cache logic
├── requirements.txt
├── render.yaml        # Render deployment config
├── .gitignore
└── static/
    └── index.html     # Frontend dashboard
```

## Deploy to Render in 4 steps

1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   gh repo create llm-pricing-tracker --public --push
   # or: git remote add origin https://github.com/YOUR_USERNAME/llm-pricing-tracker.git && git push -u origin main
   ```

2. **Create a new Web Service on Render**
   - Go to [render.com](https://render.com) → New → Web Service
   - Connect your GitHub repo
   - Render auto-detects `render.yaml` — just click **Deploy**

3. **Add a Persistent Disk** *(so the 7-day cache survives restarts)*
   - In your Render service → Disks → Add Disk
   - Mount path: `/opt/render/project/src`
   - Size: 1 GB (free tier includes 1 GB)
   - *(The render.yaml already configures this)*

4. **Share the URL**
   - Your app will be live at `https://llm-pricing-tracker.onrender.com` (or similar)
   - Share it with your team — no login required

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Web dashboard |
| `GET /api/pricing` | JSON: all models + metadata |
| `GET /api/refresh` | Force cache bust + re-fetch |
| `GET /api/health` | Health check + cache age |

## How auto-refresh works

- On first request, the backend fetches live pricing from official docs
- Results are cached to `pricing_cache.json` for **7 days**
- After 7 days, the next request triggers a fresh scrape automatically
- You can also hit `/api/refresh` (or the "Force refresh" button in the UI) to update immediately

## Data sources

| Provider | Method |
|---|---|
| Anthropic | Scraped from `platform.claude.com/docs/en/about-claude/pricing` |
| Google | Scraped from `ai.google.dev/gemini-api/docs/pricing` |
| OpenAI | Hardcoded (their pricing page is JS-rendered; updated manually) |

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# Open http://localhost:8000
```

# CANSLIM Stock Screener

A Streamlit app implementing the seven **C-A-N-S-L-I-M** criteria from William
O'Neil's *How to Make Money in Stocks*, built entirely on free data sources.

## Features

- **Market Direction (M)** dashboard: SPY/QQQ trend vs 50/200-day lines plus a
  distribution-day count, per the book's market-top rules.
- **Single Stock** analysis: letter-by-letter pass/fail with the actual metric,
  the book's target, a composite 0–100 score, and price/EPS/revenue charts.
- **Screener**: paste a watchlist, get a ranked table with per-letter results,
  downloadable as CSV.
- All thresholds default to the book's numbers and are tunable in the sidebar.

## Data sources (and the Yahoo rate-limit problem)

| Data | Primary | Fallbacks |
|---|---|---|
| Daily prices/volume | **Yahoo Finance**, but *batched*: the whole watchlist is fetched in **one** request, throttled to ~1 req/sec, cached 6 h | Stooq, then **FMP** (optional free API key) |
| Fundamentals (EPS, sales, ROE, ownership) | **Yahoo Finance** — throttled and cached for 24 h | **FMP** (optional free API key) |

Three layers of defence against Yahoo's rate limiting:

1. **Batching** — screening 30 tickers costs one price request, not 30.
2. **Throttling + caching** — requests are spaced ≥1 s apart; re-running the
   app or re-screening the same list costs zero new Yahoo calls (prices cached
   6 h, fundamentals 24 h).
3. **Graceful degradation** — if a source still fails, the affected letter
   shows "no data" (never fake numbers), and you can add a free
   [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs)
   API key in the sidebar (or as `FMP_API_KEY` in Streamlit secrets) as backup
   for both prices and fundamentals.

(Stooq used to be a good keyless price source, but as of 2026 its CSV endpoint
sits behind a JavaScript browser check, so it only works as an opportunistic
fallback.)

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. On [share.streamlit.io](https://share.streamlit.io), create an app pointing
   at `streamlit_app.py`.
3. (Optional) In the app's **Settings → Secrets**, add:
   ```toml
   FMP_API_KEY = "your-free-fmp-key"
   ```

## Files

- `streamlit_app.py` — UI (tabs: Market / Single Stock / Screener / Method)
- `canslim_rules.py` — pure CANSLIM logic, every rule annotated with the book's source
- `data_fetch.py` — data layer: Stooq → Yahoo → FMP chain, throttling, caching

## Disclaimer

Educational tool, not investment advice. Free data can be delayed or
incomplete; the RS rating is an approximation of IBD's proprietary rating.

"""Data layer for the CANSLIM screener.

Free-data strategy (designed around Yahoo Finance rate limits):

  Daily price history   1) yfinance - BATCHED (whole watchlist in one request),
                           throttled (>= 1 call/sec) and cached 6h
                        2) Stooq CSV - opportunistic fallback; Stooq added a
                           JavaScript anti-bot check in 2026 so this often
                           fails, but it is one cheap request to try
                        3) Financial Modeling Prep - optional free API key

  Fundamentals          1) yfinance - throttled + cached 24h
                        2) Financial Modeling Prep - optional free API key, used
                           to fill anything Yahoo could not provide

Every fetch returns partial data instead of raising, so a rate-limited or
missing endpoint degrades a single criterion to "no data" rather than
breaking the whole app.
"""

from __future__ import annotations

import io
import time
import datetime as dt

import pandas as pd
import requests
import streamlit as st

try:
    import yfinance as yf
    YF_OK = True
except Exception:  # pragma: no cover
    YF_OK = False

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CANSLIM-screener/2.0"

# ---------------------------------------------------------------------------
# Throttle: keep Yahoo calls spaced out so we do not trip the rate limiter.
# ---------------------------------------------------------------------------
_MIN_YF_INTERVAL = 1.0  # seconds between Yahoo requests
_last_yf_call = [0.0]


def _yf_throttle() -> None:
    wait = _MIN_YF_INTERVAL - (time.monotonic() - _last_yf_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_yf_call[0] = time.monotonic()


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------
_STOOQ_INDEX_MAP = {
    "^GSPC": "^spx",   # S&P 500
    "^IXIC": "^ndq",   # Nasdaq Composite
    "^DJI": "^dji",    # Dow Jones Industrials
}


def _stooq_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if s in _STOOQ_INDEX_MAP:
        return _STOOQ_INDEX_MAP[s]
    if s.startswith("^"):
        return s.lower()
    # Yahoo class shares use "-" (BRK-B) or "." (BRK.B); Stooq uses "-": brk-b.us
    return s.lower().replace(".", "-") + ".us"


def _yahoo_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    return s if s.startswith("^") else s.replace(".", "-")


def _history_from_stooq(symbol: str, days: int) -> pd.DataFrame | None:
    d2 = dt.date.today()
    d1 = d2 - dt.timedelta(days=days)
    url = (
        "https://stooq.com/q/d/l/"
        f"?s={_stooq_symbol(symbol)}&i=d"
        f"&d1={d1.strftime('%Y%m%d')}&d2={d2.strftime('%Y%m%d')}"
    )
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        if not r.text or r.text.startswith("No data") or "Exceeded" in r.text[:80]:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        if "Close" not in df.columns or len(df) < 30:
            return None
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        if "Volume" not in df.columns:
            df["Volume"] = 0.0
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
        # Reject stale series (delisted tickers etc.)
        if (dt.datetime.now() - df.index[-1].to_pydatetime()).days > 10:
            return None
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception:
        return None


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    try:
        df.index = df.index.tz_localize(None)
    except TypeError:
        pass
    df = df.dropna(subset=["Close"]).sort_index()
    if len(df) < 30:
        return None
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _history_from_yahoo(symbol: str, days: int) -> pd.DataFrame | None:
    if not YF_OK:
        return None
    try:
        _yf_throttle()
        df = yf.Ticker(_yahoo_symbol(symbol)).history(period=f"{days}d",
                                                      auto_adjust=True)
        return _clean_ohlcv(df)
    except Exception:
        return None


def _history_from_fmp(symbol: str, days: int, api_key: str) -> pd.DataFrame | None:
    if not api_key:
        return None
    d2 = dt.date.today()
    d1 = d2 - dt.timedelta(days=days)
    params = {"symbol": symbol, "from": d1.isoformat(), "to": d2.isoformat(),
              "apikey": api_key}
    for url, p in (
        ("https://financialmodelingprep.com/stable/historical-price-eod/full", params),
        (f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}",
         {k: v for k, v in params.items() if k != "symbol"}),
    ):
        try:
            r = requests.get(url, params=p, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data, dict):
                data = data.get("historical")
            if not isinstance(data, list) or not data:
                continue
            df = pd.DataFrame(data)
            if "date" not in df.columns or "close" not in df.columns:
                continue
            df = df.rename(columns={"date": "Date", "open": "Open", "high": "High",
                                    "low": "Low", "close": "Close", "volume": "Volume"})
            df = df.set_index(pd.to_datetime(df["Date"]))
            for col in ("Open", "High", "Low"):
                if col not in df.columns:
                    df[col] = df["Close"]
            return _clean_ohlcv(df)
        except Exception:
            continue
    return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_history(symbol: str, fmp_api_key: str = "", days: int = 460) -> pd.DataFrame | None:
    """Daily OHLCV for one symbol: Yahoo -> Stooq -> FMP. Cached 6h, so each
    symbol costs at most one Yahoo request per session/day."""
    df = _history_from_yahoo(symbol, days)
    if df is None:
        df = _history_from_stooq(symbol, days)
    if df is None and fmp_api_key:
        df = _history_from_fmp(symbol, days, fmp_api_key)
    return df


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_histories(symbols: tuple, fmp_api_key: str = "", days: int = 460) -> dict:
    """Histories for a whole watchlist. The key rate-limit defence: ONE
    batched Yahoo request covers every ticker; only the stragglers fall back
    to the per-symbol chain."""
    syms = [s.strip().upper() for s in symbols if s.strip()]
    out: dict = {}
    if YF_OK and len(syms) > 1:
        try:
            _yf_throttle()
            raw = yf.download([_yahoo_symbol(s) for s in syms], period=f"{days}d",
                              auto_adjust=True, group_by="ticker",
                              threads=False, progress=False)
            if raw is not None and not raw.empty:
                for s in syms:
                    try:
                        sub = (raw[_yahoo_symbol(s)]
                               if isinstance(raw.columns, pd.MultiIndex) else raw)
                        cleaned = _clean_ohlcv(sub)
                        if cleaned is not None:
                            out[s] = cleaned
                    except Exception:
                        pass
        except Exception:
            pass
    for s in syms:
        if s not in out:
            out[s] = get_history(s, fmp_api_key, days)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_market_history() -> dict:
    """Index/ETF history for the M criterion and the RS benchmark. SPY/QQQ
    are used (rather than ^GSPC/^IXIC) for reliable volume data, which the
    distribution-day count requires."""
    data = get_histories(("SPY", "QQQ"))
    return {
        "S&P 500 (SPY)": data.get("SPY"),
        "Nasdaq 100 (QQQ)": data.get("QQQ"),
    }


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------
def _empty_fundamentals(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "eps_quarters": None,        # list of (date_str, eps), newest first
        "eps_growth_q": None,        # % YoY, latest reported quarter
        "eps_growth_q_prev": None,   # % YoY, the quarter before (acceleration)
        "eps_growth_source": None,
        "sales_growth_q": None,      # % YoY quarterly revenue growth
        "eps_annual": None,          # list of (year, eps), oldest first
        "annual_growth_years": None, # "x of y" years with EPS up
        "eps_cagr": None,            # % compounded annual EPS growth
        "roe": None,                 # %
        "inst_pct": None,            # % held by institutions
        "insider_pct": None,
        "shares_out": None,
        "float_shares": None,
        "debt_to_equity": None,      # %
        "market_cap": None,
        "quarterly_chart": None,     # DataFrame: quarter / eps / revenue
        "sources": [],
        "errors": [],
    }


def _yoy_growth(new: float, old: float) -> float | None:
    """YoY growth in %. O'Neil: a comparison against near-zero year-ago EPS
    (e.g. $0.01) is distorted - we cap the displayed value at +-999%."""
    if old is None or new is None or old == 0:
        return None
    g = (new - old) / abs(old) * 100.0
    return max(min(g, 999.0), -999.0)


def _fundamentals_from_yahoo(symbol: str, out: dict) -> None:
    if not YF_OK:
        out["errors"].append("yfinance not installed")
        return
    t = yf.Ticker(_yahoo_symbol(symbol))

    # --- info: one call covers C confirmation, A (ROE), S, I -------------
    try:
        _yf_throttle()
        info = t.get_info()
        if isinstance(info, dict) and info:
            out["name"] = info.get("longName") or info.get("shortName") or symbol
            if info.get("revenueGrowth") is not None:
                out["sales_growth_q"] = float(info["revenueGrowth"]) * 100.0
            if info.get("returnOnEquity") is not None:
                out["roe"] = float(info["returnOnEquity"]) * 100.0
            if info.get("heldPercentInstitutions") is not None:
                out["inst_pct"] = float(info["heldPercentInstitutions"]) * 100.0
            if info.get("heldPercentInsiders") is not None:
                out["insider_pct"] = float(info["heldPercentInsiders"]) * 100.0
            out["shares_out"] = info.get("sharesOutstanding")
            out["float_shares"] = info.get("floatShares")
            out["market_cap"] = info.get("marketCap")
            if info.get("debtToEquity") is not None:
                out["debt_to_equity"] = float(info["debtToEquity"])
            # Fallback for C if reported-EPS history is unavailable
            # (earningsQuarterlyGrowth is net-income based YoY growth).
            if info.get("earningsQuarterlyGrowth") is not None:
                out["_eps_growth_info"] = float(info["earningsQuarterlyGrowth"]) * 100.0
            out["sources"].append("Yahoo info")
    except Exception as e:
        out["errors"].append(f"Yahoo info: {type(e).__name__}")

    # --- reported quarterly EPS (true C criterion: YoY same-quarter) -----
    try:
        _yf_throttle()
        ed = t.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty and "Reported EPS" in ed.columns:
            past = ed[ed["Reported EPS"].notna()].sort_index(ascending=False)
            eps = [(idx.strftime("%Y-%m-%d"), float(v))
                   for idx, v in past["Reported EPS"].items()]
            if len(eps) >= 2:
                out["eps_quarters"] = eps[:9]
            if len(eps) >= 5:
                out["eps_growth_q"] = _yoy_growth(eps[0][1], eps[4][1])
                out["eps_growth_source"] = "reported EPS (Yahoo)"
            if len(eps) >= 6:
                out["eps_growth_q_prev"] = _yoy_growth(eps[1][1], eps[5][1])
            out["sources"].append("Yahoo earnings dates")
    except Exception as e:
        out["errors"].append(f"Yahoo earnings: {type(e).__name__}")

    # --- annual income statement (A criterion) ---------------------------
    try:
        _yf_throttle()
        inc = t.income_stmt
        if inc is not None and not inc.empty:
            row = None
            for label in ("Diluted EPS", "Basic EPS"):
                if label in inc.index:
                    row = inc.loc[label].dropna()
                    break
            if row is not None and len(row) >= 2:
                row = row.sort_index()
                out["eps_annual"] = [(idx.year, float(v)) for idx, v in row.items()]
            out["sources"].append("Yahoo annual income stmt")
    except Exception as e:
        out["errors"].append(f"Yahoo income stmt: {type(e).__name__}")

    # --- quarterly statement (chart + sales-growth fallback) -------------
    try:
        _yf_throttle()
        q = t.quarterly_income_stmt
        if q is not None and not q.empty:
            cols = sorted(q.columns)
            chart = pd.DataFrame({"quarter": [c.strftime("%Y-%m") for c in cols]})
            if "Diluted EPS" in q.index:
                chart["EPS ($)"] = [q.loc["Diluted EPS", c] for c in cols]
            if "Total Revenue" in q.index:
                chart["Revenue ($B)"] = [
                    (q.loc["Total Revenue", c] or 0) / 1e9 for c in cols
                ]
                rev = q.loc["Total Revenue"].dropna().sort_index(ascending=False)
                if out["sales_growth_q"] is None and len(rev) >= 5:
                    out["sales_growth_q"] = _yoy_growth(float(rev.iloc[0]),
                                                        float(rev.iloc[4]))
            if len(chart.columns) > 1:
                out["quarterly_chart"] = chart
            out["sources"].append("Yahoo quarterly income stmt")
    except Exception as e:
        out["errors"].append(f"Yahoo quarterly stmt: {type(e).__name__}")


def _fmp_get(endpoint: str, params: dict, api_key: str):
    """Try FMP's current 'stable' API first, then the legacy v3 API."""
    params = dict(params, apikey=api_key)
    for base in (f"https://financialmodelingprep.com/stable/{endpoint}",
                 f"https://financialmodelingprep.com/api/v3/{endpoint}"):
        try:
            r = requests.get(base, params=params, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data, list) and data:
                return data
        except Exception:
            continue
    return None


def _fundamentals_from_fmp(symbol: str, out: dict, api_key: str) -> None:
    """Fill whatever Yahoo could not provide, using an FMP free-tier key."""
    need_quarterly = out["eps_growth_q"] is None or out["sales_growth_q"] is None
    if need_quarterly:
        data = _fmp_get("income-statement",
                        {"symbol": symbol, "period": "quarter", "limit": 12},
                        api_key)
        if data:
            eps = [(d.get("date", "?"), d.get("epsDiluted") or d.get("epsdiluted")
                    or d.get("eps")) for d in data]
            eps = [(d, float(v)) for d, v in eps if v is not None]
            if out["eps_growth_q"] is None and len(eps) >= 5:
                out["eps_quarters"] = out["eps_quarters"] or eps[:9]
                out["eps_growth_q"] = _yoy_growth(eps[0][1], eps[4][1])
                out["eps_growth_source"] = "diluted EPS (FMP)"
                if len(eps) >= 6:
                    out["eps_growth_q_prev"] = _yoy_growth(eps[1][1], eps[5][1])
            rev = [(d.get("date"), d.get("revenue")) for d in data]
            rev = [(d, float(v)) for d, v in rev if v]
            if out["sales_growth_q"] is None and len(rev) >= 5:
                out["sales_growth_q"] = _yoy_growth(rev[0][1], rev[4][1])
            out["sources"].append("FMP quarterly")

    if out["eps_annual"] is None:
        data = _fmp_get("income-statement",
                        {"symbol": symbol, "period": "annual", "limit": 6},
                        api_key)
        if data:
            eps = [(d.get("calendarYear") or (d.get("date") or "?")[:4],
                    d.get("epsDiluted") or d.get("epsdiluted") or d.get("eps"))
                   for d in data]
            eps = [(int(y), float(v)) for y, v in eps if v is not None]
            if len(eps) >= 2:
                out["eps_annual"] = sorted(eps)
            out["sources"].append("FMP annual")

    if out["roe"] is None:
        data = _fmp_get("ratios", {"symbol": symbol, "limit": 1}, api_key)
        if data:
            roe = data[0].get("returnOnEquity")
            if roe is not None:
                out["roe"] = float(roe) * 100.0
                out["sources"].append("FMP ratios")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_fundamentals(symbol: str, fmp_api_key: str = "") -> dict:
    """All fundamental inputs for the C / A / S / I criteria.

    Cached for 24h: fundamentals only change once per quarter, and the cache
    is the main defence against Yahoo's rate limiting."""
    out = _empty_fundamentals(symbol)
    _fundamentals_from_yahoo(symbol, out)
    if fmp_api_key:
        try:
            _fundamentals_from_fmp(symbol, out, fmp_api_key.strip())
        except Exception as e:
            out["errors"].append(f"FMP: {type(e).__name__}")

    # Last-resort C fallback: Yahoo's net-income-based quarterly growth
    if out["eps_growth_q"] is None and out.get("_eps_growth_info") is not None:
        out["eps_growth_q"] = max(min(out["_eps_growth_info"], 999.0), -999.0)
        out["eps_growth_source"] = "net income YoY (Yahoo info)"
    out.pop("_eps_growth_info", None)

    # Derive annual-EPS stats here so the rules module stays data-free
    if out["eps_annual"] and len(out["eps_annual"]) >= 2:
        eps = [v for _, v in out["eps_annual"]]
        ups = sum(1 for a, b in zip(eps, eps[1:]) if b > a)
        out["annual_growth_years"] = (ups, len(eps) - 1)
        first, last, span = eps[0], eps[-1], len(eps) - 1
        if first > 0 and last > 0 and span > 0:
            out["eps_cagr"] = ((last / first) ** (1.0 / span) - 1.0) * 100.0
    return out

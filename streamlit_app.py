"""CANSLIM Stock Screener - Streamlit app.

Implements the seven criteria from William O'Neil's "How to Make Money in
Stocks" using free data sources (Stooq for prices, Yahoo Finance for
fundamentals with throttling/caching, optional Financial Modeling Prep key
as backup). See canslim_rules.py for the rule definitions and data_fetch.py
for the data layer.
"""

import time

import pandas as pd
import streamlit as st

from canslim_rules import (DEFAULTS, combine_market, evaluate_stock,
                           market_health, verdict)
from data_fetch import (get_fundamentals, get_histories, get_history,
                        get_market_history)
from report import build_screener_report, build_stock_report

st.set_page_config(page_title="CANSLIM Screener", page_icon="📈", layout="wide")

PASS, FAIL, NA = "✅", "❌", "➖"
DEFAULT_WATCHLIST = "NVDA, MSFT, AAPL, META, AMZN, GOOGL, AVGO, LLY, COST, NFLX, CRWD, PLTR"


def icon(flag):
    return {True: PASS, False: FAIL}.get(flag, NA)


# ---------------------------------------------------------------------------
# Sidebar: thresholds (book defaults) + data settings
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Settings")

with st.sidebar.expander("Rule thresholds (book defaults)", expanded=False):
    th = dict(DEFAULTS)
    th["eps_q_min"] = st.slider("C: min quarterly EPS growth YoY %", 10, 100,
                                int(DEFAULTS["eps_q_min"]), 5,
                                help="O'Neil: at least 18-20%; 25%+ preferred, the big winners showed 40-500%.")
    th["sales_q_min"] = st.slider("C: min quarterly sales growth YoY %", 5, 100,
                                  int(DEFAULTS["sales_q_min"]), 5)
    th["eps_cagr_min"] = st.slider("A: min annual EPS growth %", 10, 100,
                                   int(DEFAULTS["eps_cagr_min"]), 5,
                                   help="O'Neil: 25-50% compounded annual growth over the last 4-5 years.")
    th["roe_min"] = st.slider("A: min return on equity %", 5, 40,
                              int(DEFAULTS["roe_min"]), 1)
    th["off_high_max"] = st.slider("N: max % below 52-week high", 5, 30,
                                   int(DEFAULTS["off_high_max"]), 1,
                                   help="Buy near new highs, not bargains. Book: don't chase >5-10% past the buy point either.")
    th["ud_vol_min"] = st.slider("S: min up/down volume ratio", 0.5, 2.0,
                                 float(DEFAULTS["ud_vol_min"]), 0.1)
    th["rs_min"] = st.slider("L: min RS rating", 50, 99, int(DEFAULTS["rs_min"]), 1,
                             help="O'Neil avoids stocks below 80; the 500 biggest winners averaged 87.")
    th["inst_min"] = st.slider("I: min institutional ownership %", 0, 50,
                               int(DEFAULTS["inst_min"]), 5)
    th["min_price"] = st.slider("Min share price $", 1, 50, int(DEFAULTS["min_price"]), 1)

with st.sidebar.expander("Data sources", expanded=False):
    st.markdown(
        "- **Prices:** Yahoo Finance — one *batched* request per watchlist, "
        "throttled and cached 6h (Stooq/FMP fallback)\n"
        "- **Fundamentals:** Yahoo Finance (throttled + cached 24h)\n"
        "- **Optional backup:** free [FMP](https://site.financialmodelingprep.com/developer/docs) "
        "API key, used for whatever Yahoo couldn't provide"
    )
    try:
        _secret_key = st.secrets.get("FMP_API_KEY", "")
    except Exception:  # no secrets.toml configured
        _secret_key = ""
    fmp_key = st.text_input("FMP API key (optional)", type="password",
                            value=_secret_key)
    if st.button("Clear data cache"):
        st.cache_data.clear()
        st.success("Cache cleared.")

st.sidebar.caption(
    "Educational tool, not investment advice. Free data can be delayed or "
    "incomplete; the RS rating is an approximation of IBD's (see L tab)."
)

# ---------------------------------------------------------------------------
# Header + market direction (M) - the book's gate for everything else
# ---------------------------------------------------------------------------
st.title("📈 CANSLIM Stock Screener")
st.caption("Based on William O'Neil's *How to Make Money in Stocks* - "
           "C-A-N-S-L-I-M criteria computed from free market data.")

tab_market, tab_stock, tab_screen, tab_help = st.tabs(
    ["🌐 Market Direction (M)", "🔍 Single Stock", "📋 Screener", "📖 Method"])

market_data = get_market_history()
healths = {name: market_health(df) for name, df in market_data.items() if df is not None}
overall_m = combine_market(list(healths.values()))

with tab_market:
    st.subheader("M = Market Direction")
    st.markdown(
        "> *\"You can be right on every one of the factors in the first six chapters; "
        "however, if you are wrong about the direction of the broad market, three out "
        "of four of your stocks will slump with the market averages.\"*")
    if not healths:
        st.error("Could not load index data right now - try again in a minute.")
    else:
        st.markdown(f"### Overall: :{overall_m['color']}[{overall_m['status']}]")
        cols = st.columns(len(healths))
        for col, (name, h) in zip(cols, healths.items()):
            with col:
                st.markdown(f"#### {name}")
                st.markdown(f"**:{h['color']}[{h['status']}]**")
                st.metric("Last close", f"{h['last']:,.2f}",
                          f"{-h['pct_off_high']:.1f}% vs 52w high")
                rows = [
                    ("Above 50-day line", icon(h["above50"])),
                    ("Above 200-day line", icon(h["above200"])),
                    ("50-day above 200-day", icon(h["golden_cross"])),
                    (f"Distribution days (25 sessions)",
                     f"{h['dist_days']} {'⚠️' if h['dist_days'] >= 5 else ''}"),
                ]
                st.table(pd.DataFrame(rows, columns=["Check", "Status"]).set_index("Check"))
        with st.expander("How to read this (from the book)"):
            st.markdown(
                "- **Distribution days** - index closes down ≥0.2% on volume higher than the "
                "day before. Five or six within a few weeks have historically marked tops.\n"
                "- **Follow-through day** - after a decline, a strong gain (≥1%) on rising "
                "volume on day 4-10 of a rally attempt confirms a new uptrend. Check the "
                "index chart manually for this signal.\n"
                "- O'Neil's rule: only make new buys in a confirmed uptrend; raise cash "
                "when distribution piles up.")
        for name, df in market_data.items():
            if df is None:
                continue
            chart = df[["Close"]].iloc[-252:].copy()
            chart["50-day avg"] = df["Close"].rolling(50).mean().iloc[-252:]
            chart["200-day avg"] = df["Close"].rolling(200).mean().iloc[-252:]
            st.markdown(f"**{name}** - last 12 months")
            st.line_chart(chart, height=220)

bench_df = market_data.get("S&P 500 (SPY)")


# ---------------------------------------------------------------------------
# Helpers shared by both analysis tabs
# ---------------------------------------------------------------------------
def analyze(symbol: str, hist=None):
    if hist is None:
        hist = get_history(symbol, fmp_key)
    if hist is None or len(hist) < 60:
        return None, None, "No price history found (check the ticker symbol)."
    fund = get_fundamentals(symbol, fmp_key)
    result = evaluate_stock(fund, hist, bench_df, th)
    return fund, result, None


def rows_df(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [(r[0], r[1], r[2], icon(r[3]), r[4] if len(r) > 4 else "") for r in rows],
        columns=["Metric", "Value", "Target", "", "Source & data date"])


def letter_panel(letter: dict):
    head = f"{icon(letter['passed'])} **{letter['key']} = {letter['title']}**"
    score = f"{letter['score']}/100" if letter["score"] is not None else "no data"
    with st.expander(f"{head} — {score}", expanded=False):
        st.write(letter["summary"])
        if letter["rows"]:
            st.table(rows_df(letter["rows"]).set_index("Metric"))


# ---------------------------------------------------------------------------
# Single stock tab
# ---------------------------------------------------------------------------
with tab_stock:
    c1, c2 = st.columns([1, 3])
    with c1:
        symbol = st.text_input("Ticker", value="NVDA").strip().upper()
        go = st.button("Analyze", type="primary", use_container_width=True)
    if symbol and (go or st.session_state.get("last_symbol") == symbol):
        st.session_state["last_symbol"] = symbol
        with st.spinner(f"Fetching data for {symbol}…"):
            fund, result, err = analyze(symbol)
        if err:
            st.error(err)
        else:
            p = result["price"]
            st.header(f"{fund['name']} ({symbol})")

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("CANSLIM score", result["composite"] if result["composite"] is not None else "n/a")
            m2.metric("Letters passed", f"{result['passed_count']} / {result['scored_count']} scored")
            m3.metric("Last close", f"${p['last_close']:,.2f}",
                      f"{-p['pct_off_high']:.1f}% vs 52w high")
            m4.metric("RS rating (approx.)", result["rs_rating"] or "n/a")
            m5.metric("Market (M)", overall_m["status"])

            v = verdict(result["composite"], result["passed_count"],
                        result["scored_count"], overall_m["passed"])
            result["verdict"] = v
            (st.success if result["composite"] and result["composite"] >= 65 else st.warning)(f"**Verdict:** {v}")

            st.download_button(
                "⬇️ Download full report (HTML, with all data sources & dates)",
                build_stock_report(symbol, fund, result, overall_m["status"], th),
                file_name=f"canslim_{symbol}_{pd.Timestamp.now():%Y-%m-%d}.html",
                mime="text/html")

            if not result["liquidity"]["ok"]:
                st.warning("⚠️ Fails O'Neil's liquidity sanity checks (price/volume too low) — "
                           "thin, cheap stocks are explicitly discouraged in the book.")
            if result["data_completeness"] < 1:
                missing = [l["key"] for l in result["letters"] if l["score"] is None]
                st.info(f"Partial data: no inputs found for {', '.join(missing)}. "
                        "This is usually Yahoo rate-limiting — retry in a few minutes "
                        "or add a free FMP key in the sidebar.")

            left, right = st.columns([1, 1])
            with left:
                st.subheader("Letter by letter")
                for letter in result["letters"]:
                    letter_panel(letter)
                with st.expander(f"{icon(result['liquidity']['ok'])} Liquidity sanity checks"):
                    st.table(rows_df(result["liquidity"]["rows"]).set_index("Metric"))
                with st.expander("🔬 Raw data used (verify manually)"):
                    st.caption(f"Fundamentals fetched {fund['fetched_at']} · "
                               f"Prices: {result['price']['source']}")
                    if fund["eps_quarters"]:
                        st.markdown("**Quarterly EPS series** — "
                                    + fund.get("prov", {}).get("eps_q", ""))
                        st.table(pd.DataFrame(fund["eps_quarters"],
                                              columns=["Quarter / report date", "EPS ($)"]))
                    if fund["eps_annual"]:
                        st.markdown("**Annual EPS series** — "
                                    + fund.get("prov", {}).get("eps_annual", ""))
                        st.table(pd.DataFrame(fund["eps_annual"],
                                              columns=["Fiscal year", "EPS ($)"]))
                    if fund["sources"]:
                        st.caption("Endpoints queried: " + ", ".join(fund["sources"]))
                    if fund["errors"]:
                        st.caption("Endpoints that failed: " + "; ".join(fund["errors"]))

            with right:
                st.subheader("Price vs moving averages (12 months)")
                hist = get_history(symbol, fmp_key)
                chart = hist[["Close"]].iloc[-252:].copy()
                chart["50-day avg"] = hist["Close"].rolling(50).mean().iloc[-252:]
                chart["200-day avg"] = hist["Close"].rolling(200).mean().iloc[-252:]
                st.line_chart(chart, height=260)
                st.caption("Volume (shares/day)")
                st.bar_chart(hist["Volume"].iloc[-120:], height=120)

                if fund["quarterly_chart"] is not None:
                    st.subheader("Recent quarters")
                    qc = fund["quarterly_chart"].set_index("quarter")
                    if "EPS ($)" in qc.columns:
                        st.bar_chart(qc[["EPS ($)"]], height=160)
                    if "Revenue ($B)" in qc.columns:
                        st.bar_chart(qc[["Revenue ($B)"]], height=160)
                elif fund["eps_quarters"]:
                    st.subheader("Reported quarterly EPS")
                    eq = pd.DataFrame(reversed(fund["eps_quarters"]),
                                      columns=["quarter", "EPS ($)"]).set_index("quarter")
                    st.bar_chart(eq, height=160)


# ---------------------------------------------------------------------------
# Screener tab
# ---------------------------------------------------------------------------
with tab_screen:
    st.subheader("Screen a watchlist")
    st.caption("Paste tickers separated by commas, spaces or new lines. "
               "Fundamentals are throttled to about one ticker per few seconds "
               "to stay under Yahoo's rate limits — keep lists to ~30 tickers.")
    raw = st.text_area("Watchlist", value=DEFAULT_WATCHLIST, height=90)
    run = st.button("Run screener", type="primary")

    if run:
        symbols = sorted({s.strip().upper() for s in raw.replace(",", " ").split() if s.strip()})
        if not symbols:
            st.warning("No tickers entered.")
        else:
            rows, errors = [], []
            prog = st.progress(0.0, text="Fetching price history (one batched request)…")
            hists = get_histories(tuple(symbols), fmp_key)
            for i, sym in enumerate(symbols):
                prog.progress((i + 1) / len(symbols), text=f"Analyzing {sym} ({i + 1}/{len(symbols)})")
                try:
                    fund, result, err = analyze(sym, hists.get(sym))
                    if err:
                        errors.append(f"{sym}: {err}")
                        continue
                    lt = {l["key"]: l for l in result["letters"]}
                    p = result["price"]
                    rows.append({
                        "Ticker": sym,
                        "Name": (fund["name"] or sym)[:28],
                        "Score": result["composite"],
                        "Passed": f"{result['passed_count']}/{result['scored_count']}",
                        "C": icon(lt["C"]["passed"]), "A": icon(lt["A"]["passed"]),
                        "N": icon(lt["N"]["passed"]), "S": icon(lt["S"]["passed"]),
                        "L": icon(lt["L"]["passed"]), "I": icon(lt["I"]["passed"]),
                        "EPS Q %": round(fund["eps_growth_q"]) if fund["eps_growth_q"] is not None else None,
                        "EPS CAGR %": round(fund["eps_cagr"]) if fund["eps_cagr"] is not None else None,
                        "RS": result["rs_rating"],
                        "% off high": round(p["pct_off_high"], 1),
                        "Price": round(p["last_close"], 2),
                    })
                except Exception as e:
                    errors.append(f"{sym}: {type(e).__name__}")
                time.sleep(0.3)  # spacing between tickers, on top of per-call throttle
            prog.empty()

            if rows:
                df = pd.DataFrame(rows).sort_values("Score", ascending=False, na_position="last")
                st.dataframe(
                    df, use_container_width=True, hide_index=True,
                    column_config={
                        "Score": st.column_config.ProgressColumn(
                            "Score", min_value=0, max_value=100, format="%d"),
                    })
                dl1, dl2 = st.columns(2)
                dl1.download_button(
                    "Download results (CSV)",
                    df.to_csv(index=False).encode(),
                    file_name="canslim_screen.csv", mime="text/csv")
                dl2.download_button(
                    "Download results (HTML)",
                    build_screener_report(df, overall_m["status"], th),
                    file_name=f"canslim_screen_{pd.Timestamp.now():%Y-%m-%d}.html",
                    mime="text/html")
                if overall_m["passed"] is False:
                    st.warning(f"Market check (M): **{overall_m['status']}** — O'Neil would "
                               "hold off on new buys until a confirmed uptrend.")
            if errors:
                st.info("Skipped / partial: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Method tab
# ---------------------------------------------------------------------------
with tab_help:
    st.subheader("What each letter means (and how it's computed here)")
    st.markdown(f"""
| Letter | Book rule | Implementation |
|---|---|---|
| **C** | Quarterly EPS up **≥18–20%** YoY (25%+ preferred; winners averaged +70%), accelerating, confirmed by sales. Avoid comparisons vs. near-zero year-ago EPS. | **GAAP diluted EPS from the quarterly income statement**, date-matched to the same quarter one year earlier (verifiable against the 10-Q). Fallbacks, in order: FMP statements, Yahoo earnings-calendar street EPS (labelled — can mix GAAP/non-GAAP), net-income growth (labelled). Every row shows its source and data date. |
| **A** | Annual EPS up **each of the last ~5 years** (one dip allowed), **25–50%** compounded growth. | Annual diluted EPS (up to 4–6 yrs of free data): years-up count, CAGR, plus ROE ≥ {int(DEFAULTS['roe_min'])}% as a quality check. |
| **N** | Something new (product, management, industry) **and a price near new highs** emerging from a base. | % below 52-week high (default ≤ {int(DEFAULTS['off_high_max'])}%). The "new catalyst" part can't be screened — verify it yourself. |
| **S** | Small or reasonable share count, low debt, **volume demand on rallies**. | Float size, debt/equity, 50-day up/down volume ratio ≥ {DEFAULTS['ud_vol_min']:.1f}. |
| **L** | Buy the **leader** of a strong group: RS rating **≥ 80** (winners averaged 87). | Approximate RS: weighted 12-month return (recent quarter ×2, IBD-style) vs. the S&P 500, mapped to 1–99. Not identical to IBD's universe percentile. |
| **I** | **A few quality institutional sponsors**, increasing — but not "overowned". | Institutional ownership % within a sane band ({int(DEFAULTS['inst_min'])}–{int(DEFAULTS['inst_max'])}%). |
| **M** | **3 of 4 stocks follow the market.** Watch distribution days at tops, follow-through days at bottoms. | SPY/QQQ vs. 50/200-day lines + distribution-day count; shown as an overall gate, not per stock. |

**Score**: weighted average of available letters (C 25, A 20, L 20, N 15, S 10, I 10).
Letters with no data are excluded and flagged rather than guessed.

**What this tool deliberately does *not* do**: chart-pattern detection (cup-with-handle
bases, pivot/buy points), sell rules, and position sizing are core to the book but need
human judgment on charts — use the price charts here plus your own review.
""")
    st.caption("Free-data caveats: Stooq/Yahoo prices can lag a day; Yahoo fundamentals "
               "are sometimes missing for smaller stocks; the RS rating is an approximation.")

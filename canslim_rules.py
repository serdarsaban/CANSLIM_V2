"""CANSLIM evaluation rules.

Every rule, threshold and default in this module is taken from William
O'Neil, "How to Make Money in Stocks" (Part 1, chapters C through M):

  C  Current quarterly EPS up at least 18-20% YoY (25%+ preferred, 40-500%
     in the best winners), accelerating, and confirmed by sales growth.
  A  Annual EPS up in (nearly) every one of the last 3-5 years with a
     25-50% compounded growth rate; strong ROE separates quality growth.
  N  New highs: the best buy candidates are within striking distance of a
     new 52-week high, not bargain-bin stocks far below their peaks.
  S  Supply & demand: small/reasonable share count, low debt-to-equity,
     and volume flowing in on up days (up/down volume ratio > 1).
  L  Leader, not laggard: Relative Strength rating of 80+ (the 500 biggest
     winners averaged 87 before their major advances).
  I  Institutional sponsorship: at least a few institutional owners, but
     not "overowned".
  M  Market direction: 3 out of 4 stocks follow the general market, so
     only buy in confirmed uptrends; tops show "distribution days"
     (higher volume, no price progress).

This module is pure computation - no network, no streamlit - so it can be
unit-tested and reused.
"""

from __future__ import annotations

import math

import pandas as pd

# Default thresholds (the book's numbers; tunable in the UI)
DEFAULTS = {
    "eps_q_min": 25.0,        # C: min YoY quarterly EPS growth %
    "sales_q_min": 25.0,      # C: min YoY quarterly sales growth %
    "eps_cagr_min": 25.0,     # A: min compounded annual EPS growth %
    "roe_min": 17.0,          # A: min return on equity %
    "off_high_max": 15.0,     # N: max % below 52-week high
    "ud_vol_min": 1.0,        # S: min 50-day up/down volume ratio
    "rs_min": 80.0,           # L: min relative strength rating
    "inst_min": 10.0,         # I: min institutional ownership %
    "inst_max": 95.0,         # I: above this a stock is "overowned"
    "min_price": 10.0,        # liquidity sanity check (book ch. 14: >$20 NYSE)
    "min_avg_vol": 200_000,   # liquidity sanity check, 50-day avg shares/day
}

# Composite weights: the book calls earnings (C, A) "the most critical
# fundamental factor" and leadership (L) the key dividing line, so they
# carry the most weight. M is a market-level gate, reported separately.
WEIGHTS = {"C": 25, "A": 20, "N": 15, "S": 10, "L": 20, "I": 10}


def _fmt(v, suffix="", decimals=1):
    if v is None:
        return "n/a"
    return f"{v:,.{decimals}f}{suffix}"


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# Price-derived metrics (N, S, L inputs)
# ---------------------------------------------------------------------------
def price_metrics(df: pd.DataFrame) -> dict:
    close, vol = df["Close"], df["Volume"]
    out = {"last_close": float(close.iloc[-1])}

    year = df.iloc[-252:] if len(df) >= 252 else df
    out["high_52w"] = float(year["High"].max())
    out["low_52w"] = float(year["Low"].min())
    out["pct_off_high"] = (out["high_52w"] - out["last_close"]) / out["high_52w"] * 100.0
    out["pct_above_low"] = (out["last_close"] / out["low_52w"] - 1.0) * 100.0 if out["low_52w"] else None

    for n in (50, 150, 200):
        out[f"sma{n}"] = float(close.rolling(n).mean().iloc[-1]) if len(close) >= n else None

    out["avg_vol_50"] = float(vol.iloc[-50:].mean()) if len(vol) >= 10 else None

    # Up/down volume ratio over the last 50 sessions (S criterion):
    # volume on up days vs volume on down days - demand vs supply.
    recent = df.iloc[-51:]
    chg = recent["Close"].diff().iloc[1:]
    v = recent["Volume"].iloc[1:]
    up, down = float(v[chg > 0].sum()), float(v[chg < 0].sum())
    out["ud_vol_ratio"] = (up / down) if down > 0 else None

    # IBD-style weighted 12-month performance: the most recent quarter
    # counts double (40% of the total weight).
    out["rs_raw"] = _rs_raw(close)
    return out


def _rs_raw(close: pd.Series) -> float | None:
    if len(close) < 70:
        return None
    last = float(close.iloc[-1])

    def ratio(n):
        return last / float(close.iloc[-1 - n]) if len(close) > n else None

    r63, r126, r189, r252 = ratio(63), ratio(126), ratio(189), ratio(252)
    parts = [2 * r63, r126 or r63, r189 or r126 or r63, r252 or r189 or r126 or r63]
    if r63 is None:
        return None
    return float(sum(parts))


def rs_rating(stock_rs_raw: float | None, bench_rs_raw: float | None) -> int | None:
    """Approximate 1-99 RS rating.

    The true IBD rating is a percentile over ~7000 stocks, which is not
    feasible with free data. Instead the stock's weighted 12-month
    performance is compared with the S&P 500's and mapped onto 1-99 so
    that matching the index = 50 and beating it by ~50% (weighted) ~= 90.
    """
    if not stock_rs_raw or not bench_rs_raw:
        return None
    excess = stock_rs_raw / bench_rs_raw - 1.0
    return int(round(_clamp(50 + 49 * math.tanh(excess * 2.5), 1, 99)))


def rs_line_near_high(stock_close: pd.Series, bench_close: pd.Series) -> bool | None:
    """True if the RS line (stock / benchmark) is within 3% of its 52-week
    high - the book's hallmark of a genuine leader."""
    try:
        joined = pd.concat([stock_close, bench_close], axis=1, join="inner").dropna()
        if len(joined) < 60:
            return None
        line = joined.iloc[:, 0] / joined.iloc[:, 1]
        line = line.iloc[-252:]
        return float(line.iloc[-1]) >= float(line.max()) * 0.97
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Letter evaluations
# ---------------------------------------------------------------------------
def _letter(key, title, score, passed, rows, summary):
    return {"key": key, "title": title, "score": score, "passed": passed,
            "rows": rows, "summary": summary}


def eval_C(f: dict, th: dict) -> dict:
    g, gp, s = f["eps_growth_q"], f["eps_growth_q_prev"], f["sales_growth_q"]
    if g is None:
        return _letter("C", "Current Quarterly Earnings", None, None, [],
                       "No quarterly earnings data available from any source.")
    accel = (gp is not None and g > gp) or None if gp is not None else None
    rows = [
        (f"Quarterly EPS growth YoY ({f['eps_growth_source']})",
         _fmt(g, "%"), f">= {th['eps_q_min']:.0f}%", g >= th["eps_q_min"]),
        ("Prior quarter EPS growth YoY", _fmt(gp, "%"),
         "accelerating", accel),
        ("Quarterly sales growth YoY", _fmt(s, "%"),
         f">= {th['sales_q_min']:.0f}%", (s >= th["sales_q_min"]) if s is not None else None),
    ]
    score = _clamp(g / th["eps_q_min"], 0, 1) * 70 if g < th["eps_q_min"] else 70
    score += 15 if accel else 0
    score += 15 if (s is not None and s >= th["sales_q_min"]) else 0
    passed = g >= th["eps_q_min"]
    summary = (f"EPS {_fmt(g, '%')} YoY"
               + (", accelerating" if accel else (", decelerating" if accel is False else ""))
               + (f", sales {_fmt(s, '%')}" if s is not None else ""))
    return _letter("C", "Current Quarterly Earnings", round(score), passed, rows, summary)


def eval_A(f: dict, th: dict) -> dict:
    cagr, roe, yrs = f["eps_cagr"], f["roe"], f["annual_growth_years"]
    if cagr is None and yrs is None and roe is None:
        return _letter("A", "Annual Earnings Increases", None, None, [],
                       "No annual earnings data available from any source.")
    eps_str = " -> ".join(f"{y}: ${v:,.2f}" for y, v in (f["eps_annual"] or []))
    rows = [
        ("Annual EPS history", eps_str or "n/a", "each year up", None),
        ("Years of EPS growth",
         f"{yrs[0]} of {yrs[1]}" if yrs else "n/a", "all (one dip ok)",
         (yrs[0] >= yrs[1] - (1 if yrs[1] >= 3 else 0)) if yrs else None),
        ("EPS compounded growth", _fmt(cagr, "%"),
         f">= {th['eps_cagr_min']:.0f}%", (cagr >= th["eps_cagr_min"]) if cagr is not None else None),
        ("Return on equity", _fmt(roe, "%"),
         f">= {th['roe_min']:.0f}%", (roe >= th["roe_min"]) if roe is not None else None),
    ]
    score = 0.0
    if yrs and yrs[1] > 0:
        score += 40 * yrs[0] / yrs[1]
    if cagr is not None:
        score += _clamp(cagr / th["eps_cagr_min"], 0, 1) * 40
    if roe is not None:
        score += _clamp(roe / th["roe_min"], 0, 1) * 20
    elif cagr is not None:  # renormalise when ROE is missing
        score *= 100 / 80
    passed = (cagr is not None and cagr >= th["eps_cagr_min"]
              and (roe is None or roe >= th["roe_min"]))
    summary = f"EPS CAGR {_fmt(cagr, '%')}" + (f", ROE {_fmt(roe, '%')}" if roe is not None else "")
    return _letter("A", "Annual Earnings Increases", round(_clamp(score)), passed, rows, summary)


def eval_N(p: dict, th: dict) -> dict:
    off = p["pct_off_high"]
    above_low = p["pct_above_low"]
    rows = [
        ("% below 52-week high", _fmt(off, "%"),
         f"<= {th['off_high_max']:.0f}%", off <= th["off_high_max"]),
        ("% above 52-week low", _fmt(above_low, "%"), ">= 30% (context)",
         (above_low >= 30) if above_low is not None else None),
        ("52-week high / last close",
         f"${p['high_52w']:,.2f} / ${p['last_close']:,.2f}", "near new highs", None),
    ]
    score = _clamp(100 - off * (70 / max(th["off_high_max"], 1)) * 0.5 - max(off - th["off_high_max"], 0) * 2)
    passed = off <= th["off_high_max"]
    note = ("'New products / new management' cannot be screened numerically - "
            "verify the catalyst yourself before buying.")
    return _letter("N", "New Highs (and Something New)", round(score), passed, rows,
                   f"{_fmt(off, '%')} below 52-week high. {note}")


def eval_S(f: dict, p: dict, th: dict) -> dict:
    shares = f["float_shares"] or f["shares_out"]
    ud = p["ud_vol_ratio"]
    de = f["debt_to_equity"]
    rows = []
    score, weight = 0.0, 0.0

    if shares:
        m = shares / 1e6
        rows.append(("Float / shares outstanding", f"{m:,.0f}M",
                     "smaller is better", m <= 1000))
        size_score = 100 if m <= 50 else 85 if m <= 200 else 65 if m <= 1000 else 40 if m <= 5000 else 25
        score += size_score * 0.4
        weight += 0.4
    else:
        rows.append(("Float / shares outstanding", "n/a", "smaller is better", None))

    if ud is not None:
        rows.append(("Up/down volume ratio (50d)", _fmt(ud, "", 2),
                     f">= {th['ud_vol_min']:.1f} (demand)", ud >= th["ud_vol_min"]))
        score += _clamp(ud / 1.5, 0, 1) * 100 * 0.4
        weight += 0.4

    if de is not None:
        rows.append(("Debt to equity", _fmt(de, "%", 0), "<= 100% (lower is safer)", de <= 100))
        score += (100 if de <= 40 else 70 if de <= 100 else 30) * 0.2
        weight += 0.2

    if weight == 0:
        return _letter("S", "Supply and Demand", None, None, rows, "No supply/demand data.")
    score = score / weight
    passed = ud is None or ud >= th["ud_vol_min"]
    return _letter("S", "Supply and Demand", round(_clamp(score)), passed, rows,
                   f"U/D volume {_fmt(ud, '', 2)}, float {_fmt(shares / 1e6 if shares else None, 'M', 0)}")


def eval_L(rating: int | None, rs_at_high: bool | None, th: dict) -> dict:
    if rating is None:
        return _letter("L", "Leader or Laggard", None, None, [],
                       "Not enough price history to compute relative strength.")
    rows = [
        ("RS rating (approx., vs S&P 500)", str(rating),
         f">= {th['rs_min']:.0f} (winners avg 87)", rating >= th["rs_min"]),
        ("RS line near 52-week high", {True: "yes", False: "no", None: "n/a"}[rs_at_high],
         "yes", rs_at_high),
    ]
    score = _clamp(rating + (5 if rs_at_high else 0), 0, 100)
    return _letter("L", "Leader or Laggard", round(score), rating >= th["rs_min"], rows,
                   f"Approx. RS rating {rating} "
                   f"({'leader' if rating >= th['rs_min'] else 'laggard - book says avoid below 70'})")


def eval_I(f: dict, th: dict) -> dict:
    inst = f["inst_pct"]
    if inst is None:
        return _letter("I", "Institutional Sponsorship", None, None, [],
                       "No institutional ownership data available.")
    overowned = inst > th["inst_max"]
    rows = [
        ("Institutional ownership", _fmt(inst, "%"),
         f"{th['inst_min']:.0f}% - {th['inst_max']:.0f}%",
         th["inst_min"] <= inst <= th["inst_max"]),
        ("Insider ownership", _fmt(f["insider_pct"], "%"), "higher is a plus", None),
    ]
    if inst < 5:
        score = 15.0
    elif inst < th["inst_min"]:
        score = 50.0
    elif overowned:
        score = 40.0
    else:
        score = 100.0 - max(inst - 80.0, 0.0)  # mild penalty as it nears overowned
    passed = th["inst_min"] <= inst <= th["inst_max"]
    note = "overowned - large potential selling" if overowned else ""
    return _letter("I", "Institutional Sponsorship", round(_clamp(score)), passed, rows,
                   f"{_fmt(inst, '%')} institutional ownership. {note}".strip())


def liquidity_check(p: dict, th: dict) -> dict:
    """Not a CANSLIM letter, but the book repeatedly warns against thin,
    low-priced stocks ('forget cheap stocks')."""
    ok_price = p["last_close"] >= th["min_price"]
    ok_vol = p["avg_vol_50"] is None or p["avg_vol_50"] >= th["min_avg_vol"]
    return {
        "ok": ok_price and ok_vol,
        "rows": [
            ("Share price", f"${p['last_close']:,.2f}", f">= ${th['min_price']:.0f}", ok_price),
            ("Avg daily volume (50d)", _fmt(p["avg_vol_50"], "", 0),
             f">= {th['min_avg_vol']:,}", ok_vol),
        ],
    }


# ---------------------------------------------------------------------------
# M = Market direction (index-level, shared by all stocks)
# ---------------------------------------------------------------------------
def market_health(df: pd.DataFrame) -> dict:
    """Trend state of a market index/ETF per the book's M chapter:
    price vs 50/200-day lines for trend, plus a count of distribution days
    (down on higher volume) in the last 25 sessions - 5-6 of them within a
    few weeks has historically marked tops."""
    close, vol = df["Close"], df["Volume"]
    last = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    dist_days = 0
    recent = df.iloc[-26:]
    pct_chg = recent["Close"].pct_change()
    vol_up = recent["Volume"].diff() > 0
    for d in recent.index[1:]:
        if pct_chg.loc[d] <= -0.002 and vol_up.loc[d]:
            dist_days += 1

    above50 = sma50 is not None and last > sma50
    above200 = sma200 is not None and last > sma200
    golden = sma50 is not None and sma200 is not None and sma50 > sma200

    if above50 and golden and dist_days <= 4:
        status, color = "Confirmed uptrend", "green"
    elif above200 and dist_days <= 6:
        status, color = "Uptrend under pressure", "orange"
    else:
        status, color = "Market in correction", "red"

    high52 = float(close.iloc[-252:].max()) if len(close) >= 252 else float(close.max())
    return {
        "status": status, "color": color, "last": last,
        "sma50": sma50, "sma200": sma200,
        "above50": above50, "above200": above200, "golden_cross": golden,
        "dist_days": dist_days,
        "pct_off_high": (high52 - last) / high52 * 100.0,
    }


def combine_market(healths: list[dict]) -> dict:
    """One overall M verdict from the individual index verdicts (worst wins
    between 'confirmed' and 'correction'; mixed -> under pressure)."""
    order = {"Confirmed uptrend": 0, "Uptrend under pressure": 1, "Market in correction": 2}
    if not healths:
        return {"status": "Unknown", "color": "gray", "passed": None}
    worst = max(healths, key=lambda h: order[h["status"]])
    return {"status": worst["status"], "color": worst["color"],
            "passed": worst["status"] == "Confirmed uptrend"}


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------
def evaluate_stock(fund: dict, hist: pd.DataFrame, bench_hist: pd.DataFrame | None,
                   th: dict) -> dict:
    p = price_metrics(hist)
    bench_raw = _rs_raw(bench_hist["Close"]) if bench_hist is not None else None
    rating = rs_rating(p["rs_raw"], bench_raw)
    at_high = (rs_line_near_high(hist["Close"], bench_hist["Close"])
               if bench_hist is not None else None)

    letters = [
        eval_C(fund, th),
        eval_A(fund, th),
        eval_N(p, th),
        eval_S(fund, p, th),
        eval_L(rating, at_high, th),
        eval_I(fund, th),
    ]

    total_w = sum(WEIGHTS[l["key"]] for l in letters if l["score"] is not None)
    composite = (round(sum(l["score"] * WEIGHTS[l["key"]] for l in letters
                           if l["score"] is not None) / total_w)
                 if total_w else None)
    n_scored = sum(1 for l in letters if l["score"] is not None)
    n_passed = sum(1 for l in letters if l["passed"])

    return {
        "letters": letters,
        "composite": composite,
        "passed_count": n_passed,
        "scored_count": n_scored,
        "price": p,
        "rs_rating": rating,
        "liquidity": liquidity_check(p, th),
        "data_completeness": n_scored / len(letters),
    }


def verdict(composite: int | None, passed: int, scored: int, market_pass: bool | None) -> str:
    if composite is None or scored < 3:
        return "Insufficient data"
    if composite >= 80 and passed >= 5:
        base = "Strong CANSLIM candidate"
    elif composite >= 65 and passed >= 4:
        base = "Possible candidate - verify the weak letters"
    elif composite >= 50:
        base = "Mixed - fails key criteria"
    else:
        base = "Does not fit CANSLIM"
    if market_pass is False and composite >= 65:
        base += " (but M says wait: market not in confirmed uptrend)"
    return base

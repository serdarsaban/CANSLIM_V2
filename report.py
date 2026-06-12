"""Standalone HTML report generation, so every number, its source and its
data date can be checked manually outside the app."""

from __future__ import annotations

import datetime as dt
import html


def _esc(x) -> str:
    return html.escape(str(x)) if x is not None else ""


def _icon(flag) -> str:
    return {True: "&#9989;", False: "&#10060;"}.get(flag, "&#8211;")


_CSS = """
body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
       max-width: 1000px; margin: 24px auto; padding: 0 16px; color: #1a202c; }
h1 { margin-bottom: 0; } h2 { margin-top: 32px; border-bottom: 2px solid #e2e8f0; padding-bottom: 4px; }
.sub { color: #64748b; margin-top: 4px; }
.verdict { padding: 12px 16px; border-radius: 8px; background: #f1f5f9;
           border-left: 5px solid #64748b; margin: 16px 0; font-size: 1.05em; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }
.card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 16px; min-width: 130px; }
.card .v { font-size: 1.5em; font-weight: 700; } .card .l { color: #64748b; font-size: .8em; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; font-size: .9em; }
th, td { border: 1px solid #e2e8f0; padding: 6px 9px; text-align: left; vertical-align: top; }
th { background: #f8fafc; }
td.src { color: #64748b; font-size: .85em; max-width: 320px; }
.pass { color: #059669; font-weight: 600; } .fail { color: #dc2626; font-weight: 600; }
.note { color: #64748b; font-size: .85em; }
.letterhead { font-size: 1.05em; font-weight: 700; margin-top: 18px; }
"""


def _rows_table(rows) -> str:
    body = ""
    for r in rows:
        label, value, target, ok = r[0], r[1], r[2], r[3]
        src = r[4] if len(r) > 4 else ""
        body += (f"<tr><td>{_esc(label)}</td><td>{_esc(value)}</td>"
                 f"<td>{_esc(target)}</td><td>{_icon(ok)}</td>"
                 f"<td class='src'>{_esc(src)}</td></tr>")
    return ("<table><tr><th>Metric</th><th>Value</th><th>Target</th><th>Pass</th>"
            "<th>Data source &amp; date</th></tr>" + body + "</table>")


def _doc(title: str, body: str) -> str:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_esc(title)}</title><style>{_CSS}</style></head><body>
{body}
<h2>Disclaimer</h2>
<p class="note">Generated {stamp} by the CANSLIM screener (based on William O'Neil,
<i>How to Make Money in Stocks</i>). Educational tool, not investment advice.
Built on free data sources (Yahoo Finance, Stooq, optionally Financial Modeling Prep);
data can be delayed, revised or incomplete - every table above lists the exact source
and data date so figures can be verified manually. The RS rating is an approximation
of IBD's proprietary rating (weighted 12-month return vs the S&amp;P 500, recent
quarter double-weighted, mapped to 1-99).</p>
</body></html>"""


def build_stock_report(symbol: str, fund: dict, result: dict,
                       market_status: str, th: dict) -> str:
    p = result["price"]
    cards = "".join(
        f"<div class='card'><div class='v'>{_esc(v)}</div><div class='l'>{_esc(l)}</div></div>"
        for l, v in [
            ("CANSLIM score (0-100)", result["composite"] if result["composite"] is not None else "n/a"),
            ("Letters passed", f"{result['passed_count']} / {result['scored_count']} scored"),
            ("Last close", f"${p['last_close']:,.2f}"),
            ("% off 52-week high", f"{p['pct_off_high']:.1f}%"),
            ("RS rating (approx.)", result["rs_rating"] or "n/a"),
            ("Market (M)", market_status),
        ])

    letters_html = ""
    for letter in result["letters"]:
        status = ("<span class='pass'>PASS</span>" if letter["passed"]
                  else "<span class='fail'>FAIL</span>" if letter["passed"] is False
                  else "<span class='note'>NO DATA</span>")
        score = f"{letter['score']}/100" if letter["score"] is not None else "&#8211;"
        letters_html += (f"<div class='letterhead'>{_esc(letter['key'])} = "
                         f"{_esc(letter['title'])} &mdash; {score} {status}</div>"
                         f"<p class='note'>{_esc(letter['summary'])}</p>")
        if letter["rows"]:
            letters_html += _rows_table(letter["rows"])

    liq = result["liquidity"]
    letters_html += (f"<div class='letterhead'>Liquidity sanity checks &mdash; "
                     f"{'<span class=pass>OK</span>' if liq['ok'] else '<span class=fail>FAIL</span>'}</div>"
                     + _rows_table(liq["rows"]))

    # Raw-data appendix for manual verification
    appendix = "<h2>Raw data appendix (for manual verification)</h2>"
    appendix += (f"<p class='note'>Fundamentals fetched: {_esc(fund['fetched_at'])} &middot; "
                 f"Price series: {_esc(p['source'])}</p>")
    if fund.get("eps_quarters"):
        rows = "".join(f"<tr><td>{_esc(d)}</td><td>${v:,.2f}</td></tr>"
                       for d, v in fund["eps_quarters"])
        appendix += ("<h3>Quarterly EPS series used</h3>"
                     f"<p class='note'>{_esc(fund.get('prov', {}).get('eps_q', ''))}</p>"
                     "<table><tr><th>Quarter / report date</th><th>EPS</th></tr>"
                     + rows + "</table>")
    if fund.get("eps_annual"):
        rows = "".join(f"<tr><td>{y}</td><td>${v:,.2f}</td></tr>" for y, v in fund["eps_annual"])
        appendix += ("<h3>Annual EPS series used</h3>"
                     f"<p class='note'>{_esc(fund.get('prov', {}).get('eps_annual', ''))}</p>"
                     "<table><tr><th>Fiscal year</th><th>EPS</th></tr>" + rows + "</table>")
    if fund.get("sources"):
        appendix += ("<h3>Endpoints queried</h3><p class='note'>"
                     + _esc(", ".join(fund["sources"])) + "</p>")
    if fund.get("errors"):
        appendix += ("<h3>Endpoints that failed</h3><p class='note'>"
                     + _esc("; ".join(fund["errors"])) + "</p>")
    th_str = ", ".join(f"{k}={v}" for k, v in th.items())
    appendix += f"<h3>Thresholds in force</h3><p class='note'>{_esc(th_str)}</p>"

    body = (f"<h1>{_esc(fund['name'])} ({_esc(symbol)})</h1>"
            f"<div class='sub'>CANSLIM analysis report</div>"
            f"<div class='cards'>{cards}</div>"
            f"<div class='verdict'><b>Verdict:</b> {_esc(result.get('verdict', ''))}</div>"
            f"<h2>Letter-by-letter results</h2>{letters_html}{appendix}")
    return _doc(f"CANSLIM report - {symbol}", body)


def build_screener_report(df, market_status: str, th: dict) -> str:
    table = df.to_html(index=False, border=0, escape=True)
    th_str = ", ".join(f"{k}={v}" for k, v in th.items())
    body = (f"<h1>CANSLIM screener results</h1>"
            f"<div class='sub'>Market direction (M): {_esc(market_status)}</div>"
            + table +
            f"<p class='note'>Thresholds in force: {_esc(th_str)}</p>"
            "<p class='note'>Download the per-stock report from the Single Stock tab "
            "for full data provenance on any ticker.</p>")
    return _doc("CANSLIM screener results", body)

#!/usr/bin/env python3
"""Generate a self-contained GEX dashboard HTML + JSON snapshot.

Usage:
    python generate.py --symbols SPY QQQ SPX
    python generate.py --symbols SPY --expiry 2026-10-30 --output dashboard.html

No API key, no paid feed: data comes from CBOE delayed quotes
(~15 minutes delayed). Run it again any time to refresh; it does not
trade, place orders, or send anything anywhere.

Output files:
    dashboard.html  - standalone page (no JS dependencies, opens offline)
    gex_data.json   - the aggregated numbers behind the page
"""
import argparse
import json
import sys
from datetime import date

sys.path.insert(0, "src")
from cboe import fetch_chain, expiries
from gex import aggregate, key_levels, near_spot
from bs import vanna_charm


def fmt_m(x):
    return f"${x / 1e6:,.1f}M"


def bar(value, max_abs, width=120):
    """A horizontal bar as an inline div; green = positive, red = negative."""
    if max_abs == 0:
        return ""
    w = abs(value) / max_abs * width
    color = "#2e9e5b" if value >= 0 else "#d64545"
    side = "left" if value >= 0 else "right"
    return (f'<span style="display:inline-block;width:{width}px;text-align:{side}">'
            f'<span style="display:inline-block;height:10px;width:{w:.0f}px;'
            f'background:{color}"></span></span>')


def build_symbol(symbol):
    meta, options = fetch_chain(symbol)
    spot = meta["current_price"] or meta["prev_day_close"]
    exps = expiries(options)
    # Use the New York trading calendar, not the VM clock: after the
    # 16:00 ET cash close, today's expiry is dead, so skip to the next.
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    ny = _dt.now(ZoneInfo("America/New_York"))
    today = ny.date().isoformat()
    if ny.hour >= 16:
        future = [e for e in exps if e > today]
    else:
        future = [e for e in exps if e >= today]
    expiry = future[0] if future else exps[-1]
    rows = aggregate(options, spot, expiry)
    levels = key_levels(rows, spot)
    # Charm exposure per strike for the heatmap column: aggregate the
    # BS charm of each contract, weighted by open interest.
    from datetime import datetime
    exp_d = datetime.strptime(expiry, "%Y-%m-%d").date()
    t_years = max((exp_d - date.today()).days, 1) / 365.0
    charm_by_strike = {}
    for o in options:
        if o["expiry"] != expiry or o["iv"] <= 0:
            continue
        _, charm = vanna_charm(spot, o["strike"], o["iv"], t_years)
        charm_by_strike[o["strike"]] = charm_by_strike.get(o["strike"], 0.0) + charm * o["open_interest"]
    table_rows = near_spot(rows, spot, 0.05)
    max_abs = max((abs(r["net_gex"]) for r in table_rows), default=0)
    return {
        "symbol": symbol, "meta": meta, "expiry": expiry,
        "expiries": exps[:12], "levels": levels,
        "rows": table_rows, "max_abs": max_abs,
        "charm": charm_by_strike,
    }


def render(symbols_data, timestamp):
    parts = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GEX Dashboard (free, CBOE delayed)</title>
<style>body{font-family:system-ui,-apple-system,sans-serif;margin:24px;color:#222}
h1{font-size:22px}h2{font-size:18px;margin-top:32px}
.card{display:inline-block;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:4px}
.card b{display:block;font-size:12px;color:#666}.card span{font-size:18px}
table{border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid #eee;padding:4px 8px;text-align:right}
td:first-child,th:first-child{text-align:left}
.note{color:#666;font-size:13px;max-width:900px}</style></head><body>"""]
    parts.append(f"<h1>GEX Dashboard — 免费版（CBOE 延迟行情，约 15 分钟延迟）</h1>"
                 f"<p class='note'>数据时间：{timestamp}。GEX 定义：gamma × 持仓量 × 100 × 现货价² × 1%。"
                 "做市商真实头寸不可观测，这里用全部持仓量作代理——这是所有公开 GEX 工具的共同假设，"
                 "图是结构参考，不是做市商账本。排名、墙、翻转点不受绝对值缩放影响，但绝对美元值不要跨平台直接比较。"
                 "本页不构成投资建议，不下单。</p>")
    for d in symbols_data:
        lv = d["levels"]
        parts.append(f"<h2>{d['symbol']} — 到期 {d['expiry']}（现货 {lv['spot']:.2f}）</h2>")
        cards = [
            ("Total GEX", fmt_m(lv["total_gex"])),
            ("Call Wall", f"{lv['call_wall']:.0f}"),
            ("Put Wall", f"{lv['put_wall']:.0f}"),
            ("Gamma Flip", f"{lv['gamma_flip']:.0f}"),
            ("Max Pain", f"{lv['max_pain']:.0f}"),
        ]
        for label, val in cards:
            parts.append(f"<div class='card'><b>{label}</b><span>{val}</span></div>")
        parts.append("<p class='note'>规则速查：价格在 Flip 上方 → 做市商行为压波动（GFM 口径：适合卖方）；"
                     "下方 → 放大波动（适合买方或观望）。接近 Call Wall 阻力、Put Wall 支撑；"
                     "Max Pain 是到期时卖方利润最大点，仅作参考。</p>")
        parts.append("<table><tr><th>行权价</th><th>Call GEX</th><th>Put GEX</th>"
                     "<th>Net GEX</th><th>分布</th><th>Charm×OI</th></tr>")
        for r in d["rows"]:
            ch = d["charm"].get(r["strike"], 0.0)
            parts.append(f"<tr><td>{r['strike']:.0f}</td><td>{fmt_m(r['call_gex'])}</td>"
                         f"<td>{fmt_m(r['put_gex'])}</td><td>{fmt_m(r['net_gex'])}</td>"
                         f"<td>{bar(r['net_gex'], d['max_abs'])}</td><td>{ch:,.0f}</td></tr>")
        parts.append("</table>")
        parts.append(f"<p class='note'>显示现货 ±5% 内的行权价，共 {len(d['rows'])} 档；"
                     f"该到期日共 {lv['n_strikes']} 档。完整数据见 gex_data.json。</p>")
    parts.append("</body></html>")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "SPX"])
    ap.add_argument("--expiry", default=None, help="override expiry, e.g. 2026-10-30")
    ap.add_argument("--output", default="dashboard.html")
    ap.add_argument("--json", default="gex_data.json")
    args = ap.parse_args()

    data = []
    for sym in args.symbols:
        d = build_symbol(sym)
        if args.expiry:
            meta, options = fetch_chain(sym)
            spot = meta["current_price"] or meta["prev_day_close"]
            rows = aggregate(options, spot, args.expiry)
            d.update({"expiry": args.expiry, "levels": key_levels(rows, spot),
                      "rows": near_spot(rows, spot, 0.05),
                      "max_abs": max((abs(r["net_gex"]) for r in near_spot(rows, spot, 0.05)), default=0)})
        data.append(d)
        lv = d["levels"]
        print(f"{sym} {d['expiry']} spot={lv['spot']:.2f} total={fmt_m(lv['total_gex'])} "
              f"call_wall={lv['call_wall']:.0f} put_wall={lv['put_wall']:.0f} "
              f"flip={lv['gamma_flip']:.0f} max_pain={lv['max_pain']:.0f}")

    ts = data[0]["meta"].get("timestamp") if data else ""
    html = render(data, ts)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    serialisable = [{k: v for k, v in d.items() if k != "charm"} for d in data]
    # charm keys are floats; stringify for JSON
    for d, s in zip(data, serialisable):
        s["charm"] = {str(k): v for k, v in d["charm"].items()}
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(serialisable, f, indent=2, ensure_ascii=False)
    print(f"Wrote {args.output} and {args.json}")


if __name__ == "__main__":
    main()

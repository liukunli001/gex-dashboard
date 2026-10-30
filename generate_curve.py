"""Generate backtest88_curve.html: curves + metrics + sensitivity.

Honesty label on the page itself:
- This is NOT GFM 88# and NOT GFM's curve. 88# parameters were not
  found in the wisdep trial, so there is nothing to reproduce.
- Curves are BS-model backtests on SPY daily OHLC, 1 contract,
  $1000 start, $5.20/trade fees. The result flips sign with the
  IV-premium assumption (see sensitivity table), so no curve here
  should be read as an expected return.
"""
import json
import sys

sys.path.insert(0, "src")
import backtest88 as b1
import backtest88_v2 as v2

rows = b1.fetch_spy_ohlc()
orig = v2.realised_vol
groups = {}


def add_group(name, delta_target, wing, stop_mult, iv_factor):
    v2.realised_vol = lambda closes, orig=orig, f=iv_factor: orig(closes) * f / 1.5
    curve, trades = v2.run_v2(rows, delta_target, wing, stop_mult)
    done = [t for t in trades if "pnl" in t]
    groups[name] = {
        "curve": [round(v, 2) for v in curve],
        "metrics": v2.metrics(curve, trades),
        "trades": len(done),
        "skipped": len(trades) - len(done),
        "first_half_pnl": round(sum(t["pnl"] for t in done[: len(done) // 2]), 2),
        "second_half_pnl": round(sum(t["pnl"] for t in done[len(done) // 2:]), 2),
        "sample_trades": done[:20],
    }


add_group("delta0.20 wing5 stop1.5x IV=1.0x realised", 0.20, 5, 1.5, 1.0)
add_group("delta0.20 wing5 stop1.5x IV=1.2x realised", 0.20, 5, 1.5, 1.2)
add_group("delta0.20 wing5 stop1.5x IV=1.5x realised", 0.20, 5, 1.5, 1.5)
add_group("delta0.20 wing10 stop1.5x IV=1.5x realised", 0.20, 10, 1.5, 1.5)
v2.realised_vol = orig

with open("backtest88_sensitivity.json", "w", encoding="utf-8") as f:
    json.dump(groups, f, indent=2)


def svg_path(curve, w=900, h=300):
    mx = max(curve)
    mn = min(curve)
    span = mx - mn or 1
    pts = []
    for i, val in enumerate(curve):
        x = i / max(len(curve) - 1, 1) * w
        y = h - (val - mn) / span * (h - 20) - 10
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts), mn, mx


colors = {"delta0.20 wing5 stop1.5x IV=1.0x realised": "#c0392b",
          "delta0.20 wing5 stop1.5x IV=1.2x realised": "#e67e22",
          "delta0.20 wing5 stop1.5x IV=1.5x realised": "#27ae60",
          "delta0.20 wing10 stop1.5x IV=1.5x realised": "#2980b9"}
polys = []
for name, g in groups.items():
    pts, mn, mx = svg_path(g["curve"])
    polys.append(f'<polyline points="{pts}" fill="none" stroke="{colors[name]}" stroke-width="2"/>')
    polys.append(f'<!-- {name}: min {mn} max {mx} -->')

rows_html = ""
for name, g in groups.items():
    m = g["metrics"]
    rows_html += (f"<tr><td>{name}</td><td>{g['trades']} (skip {g['skipped']})</td>"
                  f"<td>{m.get('final_equity')}</td><td>{m.get('total_return')}</td>"
                  f"<td>{m.get('win_rate')}</td><td>{m.get('max_drawdown')}</td>"
                  f"<td>{m.get('profit_factor')}</td>"
                  f"<td>{g['first_half_pnl']} / {g['second_half_pnl']}</td></tr>\n")

sample = groups["delta0.20 wing10 stop1.5x IV=1.5x realised"]["sample_trades"]
sample_html = "".join(
    f"<tr><td>{t['date_ts']}</td><td>{t['short_put']}P / {t['short_call']}C</td>"
    f"<td>{t['credit']}</td><td>{t['pnl']}</td><td>{t['outcome']}</td><td>{t['equity']}</td></tr>\n"
    for t in sample)

html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>88# backtest v2 -- model curves, NOT GFM</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:2em auto;padding:0 1em}}
.warn{{background:#fdf2e9;border:1px solid #e67e22;padding:1em}} table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}} svg{{background:#fafafa;border:1px solid #ddd}}</style>
</head><body>
<h1>铁鹰 0DTE 回测 v2 -- 模型曲线（不是 GFM 88#）</h1>
<div class="warn"><b>先读结论：</b>88# 原参数在 wisdep 试用区没有找到，所以这里没有任何一条曲线是“复现 GFM”。
下面是 BS 模型在 SPY 日线上的回测，1 张合约、$1000 起步、手续费 $5.20/笔、持有至收盘、
盘中破盈亏平衡按止损倍数离场。结果随 IV 溢价假设完全翻面：
IV=1.0x/1.2x 实现波动率时亏到 $362/$357，IV=1.5x 时变成 $14,294。
<b>结论是模型对假设极度敏感，不是对策略的验证，更不是预期收益。</b>
这与他 2026-10-07 的 0DTE 回测“无 edge”结论并不矛盾：没有真实历史期权价（Databento tick），
就无法区分“策略真有 edge”和“假设给了 edge”。</div>
<h2>净值曲线（$1000 起步，1 张合约，不复利加仓）</h2>
<svg viewBox="0 0 900 300" width="900" height="300">{''.join(polys)}</svg>
<p><span style="color:#c0392b">■</span> wing5 IV 1.0x &nbsp;
<span style="color:#e67e22">■</span> wing5 IV 1.2x &nbsp;
<span style="color:#27ae60">■</span> wing5 IV 1.5x &nbsp;
<span style="color:#2980b9">■</span> wing10 IV 1.5x</p>
<h2>敏感性表</h2>
<table><tr><th>组</th><th>交易数</th><th>终值</th><th>总回报</th><th>胜率</th><th>最大回撤</th><th>盈亏比</th><th>前半/后半 PnL</th></tr>
{rows_html}</table>
<p>另注意：wing10 + $1000 在 IV 1.0x/1.2x 下只成交 3 笔就因保证金不足全部跳过（账户 $807/$813 后无法再开仓），
这是 $1000 本金 + 10 点翼宽的结构性脆弱，不是策略信号。</p>
<h2>数据来源与假设</h2>
<ul><li>标的价格：Yahoo SPY 日线 OHLC，365 个交易日（样本期以 backtest88_v2.json 为准）。</li>
<li>定价：Black-Scholes，T=1/365，IV = 20 日实现波动率 x 溢价因子（下限 0.12），短腿按 |delta| 目标选 $1 整数行权价。</li>
<li>离场：收盘内在价值结算；盘中高/低破盈亏平衡即按止损倍数离场（上限为最大亏损）；无盘中止盈假设。</li>
<li>费用：$0.65/张 x 8 边 = $5.20/笔。滑点未单独建模（已含在 bid/ask 权利金口径的讨论里，见校准记录）。</li>
<li>未使用：真实历史期权价、盘中 tick、GFM 回测曲线数字、Discord 推送内容（均无来源）。</li></ul>
<h2>示例交易明细（wing10 IV 1.5x 组，前 20 笔）</h2>
<table><tr><th>日期戳</th><th>短腿</th><th>权利金</th><th>PnL</th><th>结果</th><th>权益</th></tr>
{sample_html}</table>
<p>完整曲线与敏感性数据：backtest88_sensitivity.json；代码：src/backtest88_v2.py。</p>
</body></html>
"""
with open("backtest88_curve.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Wrote backtest88_curve.html and backtest88_sensitivity.json")
for name, g in groups.items():
    print(name, g["metrics"])

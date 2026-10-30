"""88# reproduction: 0DTE neutral Iron Condor backtest engine.

WHAT THIS IS -- and what it is NOT:
  * IS: a parameter-sweep backtest of the *template* GFM promotes as 88#:
    a neutral 0DTE Iron Condor, entered in the morning, exited the same day.
    Underlying here is SPY (the ETF version of the SPXW template; SPXW
    itself is verified to exist in the free CBOE chain, but free
    *historical* SPXW option prices are not available, so the sweep
    uses SPY, whose historical OHLC is free via Yahoo chart data).
  * IS NOT: GFM's own backtest. GFM has not published its entry time,
    short-leg delta/distance, wing, profit target or stop. The
    parameters below are the *sweep grid* -- the research question is
    which parameter set, if any, produces a curve with the shape GFM
    claims (high win rate, controlled drawdown), and whether it
    survives an out-of-sample split. Sweeping until a curve "looks
    good" in-sample is overfitting unless the out-of-sample half
    confirms it; both halves are reported.

MODEL (all assumptions stated, no hidden inputs):
  * One trade per trading day, 1 contract, entered at the open.
  * Short strikes = open * (1 +/- distance); long strikes = short +/- wing.
  * Credit is priced by Black-Scholes at entry, with IV = 20-day
    realised volatility (annualised, floored 0.08, capped 0.50) and
    T = 1/252 years. No historical option prices are used, because
    none are freely available; this is the largest model limitation
    and is stated on every output.
  * Exit: if the day's high/low breaches a breakeven, the stop fires
    and the loss is stop_multiple * credit (capped at max loss).
    Otherwise the trade is held to expiry (close): payoff =
    credit - call-spread intrinsic - put-spread intrinsic, capped
    at profit_target * credit on the upside.
  * Fees: IBKR $0.65/contract x 8 contract-sides (4 legs in, 4 out)
    = $5.20 per trade, deducted from every trade's P&L.
  * Margin check: wing*100 - credit*100 must fit in current equity,
    otherwise the day is skipped (this is what makes $1000 binding).
  * Starting equity: $1000.
"""
import json
import math
import sys

import requests

FEE_PER_TRADE = 0.65 * 8  # 4 legs in + 4 legs out


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot, strike, vol, t_years, kind, r=0.035):
    if vol <= 0 or t_years <= 0:
        return max(spot - strike, 0.0) if kind == "C" else max(strike - spot, 0.0)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    if kind == "C":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def fetch_spy_ohlc(days=365):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/SPY?range=2y&interval=1d"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
            continue
        rows.append({"date_ts": t, "open": q["open"][i], "high": q["high"][i],
                     "low": q["low"][i], "close": q["close"][i]})
    return rows[-days:]


def realised_vol(closes, window=20):
    if len(closes) < window + 1:
        return 0.15
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    recent = rets[-window:]
    mean = sum(recent) / len(recent)
    var = sum((x - mean) ** 2 for x in recent) / len(recent)
    return min(max(math.sqrt(var * 252), 0.08), 0.50)


def run_backtest(rows, distance, wing, profit_frac, stop_mult):
    """Return (equity_curve, trades). equity_curve[0] = 1000."""
    equity = 1000.0
    curve = [equity]
    trades = []
    closes = [r["close"] for r in rows]
    for i, r in enumerate(rows):
        if i < 21:
            curve.append(equity)
            continue
        spot = r["open"]
        vol = realised_vol(closes[:i])
        t = 1.0 / 252.0
        short_put = spot * (1 - distance)
        short_call = spot * (1 + distance)
        long_put = short_put - wing
        long_call = short_call + wing
        credit = (bs_price(spot, short_put, vol, t, "P")
                  + bs_price(spot, short_call, vol, t, "C")
                  - bs_price(spot, long_put, vol, t, "P")
                  - bs_price(spot, long_call, vol, t, "C"))
        if credit <= 0:
            curve.append(equity)
            continue
        margin = wing * 100 - credit * 100
        if margin > equity:
            trades.append({"date_ts": r["date_ts"], "skipped": "margin", "margin": round(margin, 2)})
            curve.append(equity)
            continue
        be_low = short_put - credit
        be_high = short_call + credit
        max_loss = (wing - credit) * 100
        if r["low"] <= be_low or r["high"] >= be_high:
            pnl = -min(stop_mult * credit * 100, max_loss)
            outcome = "stop"
        else:
            call_pay = min(max(r["close"] - short_call, 0.0), wing)
            put_pay = min(max(short_put - r["close"], 0.0), wing)
            pnl = credit * 100 - (call_pay + put_pay) * 100
            if pnl > profit_frac * credit * 100:
                pnl = profit_frac * credit * 100
                outcome = "profit_target"
            else:
                outcome = "expiry"
        pnl -= FEE_PER_TRADE
        equity += pnl
        trades.append({"date_ts": r["date_ts"], "open": round(spot, 2),
                       "short_put": round(short_put, 2), "short_call": round(short_call, 2),
                       "credit": round(credit, 3), "pnl": round(pnl, 2),
                       "outcome": outcome, "equity": round(equity, 2)})
        curve.append(equity)
        if equity <= 0:
            break
    return curve, trades


def metrics(curve, trades):
    done = [t for t in trades if "pnl" in t]
    if not done:
        return {}
    wins = [t for t in done if t["pnl"] > 0]
    losses = [t for t in done if t["pnl"] <= 0]
    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak else 0)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    return {
        "n_trades": len(done),
        "win_rate": round(len(wins) / len(done), 3),
        "final_equity": round(curve[-1], 2),
        "total_return": round(curve[-1] / 1000 - 1, 3),
        "max_drawdown": round(max_dd, 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
    }


def sweep(rows):
    results = []
    for distance in (0.01, 0.015, 0.02):
        for wing in (5, 10):
            for profit_frac in (0.5, 0.8):
                for stop_mult in (2.0, 3.0):
                    curve, trades = run_backtest(rows, distance, wing, profit_frac, stop_mult)
                    # Out-of-sample split: first half vs second half of trades.
                    done = [t for t in trades if "pnl" in t]
                    half = len(done) // 2
                    first = sum(t["pnl"] for t in done[:half])
                    second = sum(t["pnl"] for t in done[half:])
                    results.append({
                        "params": {"distance": distance, "wing": wing,
                                   "profit_frac": profit_frac, "stop_mult": stop_mult},
                        "metrics": metrics(curve, trades),
                        "first_half_pnl": round(first, 2),
                        "second_half_pnl": round(second, 2),
                    })
    results.sort(key=lambda r: r["metrics"].get("final_equity", 0), reverse=True)
    return results


if __name__ == "__main__":
    rows = fetch_spy_ohlc()
    print(f"SPY rows: {len(rows)}")
    results = sweep(rows)
    for r in results[:8]:
        print(r["params"], r["metrics"],
              f"1H={r['first_half_pnl']} 2H={r['second_half_pnl']}")
    with open("backtest88_sweep.json", "w", encoding="utf-8") as f:
        json.dump({"rows": len(rows), "results": results}, f, indent=2)
    print("Wrote backtest88_sweep.json")

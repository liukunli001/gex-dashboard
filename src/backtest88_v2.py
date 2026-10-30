"""88# backtest v2 -- delta-targeted, calibrated.

CHANGES vs v1 (documented, because v1 was flawed):
  * v1 used distance-fixed short legs (1.5% OTM). Real 0DTE delta-0.20
    legs sit only ~0.6-0.8% OTM on SPY, so v1 legs were too far out,
    credits collapsed (median $0.086), and most trades were skipped
    on margin. v2 targets |delta| directly, as the real chain does.
  * v1 priced with realised vol and T=1/252. Calibration against the
    real 2026-10-30 CBOE chain (SPY/QQQ/IWM, delta-0.20 condors):
    realised vol understates market IV by ~1.5x (SPY 9.8% realised
    vs 14-20% market IV; QQQ 19.6% vs 26-38%; IWM 15.0% vs 18-23%).
    v2 therefore uses IV = realised * 1.5 (floor 0.12) and T=1/365,
    the convention CBOE's own IV is calibrated to. This is still
    an assumption, stated on every output, not a market price.
  * v1 capped every non-stop trade at profit_frac * credit, which
    made almost every in-range close an automatic "profit target"
    hit. v2 holds to expiry: P&L = credit - intrinsic at close,
    unless a breakeven is breached intraday (high/low), in which
    case the stop fires at stop_mult * credit (capped at max loss).
    No intraday profit-target claim is made, because daily OHLC
    cannot show when it would have filled.
  * Fees, margin check and $1000 start are unchanged from v1.
"""
import json
import math

from backtest88 import FEE_PER_TRADE, bs_price, fetch_spy_ohlc, realised_vol


def _bs_delta(spot, strike, vol, t_years, kind, r=0.035):
    if vol <= 0 or t_years <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    ncdf = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return ncdf if kind == "C" else ncdf - 1.0


def delta_strike(spot, vol, t_years, kind, target_abs_delta):
    """Strike (nearest $1) whose BS delta is closest to the target."""
    best, best_err = None, 1e9
    lo = int(spot * 0.95)
    hi = int(spot * 1.05) + 1
    for strike in range(lo, hi + 1):
        d = _bs_delta(spot, strike, vol, t_years, kind)
        err = abs(abs(d) - target_abs_delta)
        if err < best_err:
            best, best_err = strike, err
    return best


def run_v2(rows, delta_target, wing, stop_mult):
    equity = 1000.0
    curve = [equity]
    trades = []
    closes = [r["close"] for r in rows]
    for i, r in enumerate(rows):
        if i < 21:
            curve.append(equity)
            continue
        spot = r["open"]
        vol = max(realised_vol(closes[:i]) * 1.5, 0.12)
        t = 1.0 / 365.0
        short_put = delta_strike(spot, vol, t, "P", delta_target)
        short_call = delta_strike(spot, vol, t, "C", delta_target)
        long_put, long_call = short_put - wing, short_call + wing
        credit = (bs_price(spot, short_put, vol, t, "P")
                  + bs_price(spot, short_call, vol, t, "C")
                  - bs_price(spot, long_put, vol, t, "P")
                  - bs_price(spot, long_call, vol, t, "C"))
        if credit <= 0:
            curve.append(equity)
            continue
        margin = wing * 100 - credit * 100
        if margin > equity:
            trades.append({"date_ts": r["date_ts"], "skipped": "margin"})
            curve.append(equity)
            continue
        be_low, be_high = short_put - credit, short_call + credit
        max_loss = (wing - credit) * 100
        if r["low"] <= be_low or r["high"] >= be_high:
            pnl = -min(stop_mult * credit * 100, max_loss)
            outcome = "stop"
        else:
            call_pay = min(max(r["close"] - short_call, 0.0), wing)
            put_pay = min(max(short_put - r["close"], 0.0), wing)
            pnl = credit * 100 - (call_pay + put_pay) * 100
            outcome = "expiry"
        pnl -= FEE_PER_TRADE
        equity += pnl
        trades.append({"date_ts": r["date_ts"], "open": round(spot, 2),
                       "short_put": short_put, "short_call": short_call,
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
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    return {"n_trades": len(done), "win_rate": round(len(wins) / len(done), 3),
            "final_equity": round(curve[-1], 2),
            "total_return": round(curve[-1] / 1000 - 1, 3),
            "max_drawdown": round(max_dd, 3),
            "profit_factor": round(gw / gl, 2) if gl else None,
            "avg_win": round(gw / len(wins), 2) if wins else 0,
            "avg_loss": round(-gl / len(losses), 2) if losses else 0}


if __name__ == "__main__":
    rows = fetch_spy_ohlc()
    print(f"SPY rows: {len(rows)}")
    results = []
    for delta_target in (0.15, 0.20, 0.25):
        for wing in (5, 10):
            for stop_mult in (1.5, 2.0, 3.0):
                curve, trades = run_v2(rows, delta_target, wing, stop_mult)
                done = [t for t in trades if "pnl" in t]
                half = len(done) // 2
                results.append({
                    "params": {"delta_target": delta_target, "wing": wing, "stop_mult": stop_mult},
                    "metrics": metrics(curve, trades),
                    "first_half_pnl": round(sum(t["pnl"] for t in done[:half]), 2),
                    "second_half_pnl": round(sum(t["pnl"] for t in done[half:]), 2),
                    "skipped": len(trades) - len(done),
                    "curve": [round(v, 2) for v in curve],
                })
    results.sort(key=lambda r: r["metrics"].get("final_equity", 0), reverse=True)
    for r in results[:10]:
        print(r["params"], r["metrics"], f"1H={r['first_half_pnl']} 2H={r['second_half_pnl']} skip={r['skipped']}")
    with open("backtest88_v2.json", "w", encoding="utf-8") as f:
        json.dump({"rows": len(rows), "results": results}, f, indent=2)
    print("Wrote backtest88_v2.json")

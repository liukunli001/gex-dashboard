"""Reproduction of GammaForceMatrix's strategy layer.

This is a re-implementation from GFM's publicly documented rules
(Help pages, 2026-10-29), not GFM's code:
  1. Regime from the GEX dashboard: sign of Total GEX, spot vs Gamma
     Flip, spot vs Call/Put Walls.
  2. Decision tree (see `recommend`).
  3. The flagship template from its upgrade announcement: a neutral
     0DTE Iron Condor, auto-pushed daily. The original is SPXW; this
     repo's scope is QQQ / SPY / IWM ETF options only, so the same
     template is built on those three underlyings (SPXW is blocked on
     the free CBOE endpoint, 403, and is out of scope here).

What this module is NOT: a backtest. The free CBOE endpoint gives
only the current delayed chain, not historical tick/OI history, so
no annualised return, win rate or max drawdown can be reproduced
from it. This builds today's structure and its risk numbers
(credit, max profit/loss, breakevens) from the current chain, for
paper / forward observation. Any claim about 500%-2000% annualised
returns needs a paid tick history (Databento OPRA) plus fees,
slippage, stop rules and a full sample period -- none of which GFM
publishes.

Pricing: credit is computed from real bid/ask, not mid:
    net_credit = bid(short put) + bid(short call)
                 - ask(long put) - ask(long call)   (per share)
    max_profit = net_credit * 100
    max_loss   = (wing_width - net_credit) * 100
    breakevens = short_put_strike - net_credit,
                 short_call_strike + net_credit
Short strikes are delta-targeted (~0.20 |delta|, the usual 0DTE
iron-condor convention); wings are ~1% of spot further OTM.
"""
import json
import sys

sys.path.insert(0, "src")
from cboe import fetch_chain, expiries
from gex import aggregate, key_levels
from datetime import datetime
from zoneinfo import ZoneInfo


def classify(levels):
    spot = levels["spot"]
    regime = "+GEX" if levels["total_gex"] > 0 else "-GEX"
    vs_flip = "above_flip" if spot >= levels["gamma_flip"] else "below_flip"
    near = []
    if abs(spot - levels["call_wall"]) / spot <= 0.01:
        near.append("near_call_wall")
    if abs(spot - levels["put_wall"]) / spot <= 0.01:
        near.append("near_put_wall")
    return {"regime": regime, "vs_flip": vs_flip, "near": near}


def recommend(levels):
    """GFM Help decision tree, verbatim in structure."""
    c = classify(levels)
    out = []
    if c["regime"] == "+GEX":
        out.append("Iron Condor / Butterfly / Short Straddle (seller, theta)")
    else:
        out.append("Long Straddle / Strangle / reverse Butterfly (buyer, delta+IV)")
    if "near_call_wall" in c["near"]:
        out.append("Bear Call Spread (near Call Wall)")
    if "near_put_wall" in c["near"]:
        out.append("Bull Put Spread (near Put Wall)")
    if c["vs_flip"] == "above_flip":
        out.append("Iron Condor / Butterfly (above Flip)")
    else:
        out.append("Directional or wait (below Flip)")
    return out


def _find(options, expiry, kind, strike):
    """Exact strike match, else the nearest available strike."""
    cands = [o for o in options if o["expiry"] == expiry and o["kind"] == kind]
    if not cands:
        return None
    return min(cands, key=lambda o: abs(o["strike"] - strike))


def _delta_pick(options, expiry, kind, target_abs_delta):
    cands = [o for o in options if o["expiry"] == expiry and o["kind"] == kind
             and o["bid"] > 0]
    if not cands:
        return None
    return min(cands, key=lambda o: abs(abs(o["delta"]) - target_abs_delta))


def build_iron_condor(options, expiry, spot, target_delta=0.20):
    """Delta-targeted 0DTE iron condor from the current chain."""
    wing = max(round(spot * 0.01), 1)
    short_put = _delta_pick(options, expiry, "P", target_delta)
    short_call = _delta_pick(options, expiry, "C", target_delta)
    if not short_put or not short_call:
        return None
    long_put = _find(options, expiry, "P", short_put["strike"] - wing)
    long_call = _find(options, expiry, "C", short_call["strike"] + wing)
    if not long_put or not long_call:
        return None
    credit = (short_put["bid"] + short_call["bid"]
              - long_put["ask"] - long_call["ask"])
    put_wing = short_put["strike"] - long_put["strike"]
    call_wing = long_call["strike"] - short_call["strike"]
    return {
        "expiry": expiry,
        "short_put": short_put["strike"], "short_put_delta": short_put["delta"],
        "long_put": long_put["strike"],
        "short_call": short_call["strike"], "short_call_delta": short_call["delta"],
        "long_call": long_call["strike"],
        "put_wing": put_wing, "call_wing": call_wing,
        "net_credit_per_share": round(credit, 2),
        "max_profit": round(credit * 100, 2),
        "max_loss": round((min(put_wing, call_wing) - credit) * 100, 2),
        "breakeven_low": round(short_put["strike"] - credit, 2),
        "breakeven_high": round(short_call["strike"] + credit, 2),
    }


def build_symbol_strategy(symbol):
    meta, options = fetch_chain(symbol)
    spot = meta["current_price"] or meta["prev_day_close"]
    ny = datetime.now(ZoneInfo("America/New_York"))
    today = ny.date().isoformat()
    exps = expiries(options)
    future = [e for e in exps if e > today] if ny.hour >= 16 else \
             [e for e in exps if e >= today]
    expiry = future[0] if future else exps[-1]
    rows = aggregate(options, spot, expiry)
    levels = key_levels(rows, spot)
    return {
        "symbol": symbol, "spot": spot, "expiry": expiry,
        "total_gex_m": round(levels["total_gex"] / 1e6, 1),
        "call_wall": levels["call_wall"], "put_wall": levels["put_wall"],
        "gamma_flip": levels["gamma_flip"], "max_pain": levels["max_pain"],
        "classification": classify(levels),
        "recommendation": recommend(levels),
        "iron_condor": build_iron_condor(options, expiry, spot),
    }


def main():
    symbols = sys.argv[1:] or ["QQQ", "SPY", "IWM"]
    results = []
    for sym in symbols:
        r = build_symbol_strategy(sym)
        results.append(r)
        ic = r["iron_condor"]
        print(f"\n{sym} {r['expiry']} spot={r['spot']:.2f} "
              f"total_gex=${r['total_gex_m']:,.1f}M "
              f"regime={r['classification']['regime']} "
              f"flip={r['gamma_flip']:.0f} walls C{ r['call_wall']:.0f}/P{r['put_wall']:.0f}")
        print("  tree:", " | ".join(r["recommendation"]))
        if ic:
            print(f"  Iron Condor: sell {ic['short_put']:.0f}P / {ic['short_call']:.0f}C "
                  f"(delta {ic['short_put_delta']:.2f}/{ic['short_call_delta']:.2f}), "
                  f"buy {ic['long_put']:.0f}P / {ic['long_call']:.0f}C")
            print(f"  credit ${ic['net_credit_per_share']:.2f} -> max profit "
                  f"${ic['max_profit']:.0f}, max loss ${ic['max_loss']:.0f}, "
                  f"breakevens {ic['breakeven_low']:.0f}-{ic['breakeven_high']:.0f}")
        else:
            print("  Iron Condor: could not build from this chain")
    with open("strategy.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nWrote strategy.json")


if __name__ == "__main__":
    main()

"""GEX aggregation and key levels.

Convention (documented, ranking-invariant to the constant scale):
    GEX(contract) = gamma * open_interest * 100 * S**2 * 0.01
i.e. the dollar value a dealer must trade in the underlying when spot
moves 1%, if the dealer held the opposite side of all open interest.
That last clause is the key assumption of every public GEX tool:
true dealer inventory is not observable, so open interest is used as
its proxy. Rankings, walls and the flip do not depend on the 100x
scale choice; absolute dollar levels do, so never compare absolute
GEX across platforms that use a different scale.

Sign convention used here, matching the usual public tools:
    Call GEX is positive, Put GEX is negative in the net sum, because
    a dealer short calls / long puts hedges in opposite directions.
    In this code we store call_gex >= 0 and put_gex <= 0, so
    net_gex = call_gex + put_gex.
"""
from collections import defaultdict


def aggregate(options, spot, expiry):
    """Aggregate one expiry into per-strike rows, sorted by strike."""
    by_strike = defaultdict(lambda: {"call_gex": 0.0, "put_gex": 0.0,
                                     "call_oi": 0.0, "put_oi": 0.0,
                                     "call_vol": 0.0, "put_vol": 0.0})
    for o in options:
        if o["expiry"] != expiry:
            continue
        gex = o["gamma"] * o["open_interest"] * 100 * spot * spot * 0.01
        row = by_strike[o["strike"]]
        if o["kind"] == "C":
            row["call_gex"] += gex
            row["call_oi"] += o["open_interest"]
            row["call_vol"] += o["volume"]
        else:
            row["put_gex"] -= gex  # puts enter the net sum negative
            row["put_oi"] += o["open_interest"]
            row["put_vol"] += o["volume"]
    rows = []
    for strike in sorted(by_strike):
        r = by_strike[strike]
        r["strike"] = strike
        r["net_gex"] = r["call_gex"] + r["put_gex"]
        rows.append(r)
    return rows


def key_levels(rows, spot):
    """Call/Put Wall, Gamma Flip, Max Pain, centers, total GEX."""
    if not rows:
        return {}
    call_wall = max(rows, key=lambda r: r["call_gex"])
    put_wall = min(rows, key=lambda r: r["put_gex"])  # most negative
    total = sum(r["net_gex"] for r in rows)

    # Gamma Flip: sum net GEX from the highest strike downwards and
    # find where that cumulative sum crosses zero. Summing downwards
    # avoids the tiny, noisy deep-OTM strikes at the low end flipping
    # the sum far from spot; among crossings, pick closest to spot.
    crossings = []
    cum = 0.0
    prev_cum = None
    for r in reversed(rows):
        prev_cum = cum
        cum += r["net_gex"]
        if prev_cum is not None and prev_cum != 0 and (prev_cum < 0) != (cum < 0):
            crossings.append(r["strike"])
    if crossings:
        flip = min(crossings, key=lambda s: abs(s - spot))
    else:
        flip = spot  # no clean crossing: flip is at/near spot by construction

    # Max Pain: candidate expiry price minimising total intrinsic value
    # of all outstanding options at this expiry.
    def total_intrinsic(price):
        s = 0.0
        for r in rows:
            s += max(price - r["strike"], 0.0) * r["call_oi"]
            s += max(r["strike"] - price, 0.0) * r["put_oi"]
        return s

    max_pain = min(rows, key=lambda r: total_intrinsic(r["strike"]))["strike"]

    def weighted_center(key):
        w = sum(abs(r[key]) for r in rows)
        if w == 0:
            return None
        return sum(r["strike"] * abs(r[key]) for r in rows) / w

    call_center = weighted_center("call_gex")
    put_center = weighted_center("put_gex")
    return {
        "spot": spot,
        "total_gex": total,
        "call_wall": call_wall["strike"],
        "call_wall_gex": call_wall["call_gex"],
        "put_wall": put_wall["strike"],
        "put_wall_gex": put_wall["put_gex"],
        "gamma_flip": flip,
        "max_pain": max_pain,
        "call_center": call_center,
        "put_center": put_center,
        "distance": (call_center - put_center) if call_center and put_center else None,
        "n_strikes": len(rows),
    }


def near_spot(rows, spot, pct=0.05):
    """Rows within +/-pct of spot, for a readable dashboard table."""
    return [r for r in rows if abs(r["strike"] - spot) / spot <= pct]

import sys
sys.path.insert(0, "src")
from strategy import recommend, build_iron_condor


def test_recommend():
    plus = {"spot": 100.0, "total_gex": 1e9, "gamma_flip": 99.0,
            "call_wall": 101.0, "put_wall": 98.0}
    rec = recommend(plus)
    assert any("Iron Condor" in r for r in rec), rec
    minus = {"spot": 100.0, "total_gex": -1e9, "gamma_flip": 101.0,
             "call_wall": 110.0, "put_wall": 90.0}
    rec = recommend(minus)
    assert any("Long Straddle" in r for r in rec), rec
    assert any("Directional or wait" in r for r in rec), rec
    print("ok: recommend tree")


def test_iron_condor_math():
    # Synthetic chain: bid/ask set so credit is exactly $1.00.
    opts = []
    for kind, strike, delta, bid, ask in [
        ("P", 95, -0.20, 1.00, 1.10), ("P", 90, -0.05, 0.20, 0.30),
        ("C", 105, 0.20, 1.00, 1.10), ("C", 110, 0.05, 0.20, 0.30),
    ]:
        opts.append({"expiry": "2026-10-30", "kind": kind, "strike": strike,
                     "delta": delta, "bid": bid, "ask": ask})
    # Wing from spot 100 is 1% -> 1 point, but our synthetic strikes are
    # 5 apart, so _find picks the nearest available wing strike.
    ic = build_iron_condor(opts, "2026-10-30", 100.0)
    assert ic["net_credit_per_share"] == 1.40, ic  # 1+1-0.3-0.3
    assert ic["max_profit"] == 140.0
    assert ic["max_loss"] == 360.0  # (5 - 1.40) * 100
    print("ok: iron condor math", ic)


if __name__ == "__main__":
    test_recommend()
    test_iron_condor_math()

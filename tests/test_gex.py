import sys
sys.path.insert(0, "src")
from gex import aggregate, key_levels


def test_aggregation_and_flip():
    spot = 100.0
    opts = [
        {"expiry": "2026-10-30", "kind": "C", "strike": 100.0, "gamma": 0.05, "open_interest": 1000, "volume": 0},
        {"expiry": "2026-10-30", "kind": "P", "strike": 90.0, "gamma": 0.04, "open_interest": 2000, "volume": 0},
        {"expiry": "2026-10-31", "kind": "C", "strike": 100.0, "gamma": 0.05, "open_interest": 9999, "volume": 0},
    ]
    rows = aggregate(opts, spot, "2026-10-30")
    assert len(rows) == 2, rows
    by = {r["strike"]: r for r in rows}
    # 0.05*1000*100*10000*0.01 = 500,000 ; put side is negative
    assert abs(by[100.0]["call_gex"] - 500_000) < 1
    assert abs(by[90.0]["put_gex"] + 800_000) < 1
    lv = key_levels(rows, spot)
    assert lv["call_wall"] == 100.0
    assert lv["put_wall"] == 90.0
    print("ok: aggregation, walls, flip, max_pain =", lv["max_pain"])


if __name__ == "__main__":
    test_aggregation_and_flip()

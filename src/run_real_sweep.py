"""Real-data 0DTE sweep runner (memory-safe, day-by-day).

Why this file exists instead of calling backtest_real.__main__:
  * __main__ loads ONE csv. The full sample is 22 daily csv files
    (~1 GB each, ~7.8M rows each). Loading all 22 days into the
    days dict at once exceeds this VM's RAM (7.7 GB).
  * So this runner loads ONE day at a time, runs all 6 parameter
    groups for that day, carries equity forward day to day per
    group, then discards the day's chain before loading the next.

Fidelity: the per-day block below is a verbatim copy of the
per-day logic in src/backtest_real.run_real, except equity is a
parameter carried across days instead of being reset to 1000.0
for every call. No parameter definition is changed:
distance 20/30/50, wing 10/20, stop_mult 1.5, entry 10:00 ET,
exit 15:30 ET, fee $5.20/trade (FEE_PER_TRADE), 1 contract,
start $1000. ATM is the same chain-inferred proxy as in
backtest_real.py, NOT an observed SPX spot price.

Validation built in: the 2026-10-28, distance 30, wing 10 trade
must reproduce the previously verified single-day result
(ATM 7730.0, credit 4.50, pnl 444.80, outcome expiry).
If it does not, the runner exits non-zero and writes nothing.
"""
import csv
import gc
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_real import (  # noqa: E402
    FEE_PER_TRADE, infer_atm, load_cbbo_csv, quote,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "databento"
ENTRY = "10:00"
EXIT_TIME = "15:30"
START_EQUITY = 1000.0
PARAMS = [(d, w) for d in (20, 30, 50) for w in (10, 20)]
STOP_MULT = 1.5


def run_one_day(date, minutes, distance, wing, equity):
    """One day of backtest_real.run_real, with equity carried in.

    Returns (record, equity_out). record is a trade dict with 'pnl',
    a skip dict with 'skipped', or None when entry/exit minutes are
    missing (run_real appends no trade record in that case).
    """
    if ENTRY not in minutes or EXIT_TIME not in minutes:
        return None, equity
    chain = minutes[ENTRY]
    atm = infer_atm(chain)
    if atm is None:
        return None, equity
    sp, sc = atm - distance, atm + distance
    lp, lc = sp - wing, sc + wing
    legs = {"SP": quote(chain, sp, "P"), "SC": quote(chain, sc, "C"),
            "LP": quote(chain, lp, "P"), "LC": quote(chain, lc, "C")}
    if any(v is None or v[0] <= 0 or v[1] <= 0 for v in legs.values()):
        return {"date": date, "skipped": "bad_quote"}, equity
    credit = legs["SP"][0] + legs["SC"][0] - legs["LP"][1] - legs["LC"][1]
    if credit <= 0:
        return {"date": date, "skipped": "nonpositive_credit"}, equity
    margin = (wing - credit) * 100
    if margin > equity:
        return {"date": date, "skipped": "margin"}, equity
    be_low, be_high = sp - credit, sc + credit
    max_loss = (wing - credit) * 100
    pnl, outcome = None, None
    for minute in sorted(minutes):
        if minute <= ENTRY:
            continue
        proxy = infer_atm(minutes[minute])
        if proxy is None:
            continue
        if proxy <= be_low or proxy >= be_high:
            pnl = -min(STOP_MULT * credit * 100, max_loss)
            outcome = "stop"
            break
        if minute >= EXIT_TIME:
            call_pay = min(max(proxy - sc, 0.0), wing)
            put_pay = min(max(sp - proxy, 0.0), wing)
            pnl = credit * 100 - (call_pay + put_pay) * 100
            outcome = "expiry"
            break
    if pnl is None:
        return {"date": date, "skipped": "no_exit"}, equity
    pnl -= FEE_PER_TRADE
    equity += pnl
    return {"date": date, "atm": atm, "short_put": sp, "short_call": sc,
            "credit": round(credit, 3), "pnl": round(pnl, 2),
            "outcome": outcome, "equity": round(equity, 2)}, equity


def half_stats(trades, equity_start, equity_end):
    done = [t for t in trades if "pnl" in t]
    wins = [t for t in done if t["pnl"] > 0]
    losses = [t for t in done if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    return {
        "n_trades": len(done),
        "n_skipped": len(trades) - len(done),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(done), 3) if done else None,
        "pnl_sum": round(sum(t["pnl"] for t in done), 2),
        "profit_factor": round(gw / gl, 2) if gl else None,
        "equity_start": round(equity_start, 2),
        "equity_end": round(equity_end, 2),
    }


def full_stats(curve, trades):
    done = [t for t in trades if "pnl" in t]
    wins = [t for t in done if t["pnl"] > 0]
    losses = [t for t in done if t["pnl"] <= 0]
    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak else 0)
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    return {
        "n_trades": len(done),
        "n_skipped": len(trades) - len(done),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(done), 3) if done else None,
        "final_equity": round(curve[-1], 2),
        "total_return": round(curve[-1] / START_EQUITY - 1, 3),
        "max_drawdown": round(max_dd, 3),
        "profit_factor": round(gw / gl, 2) if gl else None,
    }


def main():
    csvs = sorted(DATA_DIR.glob("opra-pillar-*.cbbo-1m.csv"))
    if not csvs:
        print("no csv files found", file=sys.stderr)
        return 2
    # state per parameter group
    state = {}
    for d, w in PARAMS:
        state[(d, w)] = {"equity": START_EQUITY, "curve": [START_EQUITY],
                         "records": [], "days": []}
    day_dates = []
    for path in csvs:
        days = load_cbbo_csv(str(path))
        dates = sorted(days)
        if len(dates) != 1:
            print(f"WARN {path.name}: loaded {len(dates)} dates {dates}",
                  file=sys.stderr)
        for date in dates:
            day_dates.append(date)
            minutes = days[date]
            for (d, w), st in state.items():
                rec, eq = run_one_day(date, minutes, d, w, st["equity"])
                st["equity"] = eq
                st["curve"].append(eq)
                st["days"].append(date)
                if rec is not None:
                    st["records"].append(rec)
            print(f"done {date} ({path.name})", flush=True)
        del days
        gc.collect()

    # validation against the previously verified 2026-10-28 single day
    val = [t for t in state[(30, 10)]["records"]
           if t.get("date") == "2026-10-28" and "pnl" in t]
    if not val or val[0]["atm"] != 7730.0 or val[0]["credit"] != 4.50 \
            or val[0]["pnl"] != 444.80 or val[0]["outcome"] != "expiry":
        print(f"VALIDATION FAILED: 2026-10-28 dist30/wing10 = {val}",
              file=sys.stderr)
        return 3

    n = len(day_dates)
    half = n // 2
    first_days = set(day_dates[:half])
    out = {
        "method": "src/backtest_real.py per-day logic, day-by-day load, "
                  "equity carried forward per parameter group; "
                  "ATM is the chain-inferred proxy, not observed SPX spot",
        "source": "Databento OPRA.PILLAR SPXW CBBO-1m, batch "
                  "OPRA-20261030-TTQY868VHE, local csv files only; "
                  "no download, no fee in this run",
        "params": {"distance": [20, 30, 50], "wing": [10, 20],
                   "stop_mult": STOP_MULT, "entry_et": ENTRY,
                   "exit_et": EXIT_TIME, "fee_per_trade": FEE_PER_TRADE,
                   "contracts": 1, "start_equity": START_EQUITY},
        "days": day_dates,
        "n_days": n,
        "first_half_days": day_dates[:half],
        "second_half_days": day_dates[half:],
        "note": "n=22 trading days only. No annualisation. "
                "This does NOT claim to reproduce any GFM curve.",
        "groups": {},
    }
    for (d, w), st in state.items():
        recs = st["records"]
        first = [t for t in recs if t["date"] in first_days]
        second = [t for t in recs if t["date"] not in first_days]
        # equity at half boundary: curve index = half (curve[0] is start)
        eq_half = st["curve"][half]
        out["groups"][f"distance_{d}_wing_{w}"] = {
            "full": full_stats(st["curve"], recs),
            "first_half": half_stats(first, START_EQUITY, eq_half),
            "second_half": half_stats(second, eq_half, st["curve"][-1]),
            "trades": recs,
        }
        print(f"dist {d} wing {w} full={out['groups'][f'distance_{d}_wing_{w}']['full']}",
              flush=True)

    out_path = Path(__file__).resolve().parent.parent / "results" / "backtest_real_sweep.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

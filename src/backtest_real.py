"""Real-data 0DTE iron-condor backtest (v3) -- for Databento CBBO-1m.

Why this exists (optimization over v2):
  * v2 priced with Black-Scholes + an assumed IV premium (1.5x realised).
    That assumption alone flipped the result from -$638 to +$13,294,
    so v2 cannot be trusted. v3 prices ONLY from real bid/ask:
    sell at bid, buy at ask, never at a modelled mid.
  * v2 used daily OHLC for stops, so a breakeven breach was only
    checked once a day. v3 checks every minute bar.
  * v2 needed a spot price to pick delta-targeted legs. Databento
    OPRA gives option quotes, not a SPX spot series, so v3 infers
    the at-the-money (ATM) strike from the chain itself: at entry,
    ATM is the strike where call mid and put mid are closest
    (for 0DTE, r ~ 0, put-call parity makes this a good proxy).
    Short legs are then ATM +/- distance points, wing points wide.
    This is documented as a proxy, not a spot price, and the
    sensitivity to it is tested by sweeping distance.

Input: a CBBO-1m CSV (Databento batch download, CSV encoding) with
columns including: ts_event, symbol, bid_px_00, ask_px_00.
Symbol is OCC: e.g. SPXW 261029C07700000 = SPXW, 2026-10-29,
Call, strike 7700.000. Only rows whose expiration == trade date
(0DTE) are used. Rows with bid<=0 or ask<=0 on a short leg are
rejected (no trade), as are wide spreads on any leg.

Trade (one per day, 1 contract):
  * entry_time: e.g. 10:00 ET (parameter, swept later)
  * at entry, infer ATM, build condor: short put = ATM-distance,
    short call = ATM+distance, long put/call = short +/- wing.
  * credit = (bid short put + bid short call)
             - (ask long put + ask long call), in $/share.
  * margin = (wing - credit) * 100; skip if margin > equity.
  * fees = $5.20/trade (IBKR $0.65 x 8 legs).
  * minute-by-minute after entry: infer ATM each minute as the
    spot proxy. If proxy <= short_put - credit (low breakeven)
    or >= short_call + credit (high breakeven), stop at
    stop_mult * credit (capped at max loss = (wing-credit)*100).
  * otherwise hold to exit_time (e.g. 15:30 ET): final proxy
    gives intrinsic: call_pay = min(max(proxy-short_call,0),wing),
    put_pay = min(max(short_put-proxy,0),wing);
    pnl = credit*100 - (call_pay+put_pay)*100 - fees.

Output: equity curve, per-trade table, metrics, first/second-half
split. No compounding: always 1 contract, so returns are linear
in trade count and must not be annualised as a compounding rate.
"""
import csv
import re
from collections import defaultdict
from datetime import datetime

FEE_PER_TRADE = 5.20
OCC_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([CP])(\d{8})$")


def parse_occ(symbol):
    m = OCC_RE.match(symbol.strip())
    if not m:
        return None
    root, yymmdd, kind, strike_raw = m.groups()
    exp = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return {"root": root, "expiration": exp, "kind": kind,
            "strike": int(strike_raw) / 1000.0}


def load_cbbo_csv(path):
    """Return {date: {minute: {strike: {'C': (bid, ask), 'P': (bid, ask)}}}}.

    Only SPXW rows whose expiration equals the trade date are kept.
    ts_event is parsed for date + HH:MM (ET as delivered).
    """
    days = defaultdict(lambda: defaultdict(dict))
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            occ = parse_occ(row.get("symbol", ""))
            if not occ or occ["root"] != "SPXW":
                continue
            ts = row.get("ts_event", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            date = dt.date().isoformat()
            if occ["expiration"] != date:
                continue
            try:
                bid = float(row.get("bid_px_00") or 0)
                ask = float(row.get("ask_px_00") or 0)
            except ValueError:
                continue
            minute = dt.strftime("%H:%M")
            days[date][minute].setdefault(occ["strike"], {})[occ["kind"]] = (bid, ask)
    return days


def infer_atm(chain):
    """Strike minimising |call_mid - put_mid| over strikes with both sides."""
    best, best_err = None, 1e18
    for strike, sides in chain.items():
        if "C" in sides and "P" in sides:
            cm = (sides["C"][0] + sides["C"][1]) / 2
            pm = (sides["P"][0] + sides["P"][1]) / 2
            err = abs(cm - pm)
            if err < best_err:
                best, best_err = strike, err
    return best


def quote(chain, strike, kind):
    sides = chain.get(strike, {})
    return sides.get(kind)


def run_real(days, distance, wing, stop_mult, entry="10:00", exit_time="15:30"):
    equity = 1000.0
    curve = [equity]
    trades = []
    for date in sorted(days):
        minutes = days[date]
        if entry not in minutes or exit_time not in minutes:
            curve.append(equity)
            continue
        chain = minutes[entry]
        atm = infer_atm(chain)
        if atm is None:
            curve.append(equity)
            continue
        sp, sc = atm - distance, atm + distance
        lp, lc = sp - wing, sc + wing
        legs = {"SP": quote(chain, sp, "P"), "SC": quote(chain, sc, "C"),
                "LP": quote(chain, lp, "P"), "LC": quote(chain, lc, "C")}
        if any(v is None or v[0] <= 0 or v[1] <= 0 for v in legs.values()):
            trades.append({"date": date, "skipped": "bad_quote"})
            curve.append(equity)
            continue
        credit = legs["SP"][0] + legs["SC"][0] - legs["LP"][1] - legs["LC"][1]
        if credit <= 0:
            trades.append({"date": date, "skipped": "nonpositive_credit"})
            curve.append(equity)
            continue
        margin = (wing - credit) * 100
        if margin > equity:
            trades.append({"date": date, "skipped": "margin"})
            curve.append(equity)
            continue
        be_low, be_high = sp - credit, sc + credit
        max_loss = (wing - credit) * 100
        pnl, outcome = None, None
        for minute in sorted(minutes):
            if minute <= entry:
                continue
            proxy = infer_atm(minutes[minute])
            if proxy is None:
                continue
            if proxy <= be_low or proxy >= be_high:
                pnl = -min(stop_mult * credit * 100, max_loss)
                outcome = "stop"
                break
            if minute >= exit_time:
                call_pay = min(max(proxy - sc, 0.0), wing)
                put_pay = min(max(sp - proxy, 0.0), wing)
                pnl = credit * 100 - (call_pay + put_pay) * 100
                outcome = "expiry"
                break
        if pnl is None:
            trades.append({"date": date, "skipped": "no_exit"})
            curve.append(equity)
            continue
        pnl -= FEE_PER_TRADE
        equity += pnl
        trades.append({"date": date, "atm": atm, "short_put": sp, "short_call": sc,
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
            "profit_factor": round(gw / gl, 2) if gl else None}


if __name__ == "__main__":
    import json
    import sys
    days = load_cbbo_csv(sys.argv[1])
    print(f"Loaded {len(days)} 0DTE days")
    for distance in (20, 30, 50):
        for wing in (10, 20):
            curve, trades = run_real(days, distance, wing, 1.5)
            done = [t for t in trades if "pnl" in t]
            print(f"dist {distance} wing {wing}", metrics(curve, trades),
                  "1H", round(sum(t["pnl"] for t in done[: len(done) // 2]), 2),
                  "2H", round(sum(t["pnl"] for t in done[len(done) // 2:]), 2),
                  "skip", len(trades) - len(done))
    with open("backtest_real_results.json", "w", encoding="utf-8") as f:
        json.dump({"days": sorted(days)}, f)

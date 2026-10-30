"""Free CBOE delayed-quotes client.

No API key needed. Data is delayed ~15 minutes (CBOE delayed quotes).
Symbols: SPY, QQQ use their own symbol; SPX index options use "_SPX".
"""
import re
import requests

BASE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Map the user-facing symbol to the CBOE symbol.
CBOE_SYMBOL = {"SPY": "SPY", "QQQ": "QQQ", "SPX": "_SPX"}

_OPT_RE = re.compile(r"^([A-Z_]+)(\d{6})([CP])(\d{8})$")


def parse_option_symbol(sym):
    """'SPY261030C00769000' -> ('SPY', '2026-10-30', 'C', 769.0)."""
    m = _OPT_RE.match(sym)
    if not m:
        return None
    underlying, ymd, kind, strike_raw = m.groups()
    date = "20%s-%s-%s" % (ymd[0:2], ymd[2:4], ymd[4:6])
    return underlying, date, kind, int(strike_raw) / 1000.0


def fetch_chain(symbol):
    """Return (meta, options) for one underlying.

    meta: dict with current_price, prev_day_close, timestamp, symbol.
    options: list of dicts with expiry, kind, strike, gamma, open_interest,
    volume, iv, delta, bid, ask.
    """
    cboe_sym = CBOE_SYMBOL.get(symbol.upper(), symbol.upper())
    resp = requests.get(BASE.format(cboe_sym), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    data = payload["data"]
    out = []
    for o in data["options"]:
        parsed = parse_option_symbol(o["option"])
        if not parsed:
            continue
        _, expiry, kind, strike = parsed
        out.append({
            "expiry": expiry,
            "kind": kind,
            "strike": strike,
            "gamma": float(o.get("gamma") or 0.0),
            "delta": float(o.get("delta") or 0.0),
            "iv": float(o.get("iv") or 0.0),
            "open_interest": float(o.get("open_interest") or 0.0),
            "volume": float(o.get("volume") or 0.0),
            "bid": float(o.get("bid") or 0.0),
            "ask": float(o.get("ask") or 0.0),
        })
    meta = {
        "symbol": symbol.upper(),
        "current_price": data.get("current_price"),
        "prev_day_close": data.get("prev_day_close"),
        "timestamp": payload.get("timestamp"),
    }
    return meta, out


def expiries(options):
    return sorted({o["expiry"] for o in options})

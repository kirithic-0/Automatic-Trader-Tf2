"""Stage 6, step 3.1 -- the listings the agent may trade with, from one book.

The agent is taker-only (handoff.md §0): it buys from a sell listing and sells into a buy
order, and only with a counterparty that accepts automatically and is trusted. A listing
is **tradable** when, in this order (the first failing check is its `reason`):

  1. it is the item itself -- not strange / spelled / parted / painted (engine.book rule 1,
     a different good), priced (`total_keys`), and its account's best listing on the side
     (one account, one listing: rules 2-3);
  2. it is inside the fence (engine.book rule 4, the same fence every read uses) -- a
     listing the fence drops is a misprice, a flier or a bait, never a trade;
  3. `listingType == "bot"` and `dealType == "instant"`: it accepts automatically;
  4. its account is not in engine.book.UNTRUSTED (any level: its trades do not go through);
  5. its account's trade-offer URL is known (`trade_url` table, or the book server's
     `tradeUrls`): an offer to a non-friend needs the token;
  6. a sell listing's id is `440_<assetid>`: the exact item the buy offer requests;
  7. a sell listing's price is whole keys + whole scrap (`payable`): metal pays it exactly.

    sells = tradable_sells(book, trade_urls)     # cheapest first: what to buy from
    buys = tradable_buys(book, trade_urls)       # highest first: what to sell into
    rows = explain(book, trade_urls)             # every listing with its reason (the log)

The rules themselves live in engine.book; this module only adds the agent's own checks
(3-6) on top, so the fence can never drift from what the model was trained on.
"""
import re

from engine import book as eb

_ASSET = re.compile(r"^440_(\d+)$")


def _sid(o):
    return str(o.get("steamId")) if o.get("steamId") is not None else None


def asset_id(listing):
    """The asset id a sell listing offers (`440_<assetid>`), else None."""
    m = _ASSET.match(str(listing.get("id") or ""))
    return m.group(1) if m else None


def payable(price):
    """Whole keys + whole scrap: what metal can pay exactly. bp.tf writes n scrap as
    n x .11 ref (7 scrap = .77); ~3% of listed prices are not (5.38 ref), 2026-10-19.
    The agent's payer (agent/lib/currency.js toUnits) applies the same rule."""
    keys, ref = price.get("keys") or 0, price.get("ref") or 0
    cents = round((ref - int(ref)) * 100)
    return float(keys).is_integer() and keys >= 0 and ref >= 0 and cents % 11 == 0


def _row(side, p, o, url):
    price = o.get("price") or {}
    return {"side": side, "total_keys": p, "keys": price.get("keys"), "ref": price.get("ref"),
            "steam_id": _sid(o), "user": o.get("user"), "listing_id": o.get("id"),
            "asset_id": asset_id(o) if side == "sell" else None, "trade_url": url,
            "listing_type": o.get("listingType"), "deal_type": o.get("dealType"),
            "premium": bool(o.get("premium")), "listed_at": o.get("listedAt")}


def _side(book, side, trade_urls):
    """[(row, reason)] for every listing of one side, best price first; reason None =
    tradable."""
    raw = book.get("sellOrders" if side == "sell" else "buyOrders") or []
    valid = eb.valid_sells(book) if side == "sell" else eb.valid_buys(book)
    fenced = eb.fence_sells(valid) if side == "sell" else eb.fence_buys(valid)
    kept = {id(o) for _, o in valid}
    inside = {id(o) for _, o in fenced or []}
    urls = trade_urls or {}

    out = []
    for o in raw:
        p = (o.get("price") or {}).get("total_keys")
        sid = _sid(o)
        if any(o.get(f) for f in eb._DIFFERENT_GOOD):
            why = "different good (" + ",".join(f for f in eb._DIFFERENT_GOOD if o.get(f)) + ")"
        elif p is None:
            why = "no price"
        elif sid in eb.DROPPED:
            why = "untrusted (dropped)"
        elif id(o) not in kept:
            why = "not the account's best listing"
        elif fenced is None:
            why = f"no fence (< {eb.MIN_VALID} valid listings)"
        elif id(o) not in inside:
            why = "outside the fence"
        elif o.get("listingType") != "bot" or o.get("dealType") != "instant":
            why = f"not bot+instant ({o.get('listingType')}/{o.get('dealType')})"
        elif sid in eb.UNSTABLE_FILLS:
            why = "untrusted"
        elif not urls.get(sid):
            why = "no trade URL"
        elif side == "sell" and asset_id(o) is None:
            why = "no asset id"
        elif side == "sell" and not payable(o.get("price") or {}):
            why = "price not whole keys + scrap"
        else:
            why = None
        out.append((_row(side, p, o, urls.get(sid)), why))
    best_first = side == "buy"
    out.sort(key=lambda r: (r[0]["total_keys"] is None,
                            -(r[0]["total_keys"] or 0) if best_first else (r[0]["total_keys"] or 0)))
    return out


def tradable_sells(book, trade_urls):
    """Sell listings the agent may buy from, cheapest first."""
    return [r for r, why in _side(book, "sell", trade_urls) if why is None]


def tradable_buys(book, trade_urls):
    """Buy orders the agent may sell into, highest first."""
    return [r for r, why in _side(book, "buy", trade_urls) if why is None]


def explain(book, trade_urls):
    """Every listing, both sides, with `reason` (None = tradable): the agent's log of why
    it took or skipped each one."""
    return [dict(r, reason=why) for side in ("sell", "buy") for r, why in _side(book, side, trade_urls)]


def trade_urls(con, steam_ids=None):
    """{steam_id: url} from the `trade_url` table (trading_app.db), optionally only these."""
    if steam_ids is None:
        return dict(con.execute("SELECT steam_id, url FROM trade_url"))
    ids = [str(s) for s in steam_ids]
    if not ids:
        return {}
    q = "SELECT steam_id, url FROM trade_url WHERE steam_id IN (%s)" % ",".join("?" * len(ids))
    return dict(con.execute(q, ids))

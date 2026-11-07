"""Stage 6, step 3.5 -- re-check one fresh book right before an offer goes out (gap 4).

A pick is made on the hourly pass; by the time the agent acts, the listing may be gone,
repriced, or the book may have moved (the four-bot fleet deletes and re-lists together,
handoff "Open questions"). So every offer is decided again on a book fetched seconds
earlier (the VM's `GET /book`, sync/fetch_book.py), with the same rules everything else
reads by (engine.book, engine.listings):

  buy  -- the book is fresh (<= MAX_AGE_S old); it is still in the model's universe (the
          fenced ask at most MAX_SPREAD over the fenced bid); the cheapest tradable sell
          costs at most the pick's ask1 (the price exp_ret was computed at: 7.6% of books'
          tradable seller is dearer, median +3.45%, data-model.md) -> buy from it;
  sell -- the book is fresh; the best tradable bid is at least `min_keys` (the
          take-profit price, or 0 at the horizon) and at most MAX_JUMP x the entry ask
          (above that it is a misread, never a sale) -> sell into it.

Each decision also lists `candidates`: the listings the agent may try in turn when a bot
does not answer (user, 2026-10-26: the next bot "if it is within a range") -- buys, every
tradable sell at or below the pick's ask1; take-profit sales, every tradable bid still >=
`min_keys`; horizon sales (min 0), every bid within HORIZON_RANGE of the best. Best first.

    d = recheck_buy(pick, book)              # {"ok", "reason", "listing", "watch_id", ...}
    d = recheck_sell(position, book, min_keys)
    d = recheck_key(book)                    # the key's own book: buy / sell one for metal

recheck_key is the agent's key<->metal rebalance (user, 2026-10-30; agent/lib/convert.js): a
key bought from a tradable sell priced in metal only, at most MAX_KEY_GAP_REF over the best
tradable bid or over the cheapest sell, whichever is higher; sold into a tradable bid at most
MAX_KEY_GAP_REF under the cheapest sell or under the best bid, whichever is lower. The gap
between them is about 1 scrap (64.66 bid / 64.77 ask on the 2026-10-30 14:38 book, with two
sells at 64.11 under the bid), so the guard only stops a misread. The book is often crossed --
63.66 ask / 64.11 bid 2026-11-01 12:41, and the 63.66 sellers ignored three cycles of offers --
so the window around the cheapest sell itself (user, 2026-11-01) lets the agent reach the
sellers just above it; the agent tries every one (agent/lib/convert.js).

`book` is the book server's answer: sellOrders, buyOrders, tradeUrls, scraped_at, watch_id.
"""
import pandas as pd

from engine import book as eb
from engine import listings as el
from engine.forecast import MAX_JUMP, MAX_SPREAD

MAX_AGE_S = 120.0          # a book older than this is not a re-check
KEY = "Mann Co. Supply Crate Key"
MAX_KEY_GAP_REF = 0.33     # a key trade is at most 3 scrap worse than the best of either side
MAX_KEY_OFF = 0.02         # ... and both sides within 2% of the book's key price (key_price_ref)
HORIZON_RANGE = 0.02       # a horizon sale may go to the next bids down to 2% under the best


def _age_s(book, now):
    """Seconds since the book was scraped; inf without a time."""
    if not book.get("scraped_at"):
        return float("inf")
    t = pd.Timestamp(book["scraped_at"])
    return (now - (t.tz_localize("UTC") if t.tzinfo is None else t)).total_seconds()


def _base(book, now, max_age_s):
    """The parts every decision carries, plus a reason if the book itself is unusable."""
    now = now or pd.Timestamp.now(tz="UTC")
    age = _age_s(book, now)
    out = {"ok": False, "reason": None, "listing": None, "watch_id": book.get("watch_id"),
           "scraped_at": book.get("scraped_at"), "age_s": round(age, 1)}
    if age > max_age_s:
        out["reason"] = f"book is {age:.0f} s old (> {max_age_s:g})"
    return out


def recheck_buy(pick, book, now=None, max_age_s=MAX_AGE_S, max_spread=MAX_SPREAD):
    """Buy now? `pick` = one row of `engine picks --json` (needs ask1)."""
    out = _base(book, now, max_age_s)
    if out["reason"]:
        return out
    r = eb.read_book(book)
    out["fresh"] = {"ask1": r.ask1, "bid1": r.bid1, "bid1_fill": r.bid1_fill}
    if r.ask1 != r.ask1 or r.bid1 != r.bid1:
        out["reason"] = "no fenced ask or bid now"
        return out
    spread = (r.ask1 - r.bid1) / r.bid1
    if not (-1e-9 <= spread <= max_spread):
        out["reason"] = f"left the universe: spread {spread:+.1%} (0 to {max_spread:.0%})"
        return out
    sells = el.tradable_sells(book, book.get("tradeUrls") or {})
    if not sells:
        out["reason"] = "no tradable sell listing now"
        return out
    best = sells[0]
    if best["total_keys"] > pick["ask1"] + 1e-9:
        out["reason"] = (f"dearer than the pick: {best['total_keys']:.4f} > ask1 {pick['ask1']:.4f} "
                         f"({best['total_keys'] / pick['ask1'] - 1:+.2%})")
        out["listing"] = best
        return out
    out.update(ok=True, listing=best,
               candidates=[s for s in sells if s["total_keys"] <= pick["ask1"] + 1e-9])
    return out


def recheck_sell(position, book, min_keys=0.0, now=None, max_age_s=MAX_AGE_S, max_jump=MAX_JUMP,
                 horizon_range=HORIZON_RANGE):
    """Sell now? `position` needs `ask1` (the model's entry ask: MAX_JUMP is measured
    against it, as in the labels) or else `entry_keys`; `min_keys` = the take-profit price,
    or 0 at the horizon."""
    out = _base(book, now, max_age_s)
    if out["reason"]:
        return out
    entry = position.get("ask1") or position.get("entry_keys")
    buys = el.tradable_buys(book, book.get("tradeUrls") or {})
    sane = [b for b in buys if b["total_keys"] <= max_jump * entry]
    if not sane:
        out["reason"] = "no tradable bid now" if not buys else f"every tradable bid is above {max_jump:g}x entry (a misread)"
        return out
    best = sane[0]
    out["listing"] = best
    if best["total_keys"] < min_keys - 1e-9:
        out["reason"] = f"best tradable bid {best['total_keys']:.4f} < {min_keys:.4f}"
        return out
    floor = min_keys if min_keys > 0 else best["total_keys"] * (1 - horizon_range)
    out.update(ok=True, candidates=[b for b in sane if b["total_keys"] >= floor - 1e-9])
    return out


def recheck_key(book, now=None, max_age_s=MAX_AGE_S, max_gap_ref=MAX_KEY_GAP_REF, max_off=MAX_KEY_OFF):
    """Buy or sell one key for metal now? `book` = the key's own (KEY, quality 6). Both
    sides at once: `buy` = sells to buy a key from (cheapest first), `sell` = bids to sell
    one into (highest first), each priced in metal only (0 keys); `ask_ref` / `bid_ref` the
    best of each, `key_ref` their mid (what a key is worth in metal now). A best ask or bid
    more than `max_off` from the book's own key price (bp.tf's, `key_price_ref`) is a misread
    or a bait: no trade at all."""
    out = _base(book, now, max_age_s)
    if out["reason"]:
        return out
    urls = book.get("tradeUrls") or {}
    metal = lambda r: not r["keys"] and (r["ref"] or 0) > 0
    sells = [s for s in el.tradable_sells(book, urls) if metal(s)]
    buys = [b for b in el.tradable_buys(book, urls) if metal(b)]
    if not sells or not buys:
        out["reason"] = f"no tradable key {'sell' if not sells else 'bid'} in metal now"
        return out
    ask, bid = sells[0]["ref"], buys[0]["ref"]
    ref = book.get("key_price_ref")
    if ref and max(abs(ask / ref - 1), abs(bid / ref - 1)) > max_off:
        out["reason"] = f"key ask {ask} / bid {bid} ref: more than {max_off:.0%} off the key price {ref}"
        return out
    out.update(ok=True, ask_ref=ask, bid_ref=bid, key_ref=round((ask + bid) / 2, 4),
               buy=[s for s in sells if s["ref"] <= max(ask, bid) + max_gap_ref + 1e-9],
               sell=[b for b in buys if b["ref"] >= min(ask, bid) - max_gap_ref - 1e-9])
    return out

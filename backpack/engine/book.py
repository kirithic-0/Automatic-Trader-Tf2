"""The read rules: one snapshot's `book_json` -> its clean prices and book shape.

This module is the single home of the rules that turn a raw classifieds book
into numbers (docs/data-model.md, "Reading the valid book"). In order:

  1. flag filter -- drop strange / spelled / parted / painted (different goods), and
     every listing of a level-2 untrusted account (UNTRUSTED);
  2. dedupe by steamId, keep the best price per account (one bot, one vote);
     buys keep the highest, sells the lowest;
  3. drop null `total_keys`;
  4. fence around the book median m (needs >= MIN_VALID listings, else missing),
     m taken over the FENCE_DEPTH best valid listings only (the depth the rules were
     tuned on: classifieds books stored the top 7 a side; app books carry up to 30,
     whose median sits far deeper -- 2026-10-17), both sides modulated by trust = (listingType == "bot") + premium in {0, 1, 2}:
       buy  -- keep BUY_K_LO*m <= bid <= BUY_K_HI[trust]*m; strips premium/combo/
               rival-provider fliers and untrusted high bids above the cluster;
       sell -- keep ASK_K_LO[trust]*m <= ask <= ASK_K_HI*m; untrusted lowballs
               drop, genuine cheap bot sells survive.

Params were tuned on Strange (11) + Unique (6); see the buy-order-outlier-fence
decision log. Bump RULES_VERSION whenever a rule or param changes -- the panel
cache is keyed on it and rebuilds itself.

Beside the rules, one read-out for trading: `bid1_fill`, the best fenced bid from an
account whose trades go through -- any account not in UNTRUSTED. It is what a sale
gets; a level-1 account stays in the fence and every other read.
"""
import json
import zlib
from dataclasses import dataclass, fields

RULES_VERSION = 4                  # 2: stricter sell fence; 3: trust-modulated buy fence;
                                   # 4: fence median + bid_med/ask_med over the top FENCE_DEPTH

BUY_K_LO = 0.40
# Buy high cut by trust 0/1/2, from a flat 1.35 on 2026-09-24: a trust-0 negotiate
# bid at 1.17x median (above the best ask) set Soldier's Stash's bid1 and faked a
# new-high step. Trust-0 bid1s sit at a median 1.045x; trust-1 at 1.011x.
BUY_K_HI = (1.02, 1.10, 1.20)
# Sell fence, tightened 2026-09-24 from (0.75, 0.60, 0.35) / 3.0: a trust-0 ask at
# 0.76x median (a negotiate-only human) was setting ask1 for days under a bot
# cluster, and asks up to 3x median were inflating ask5. Trust-0 asks set ask1 in
# 12.6% of books; 0.95 changes ask1 in 8.9% of them (last 24 passes).
ASK_K_LO = (0.95, 0.60, 0.35)      # indexed by trust 0/1/2
ASK_K_HI = 1.5
TOP_N = 5
MIN_VALID = 3
# The fence median and the level (bid_med / ask_med) read only the best FENCE_DEPTH valid
# listings a side. Every param above was tuned on classifieds books stored top 7 a side
# (data-model.md); on a 30-deep app book the median of all valid bids sits near the 15th,
# and BUY_K_HI[0] = 1.02x of that cut real top bids. On a <= 7-deep book this is a no-op.
FENCE_DEPTH = 7
NEAR_TOP = 0.01                    # "near the top" = within 1% of the best bid

# Untrusted accounts (user, 2026-09-26): only the accounts judged untrustworthy that the
# fence does NOT already catch -- they get past the median rule -- each with how much, not
# a score for every account. `python -m engine untrusted` (engine/untrusted.py)
# measures candidates; an account goes on the list only when the user confirms it.
#   1 = never a fill: stays in the book and the fence, but a trade with it does not go
#       through, so bid1_fill skips it (the forecast never sells into it);
#   2 = out of the book: dropped with the different goods (rule 1), before the median and
#       the fence -- and so never a fill either.
# Every other account's order fills instantly (the fill rule). data-model.md, "Untrusted accounts".
# Public copy: the list ships empty. The live engine held two accounts here (both level 1,
# "unstable trades"); their Steam IDs are left out, so a backtest run from this repository
# can differ slightly from the reported numbers.
UNTRUSTED = {}
UNSTABLE_FILLS = frozenset(UNTRUSTED)                                  # level >= 1: never a fill
DROPPED = frozenset(s for s, lv in UNTRUSTED.items() if lv >= 2)     # level 2: out of the book
# the cache key of every book read (panel, bot observations): the rules plus the list
READ_KEY = (f"{RULES_VERSION}_u"
            f"{zlib.crc32(','.join(f'{s}:{lv}' for s, lv in sorted(UNTRUSTED.items())).encode()):08x}")

_DIFFERENT_GOOD = ("strange", "spelled", "parted", "painted")
NaN = float("nan")


def median(xs):
    s = sorted(xs)
    m = len(s)
    if m == 0:
        return None
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2


def _trust(o):
    return (1 if o.get("listingType") == "bot" else 0) + (1 if o.get("premium") else 0)


def _valid(orders, keep_high):
    """Rules 1-3 over one side: [(total_keys, listing)] deduped by steamId."""
    best = {}
    for o in orders or []:
        if any(o.get(f) for f in _DIFFERENT_GOOD):
            continue
        tk = (o.get("price") or {}).get("total_keys")
        if tk is None:
            continue
        sid = o.get("steamId")
        if sid is not None and str(sid) in DROPPED:
            continue
        kid = sid if sid is not None else id(o)
        cur = best.get(kid)
        if cur is None or (tk > cur[0] if keep_high else tk < cur[0]):
            best[kid] = (tk, o)
    return list(best.values())


def valid_buys(book):
    """Valid buy listings, highest first: [(total_keys, listing)]."""
    return sorted(_valid(book.get("buyOrders"), keep_high=True),
                  key=lambda t: t[0], reverse=True)


def valid_sells(book):
    """Valid sell listings, lowest first: [(total_keys, listing)]."""
    return sorted(_valid(book.get("sellOrders"), keep_high=False),
                  key=lambda t: t[0])


def fence_median(vals, depth=FENCE_DEPTH):
    """Rule 4's centre: the median of the `depth` best valid listings (vals sorted best
    first, as valid_buys / valid_sells return them); None below MIN_VALID listings.
    depth=None takes every valid listing (the pre-2026-10-17 rule; the dashboard's
    comparison)."""
    if len(vals) < MIN_VALID:
        return None
    m = median([p for p, _ in (vals if depth is None else vals[:depth])])
    return m if m and m > 0 else None


def fence_buys(buys, depth=FENCE_DEPTH):
    """Rule 4, buy side. Survivors highest first, or None if no median forms."""
    m = fence_median(buys, depth)
    if m is None:
        return None
    return [(p, o) for p, o in buys if BUY_K_LO * m <= p <= BUY_K_HI[_trust(o)] * m]


def fence_sells(sells, depth=FENCE_DEPTH):
    """Rule 4, sell side (trust-modulated). Survivors lowest first, or None."""
    m = fence_median(sells, depth)
    if m is None:
        return None
    return [(p, o) for p, o in sells
            if ASK_K_LO[_trust(o)] * m <= p <= ASK_K_HI * m]


def _mean(xs):
    return sum(xs) / len(xs) if xs else NaN


def _share(listings, pred):
    return sum(1 for _, o in listings if pred(o)) / len(listings) if listings else NaN


@dataclass(frozen=True)
class BookRead:
    """Everything the rules read off one book. NaN = not computable (missing,
    never zero). Prices are decimal keys."""
    bid1: float            # best fenced bid
    bid5: float            # mean of the up-to-5 highest fenced bids
    bid_disp: float        # (max-min)/median of those top-5 -- do they agree?
    ask1: float            # best (lowest) fenced ask
    ask5: float            # mean of the up-to-5 lowest fenced asks
    n_buy: int             # valid buys (rules 1-3), before the fence
    n_sell: int
    n_buy_fenced: int      # valid buys the fence dropped (contamination)
    n_sell_fenced: int
    gap2: float            # (best - 2nd best fenced bid) / best
    n_near_top: int        # fenced bids within NEAR_TOP of the best (incl. it)
    buy_bot_share: float   # of valid buys: listingType == "bot"
    buy_premium_share: float
    buy_instant_share: float  # dealType == "instant"
    top_bot: float         # 1.0 if the best fenced bid is a bot, else 0.0
    sell_bot_share: float
    bid_med: float         # median of the fenced bids among the top FENCE_DEPTH valid: the
                           #   buy-order level (engine.forecast)
    ask_med: float         # the same, asks
    ask1_bot: float        # lowest fenced ask from a bp.tf-badged bot (an instant buy)
    bid1_fill: float       # best fenced bid from an account not in UNTRUSTED: what a sale gets


BOOK_FIELDS = [f.name for f in fields(BookRead)]


def _parse(book_json):
    if isinstance(book_json, (bytes, memoryview)):
        raise TypeError("a zstd-compressed book: decode it with book_codec.Codec first "
                        "(trading_app.db stores books compressed since 2026-10-04)")
    if isinstance(book_json, str):
        try:
            return json.loads(book_json)
        except ValueError:
            return {}
    return book_json or {}


def read_book(book_json):
    """Apply the read rules to one snapshot's `book_json` (str or dict)."""
    book = _parse(book_json)
    buys, sells = valid_buys(book), valid_sells(book)
    fb, fs = fence_buys(buys), fence_sells(sells)
    top_b = {id(o) for _, o in buys[:FENCE_DEPTH]}
    top_s = {id(o) for _, o in sells[:FENCE_DEPTH]}
    lvl_b = [p for p, o in fb or [] if id(o) in top_b]
    lvl_s = [p for p, o in fs or [] if id(o) in top_s]

    bid1 = bid5 = bid_disp = gap2 = top_bot = bid1_fill = NaN
    n_near = 0
    if fb:
        top = [p for p, _ in fb[:TOP_N]]
        bid1, bid5 = top[0], _mean(top)
        med = median(top)
        bid_disp = (max(top) - min(top)) / med if med else NaN
        gap2 = (fb[0][0] - fb[1][0]) / fb[0][0] if len(fb) > 1 and fb[0][0] else NaN
        n_near = sum(1 for p, _ in fb if p >= bid1 * (1 - NEAR_TOP))
        top_bot = 1.0 if fb[0][1].get("listingType") == "bot" else 0.0
        bid1_fill = next((p for p, o in fb if str(o.get("steamId")) not in UNSTABLE_FILLS), NaN)

    ask1 = ask5 = ask1_bot = NaN
    if fs:
        low = [p for p, _ in fs[:TOP_N]]
        ask1, ask5 = low[0], _mean(low)
        ask1_bot = next((p for p, o in fs if o.get("listingType") == "bot"), NaN)

    return BookRead(
        bid1=bid1, bid5=bid5, bid_disp=bid_disp, ask1=ask1, ask5=ask5,
        n_buy=len(buys), n_sell=len(sells),
        n_buy_fenced=len(buys) - len(fb) if fb is not None else 0,
        n_sell_fenced=len(sells) - len(fs) if fs is not None else 0,
        gap2=gap2, n_near_top=n_near,
        buy_bot_share=_share(buys, lambda o: o.get("listingType") == "bot"),
        buy_premium_share=_share(buys, lambda o: bool(o.get("premium"))),
        buy_instant_share=_share(buys, lambda o: o.get("dealType") == "instant"),
        top_bot=top_bot,
        sell_bot_share=_share(sells, lambda o: o.get("listingType") == "bot"),
        bid_med=median(lvl_b) if lvl_b else NaN,
        ask_med=median(lvl_s) if lvl_s else NaN,
        ask1_bot=ask1_bot,
        bid1_fill=bid1_fill,
    )


def _explain_side(orders, valid, fenced, is_buy):
    """Every raw listing on one side with the rule verdict, via the SAME valid_*/
    fence_* calls read_book makes -- the explanation cannot drift from the read."""
    valid_ids = {id(o) for _, o in valid}
    kept = [] if fenced is None else fenced
    rank = {id(o): i for i, (_, o) in enumerate(kept)}
    m = fence_median(valid)
    m_all = fence_median(valid, None)
    top = {id(o) for _, o in valid[:FENCE_DEPTH]}
    rows = []
    for i, o in enumerate(orders or []):
        price = o.get("price") or {}
        tk = price.get("total_keys")
        trust = _trust(o)
        flag = next((f for f in _DIFFERENT_GOOD if o.get(f)), None)
        role, detail = "", ""
        if flag:
            reason = f"flag: {flag}"
        elif tk is None:
            reason = "null price"
        elif str(o.get("steamId")) in DROPPED:
            reason = "untrusted account"
            detail = "level 2: out of the book (engine.book.UNTRUSTED)"
        elif id(o) not in valid_ids:
            reason = "duplicate account"
            detail = f"this account's {'higher' if is_buy else 'lower'} listing is the one counted"
        elif fenced is None:
            reason = "no median"
            detail = f"only {len(valid)} valid listing(s); need {MIN_VALID}"
        elif id(o) in rank:
            reason = "kept"
            r = rank[id(o)]
            role = ("bid1" if is_buy else "ask1") if r == 0 else ("top5" if r < TOP_N else "")
        else:
            k_hi = BUY_K_HI[trust] if is_buy else ASK_K_HI
            if tk > k_hi * m:
                reason = "fence: too high"
                detail = (f"{tk / m:.2f}x median > {k_hi}x"
                          + (f" (trust {trust})" if is_buy else ""))
            else:
                reason = "fence: too low"
                k = BUY_K_LO if is_buy else ASK_K_LO[trust]
                detail = (f"{tk / m:.2f}x median < {k}x"
                          + ("" if is_buy else f" (trust {trust})"))
        rows.append({
            "i": i, "user": o.get("user"), "steamId": o.get("steamId"),
            "raw": price.get("raw"), "keys": price.get("keys"), "ref": price.get("ref"),
            "tk": tk, "bot": o.get("listingType") == "bot", "premium": bool(o.get("premium")),
            "deal": o.get("dealType"), "trust": trust,
            "flags": [f for f in _DIFFERENT_GOOD if o.get(f)],
            "reason": reason, "detail": detail, "role": role,
            "untrusted": UNTRUSTED.get(str(o.get("steamId")), 0),
            "in_depth": id(o) in top,
        })
    rows.sort(key=lambda r: (r["tk"] is None, -(r["tk"] or 0) if is_buy else (r["tk"] or 0)))
    summary = {"n_raw": len(orders or []), "n_valid": len(valid), "n_kept": len(kept),
               "median": m, "median_all": m_all, "depth": FENCE_DEPTH}
    if m is not None:
        summary["fence"] = ({"lo": BUY_K_LO * m, "hi_by_trust": [k * m for k in BUY_K_HI]}
                            if is_buy else
                            {"lo_by_trust": [k * m for k in ASK_K_LO], "hi": ASK_K_HI * m})
    return rows, summary


def explain_book(book_json):
    """read_book, shown its working: every raw buy and sell listing with the rule
    that kept or dropped it (and why), plus the resulting prices. For inspection
    -- the dashboard's order-book view -- not for bulk reads."""
    book = _parse(book_json)
    buys, sells = valid_buys(book), valid_sells(book)
    fb, fs = fence_buys(buys), fence_sells(sells)
    brows, bsum = _explain_side(book.get("buyOrders"), buys, fb, True)
    srows, ssum = _explain_side(book.get("sellOrders"), sells, fs, False)
    r = read_book(book)
    prices = {k: (None if v != v else v) for k, v in
              (("bid1", r.bid1), ("bid5", r.bid5), ("ask1", r.ask1), ("ask5", r.ask5))}
    return {"buys": brows, "sells": srows, "buy": bsum, "sell": ssum, "prices": prices}

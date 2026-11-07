# -*- coding: utf-8 -*-
"""Offline test for engine.book -- the read rules, through read_book() and explain_book().

Pins each rule on a hand-built book: the flag filter, steamId dedupe (buys keep
the highest, sells the lowest), the median-ratio buy fence, the trust-modulated
sell fence, "missing, not zero" below MIN_VALID, the book-shape fields, and
explain_book's per-listing verdicts agreeing with read_book.

    python test_engine_book.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import json
import math
import sys

from engine import book as eb  # noqa: E402
# The public copy ships UNTRUSTED empty: test the rule on two made-up accounts.
eb.UNTRUSTED = {"1000000001": 1, "1000000002": 1}
eb.UNSTABLE_FILLS = frozenset(eb.UNTRUSTED)
from engine.book import UNSTABLE_FILLS, explain_book, read_book

fails = []


def check(name, got, want, tol=1e-9):
    if isinstance(want, float) and math.isnan(want):
        ok = isinstance(got, float) and math.isnan(got)
    elif isinstance(want, float):
        ok = got is not None and abs(got - want) <= tol
    else:
        ok = got == want
    print("  %-4s %-54s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True, premium=False, deal="instant", **flags):
    o = {"steamId": sid, "price": {"total_keys": keys}, "premium": premium,
         "listingType": "bot" if bot else None, "dealType": deal,
         "strange": False, "spelled": False, "painted": False, "parted": False}
    o.update(flags)
    return o


def main():
    print("buy fence: the Big Steel Jaw book (one 50-key flier over a 0.0214 cluster)")
    buys = [L("flier", 50.0, bot=False), L("a", 0.0223)] + \
           [L(f"b{i}", 0.0214) for i in range(5)]
    r = read_book(json.dumps({"buyOrders": buys, "sellOrders": []}))
    check("flier fenced out: bid1", r.bid1, 0.0223)
    check("bid5 = mean of top-5 survivors", r.bid5, (0.0223 + 4 * 0.0214) / 5)
    check("n_buy counts the flier (pre-fence)", r.n_buy, 7)
    check("n_buy_fenced", r.n_buy_fenced, 1)
    check("top_bot", r.top_bot, 1.0)
    check("gap2 = (0.0223-0.0214)/0.0223", r.gap2, (0.0223 - 0.0214) / 0.0223)
    check("n_near_top: only the best is within 1%", r.n_near_top, 1)
    check("no sells -> ask missing", r.ask1, float("nan"))
    check("bid1_fill = bid1 when no unstable account bids", r.bid1_fill, 0.0223)

    print("bid1_fill: an unstable account's bid is in the book but never a fill")
    bad = sorted(UNSTABLE_FILLS)[0]
    buys = [L(bad, 1.04, premium=True), L("a", 1.01), L("b", 1.0), L("c", 1.0), L("d", 1.0)]
    r = read_book(json.dumps({"buyOrders": buys, "sellOrders": []}))
    check("the unstable bid is still bid1 (the fence is unchanged)", r.bid1, 1.04)
    check("... bid1_fill is the best bid from any other account", r.bid1_fill, 1.01)
    r = read_book(json.dumps({"buyOrders": [L(bad, 1.0), L(sorted(UNSTABLE_FILLS)[1], 1.0),
                                            L("x", 1.0, bot=False)], "sellOrders": []}))
    check("... a steamId read as a number matches too", read_book(json.dumps(
        {"buyOrders": [L(int(bad), 1.2, premium=True), L("a", 1.0), L("b", 1.0)]})).bid1_fill, 1.0)
    check("... only unstable bids but one: that one", r.bid1_fill, 1.0)
    r = read_book(json.dumps({"buyOrders": [L(b, 1.0) for b in sorted(UNSTABLE_FILLS)] + [L("x", 3.0)],
                              "sellOrders": []}))
    check("... none left after the fence: missing", r.bid1_fill, float("nan"))

    print("untrusted level 2: out of the book, before the median")
    import engine.book as eb
    saved = eb.DROPPED
    eb.DROPPED = frozenset({"z"})
    try:
        buys = [L("z", 1.30, premium=True), L("z", 1.25, premium=True), L("a", 1.0), L("b", 1.0),
                L("c", 1.0)]
        r = read_book(json.dumps({"buyOrders": buys, "sellOrders": [L("z", 0.5), L("s", 1.1)]}))
        check("a level-2 account's bids are not valid (3, not 4)", r.n_buy, 3)
        check("... so they never set bid1", r.bid1, 1.0)
        check("... nor its sells", r.n_sell, 1)
        e = explain_book({"buyOrders": buys, "sellOrders": []})
        check("explain_book names it", {x["reason"] for x in e["buys"] if x["steamId"] == "z"},
              {"untrusted account"})
    finally:
        eb.DROPPED = saved
    lv1 = explain_book({"buyOrders": [L(bad, 1.0), L("a", 1.0), L("b", 1.0)], "sellOrders": []})
    check("a level-1 account stays kept, marked with its level",
          [(x["reason"], x["untrusted"]) for x in lv1["buys"] if x["steamId"] == bad], [("kept", 1)])

    print("buy fence: trust-modulated high cut (1.02 / 1.10 / 1.20)")
    buys = [L("h0", 1.03, bot=False), L("h0b", 1.01, bot=False),
            L("t1", 1.12), L("t2", 1.25, premium=True), L("t2b", 1.18, premium=True),
            L("m1", 1.0), L("m2", 1.0), L("m3", 1.0)]
    r = read_book({"buyOrders": buys, "sellOrders": []})
    # median of the top 7 [1.25, 1.18, 1.12, 1.03, 1.01, 1.0, 1.0] = 1.03 (the 8th, 1.0, is
    # past FENCE_DEPTH)
    check("trust-2 at 1.21x dropped, at 1.146x kept -> bid1", r.bid1, 1.18)
    check("only the trust-2 1.25 fenced (t0 1.03=1.0x, t1 1.12=1.087x kept)",
          r.n_buy_fenced, 1)
    e = explain_book({"buyOrders": buys, "sellOrders": []})
    why = {x["steamId"]: (x["reason"], x["detail"]) for x in e["buys"]}
    check("trust-2 flier detail names the trust", why["t2"],
          ("fence: too high", "1.21x median > 1.2x (trust 2)"))
    buys = [L("h0", 1.03, bot=False), L("m1", 1.0), L("m2", 1.0), L("m3", 1.0)]
    check("trust-0 bid at 1.03x median dropped (k=1.02)",
          read_book({"buyOrders": buys, "sellOrders": []}).bid1, 1.0)

    print("fence depth: the median reads the top FENCE_DEPTH (7) only -- a 30-deep app book")
    from engine.book import FENCE_DEPTH, fence_median, valid_buys
    top = [L(f"c{i}", 1.0 - i * 0.001, bot=False) for i in range(8)]       # human cluster at ~1.0
    deep = [L(f"d{i}", 0.75 - i * 0.005) for i in range(22)]                # deep bids 0.75 -> 0.645
    book = {"buyOrders": top + deep, "sellOrders": []}
    vb = valid_buys(book)
    check("FENCE_DEPTH is 7 (classifieds books stored the top 7 a side)", FENCE_DEPTH, 7)
    check("median over the top 7 = the cluster's 4th", fence_median(vb), 0.997, tol=1e-12)
    check("median over all 30 sits deep in the book", fence_median(vb, None) < 0.75, True)
    r = read_book(book)
    check("trust-0 cluster kept: bid1 is the real top bid", r.bid1, 1.0)
    check("bid_med = median of the fenced top 7", r.bid_med, 0.997, tol=1e-12)
    check("deep bids inside 0.40x stay in the fence (depth features keep them)", r.n_buy_fenced, 0)
    check("... and count in n_buy", r.n_buy, 30)
    e = explain_book(book)
    check("explain_book shows both medians",
          (round(e["buy"]["median"], 6), e["buy"]["median_all"] < 0.75, e["buy"]["depth"]),
          (0.997, True, 7))
    check("explain_book marks the listings the median read",
          sum(x["in_depth"] for x in e["buys"]), 7)
    m_all = fence_median(vb, None)
    old = [p for p, o in vb if 0.40 * m_all <= p <= 1.02 * m_all]
    check("the old rule (median over all 30) would have fenced out the whole cluster",
          max(old) < 0.99, True)
    shallow = {"buyOrders": top[:7], "sellOrders": []}
    check("a <= 7-deep book reads exactly as before",
          fence_median(valid_buys(shallow)), fence_median(valid_buys(shallow), None))

    print("flags + dedupe")
    buys = [L("x", 1.00), L("x", 1.02), L("y", 1.01), L("z", 0.99),
            L("p", 1.30, parted=True), L("q", 1.30, painted=True),
            L("s", 1.30, strange=True), L("t", 1.30, spelled=True),
            {"steamId": "n", "price": {"total_keys": None}}]
    r = read_book({"buyOrders": buys, "sellOrders": []})
    check("different goods + null price dropped, x deduped", r.n_buy, 3)
    check("dedupe keeps x's HIGHEST bid", r.bid1, 1.02)
    check("n_near_top (1.02, 1.01 within 1%)", r.n_near_top, 2)

    print("missing, not zero")
    r = read_book({"buyOrders": [L("a", 1.0), L("b", 1.0)], "sellOrders": []})
    check("2 valid bids < MIN_VALID -> bid missing", r.bid1, float("nan"))
    check("...and nothing counted as fenced", r.n_buy_fenced, 0)
    check("bad json -> empty read", read_book("{not json").n_buy, 0)

    print("sell fence: trust-modulated")
    sells = [L("t0", 0.70, bot=False), L("t1", 0.70, bot=True),     # 0.70 x median
             L("t2", 0.40, bot=True, premium=True),                 # 0.40 x median
             L("m1", 1.0), L("m2", 1.0), L("m3", 1.0), L("m4", 1.02),
             L("m4", 1.01)]                                          # dedupe -> lowest
    r = read_book({"buyOrders": [], "sellOrders": sells})
    # median of [0.40, 0.70, 0.70, 1.0, 1.0, 1.0, 1.01] = 1.0
    check("trust-2 lowball at 0.40x survives (k=0.35)", r.ask1, 0.40)
    check("trust-0 at 0.70x dropped, trust-1 kept", r.n_sell_fenced, 1)
    check("ask5 = mean of 5 lowest survivors", r.ask5, (0.40 + 0.70 + 1.0 + 1.0 + 1.0) / 5)
    check("sells dedupe keeps the LOWEST", r.n_sell, 7)
    check("sell_bot_share", r.sell_bot_share, 6 / 7)
    sells = [L("h0", 0.93, bot=False), L("h1", 0.96, bot=False),
             L("m1", 1.0), L("m2", 1.0), L("m3", 1.0), L("far", 1.6)]
    r = read_book({"buyOrders": [], "sellOrders": sells})
    check("trust-0 at 0.93x dropped, at 0.96x kept (k=0.95)", r.ask1, 0.96)
    check("an ask above 1.5x median is fenced out of ask5",
          r.ask5, (0.96 + 1.0 + 1.0 + 1.0) / 4)

    print("who is bidding")
    buys = [L("a", 1.0, bot=True, premium=True), L("b", 1.0, bot=False, deal="negotiate"),
            L("c", 1.0, bot=False), L("d", 1.0, bot=True)]
    r = read_book({"buyOrders": buys, "sellOrders": []})
    check("buy_bot_share", r.buy_bot_share, 0.5)
    check("buy_premium_share", r.buy_premium_share, 0.25)
    check("buy_instant_share", r.buy_instant_share, 0.75)
    check("bid_disp of an agreed book", r.bid_disp, 0.0)

    print("explain_book: every listing's verdict, same numbers as read_book")
    buys = ([L("flier", 50.0, bot=False), L("a", 0.0223), L("a", 0.0200)]
            + [L(f"b{i}", 0.0214) for i in range(5)]
            + [L("p", 0.03, parted=True), {"steamId": "n", "price": {"total_keys": None}}])
    sells = [L("t0", 0.70, bot=False), L("m1", 1.0), L("m2", 1.0), L("m3", 1.0)]
    e = explain_book(json.dumps({"buyOrders": buys, "sellOrders": sells}))
    why = {(r["steamId"], r["tk"]): (r["reason"], r["role"]) for r in e["buys"]}
    check("flier -> fence: too high", why[("flier", 50.0)], ("fence: too high", ""))
    check("best survivor -> kept, bid1", why[("a", 0.0223)], ("kept", "bid1"))
    check("same account's lower bid -> duplicate", why[("a", 0.02)], ("duplicate account", ""))
    check("parted -> flag", why[("p", 0.03)], ("flag: parted", ""))
    check("null price", why[("n", None)], ("null price", ""))
    check("6th survivor kept but outside the top 5",
          sum(1 for r in e["buys"] if r["reason"] == "kept" and r["role"] == ""), 1)
    check("buys listed high -> low, nulls last", [r["tk"] for r in e["buys"]][:2], [50.0, 0.03])
    check("sell trust fence detail names the trust",
          next(r["detail"] for r in e["sells"] if r["steamId"] == "t0"), "0.70x median < 0.95x (trust 0)")
    check("summary counts", (e["buy"]["n_raw"], e["buy"]["n_valid"], e["buy"]["n_kept"]), (10, 7, 6))
    r = read_book({"buyOrders": buys, "sellOrders": sells})
    check("prices agree with read_book", (e["prices"]["bid1"], e["prices"]["ask1"]), (r.bid1, r.ask1))
    few = explain_book({"buyOrders": [L("a", 1.0), L("b", 1.0)], "sellOrders": []})
    check("< 3 valid -> 'no median', nothing kept",
          [x["reason"] for x in few["buys"]], ["no median", "no median"])

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


main()

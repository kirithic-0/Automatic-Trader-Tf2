# -*- coding: utf-8 -*-
"""Offline test for engine.recheck (Stage 6, 3.5): buy / sell decided again on a fresh book.

    python test_recheck.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import sys

import pandas as pd

from engine import recheck as rc

fails = []
NOW = pd.Timestamp("2026-10-19T14:00:00", tz="UTC")


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True):
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": int(keys), "ref": round(int(round(keys * 100)) % 9 * 0.11, 2), "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False,
            "id": f"440_{17000000000 + int(sid)}"}


def book(sells, buys, age_s=10.0):
    sids = {o["steamId"] for o in sells + buys}
    return {"sellOrders": sells, "buyOrders": buys, "tradeUrls": {s: "url" + s for s in sids},
            "scraped_at": (NOW - pd.Timedelta(seconds=age_s)).isoformat(), "watch_id": 7}


SELLS = [L("1", 1.00), L("2", 1.01), L("3", 1.02)]
BUYS = [L("11", 0.98), L("12", 0.97), L("13", 0.96)]


def test_buy():
    print("recheck_buy")
    d = rc.recheck_buy({"ask1": 1.00}, book(SELLS, BUYS), now=NOW)
    check("the same price as the pick: go, from the cheapest", (d["ok"], d["listing"]["steam_id"]), (True, "1"))
    check("... carrying the book's watch_id (the offer log's)", d["watch_id"], 7)
    d = rc.recheck_buy({"ask1": 1.02}, book(SELLS, BUYS), now=NOW)
    check("cheaper than the pick: go", d["ok"], True)
    d = rc.recheck_buy({"ask1": 0.995}, book(SELLS, BUYS), now=NOW)
    check("dearer than the pick's ask1: no", (d["ok"], d["reason"][:21]), (False, "dearer than the pick:"))
    human = [L("0", 0.99, bot=False)] + SELLS
    d = rc.recheck_buy({"ask1": 0.99}, book(human, BUYS), now=NOW)
    check("ask1 set by a human, the bots dearer: no", (d["ok"], d["listing"]["steam_id"]), (False, "1"))
    d = rc.recheck_buy({"ask1": 1.00}, book(SELLS, BUYS, age_s=600), now=NOW)
    check("a stale book is not a re-check", (d["ok"], d["reason"][:12]), (False, "book is 600 "))
    d = rc.recheck_buy({"ask1": 1.00}, book(SELLS, [L("11", 0.90), L("12", 0.89), L("13", 0.88)]), now=NOW)
    check("bids fell: spread 11% left the universe", (d["ok"], d["reason"][:17]), (False, "left the universe"))
    d = rc.recheck_buy({"ask1": 1.00}, book(SELLS, BUYS[:2]), now=NOW)
    check("too few bids for a fence", (d["ok"], d["reason"]), (False, "no fenced ask or bid now"))
    d = rc.recheck_buy({"ask1": 1.00}, book([L(s, 1.0, bot=False) for s in "123"], BUYS), now=NOW)
    check("only humans sell: no", (d["ok"], d["reason"]), (False, "no tradable sell listing now"))
    d = rc.recheck_buy({"ask1": 1.00}, dict(book(SELLS, BUYS), scraped_at=None), now=NOW)
    check("no scrape time: no", d["ok"], False)


def test_sell():
    print("recheck_sell")
    pos = {"ask1": 1.00}
    d = rc.recheck_sell(pos, book(SELLS, BUYS), min_keys=0.97, now=NOW)
    check("best bid over the minimum: go, into the best", (d["ok"], d["listing"]["steam_id"]), (True, "11"))
    d = rc.recheck_sell(pos, book(SELLS, BUYS), min_keys=1.04, now=NOW)
    check("under the take-profit: no, but says what is there",
          (d["ok"], d["listing"]["total_keys"]), (False, 0.98))
    d = rc.recheck_sell(pos, book(SELLS, BUYS), now=NOW)
    check("at the horizon (min 0): sell to the best", d["ok"], True)
    d = rc.recheck_sell({"ask1": 0.30}, book(SELLS, BUYS), now=NOW)
    check("every bid above MAX_JUMP x entry: a misread, never a sale",
          (d["ok"], d["reason"][:21]), (False, "every tradable bid is"))
    d = rc.recheck_sell(pos, book(SELLS, [L(s, 0.98, bot=False) for s in ("11", "12", "13")]), now=NOW)
    check("only humans bid: no", (d["ok"], d["reason"]), (False, "no tradable bid now"))
    d = rc.recheck_sell({"entry_keys": 1.00}, book(SELLS, BUYS), now=NOW)
    check("entry_keys stands in for ask1", d["ok"], True)


def test_candidates():
    print("candidates: the next bots to try when one does not answer")
    d = rc.recheck_buy({"ask1": 1.01}, book(SELLS, BUYS), now=NOW)
    check("buy: every tradable sell at or below ask1, cheapest first",
          [c["steam_id"] for c in d["candidates"]], ["1", "2"])
    pos = {"ask1": 0.90}
    d = rc.recheck_sell(pos, book(SELLS, BUYS), min_keys=0.965, now=NOW)
    check("take-profit sale: every bid still >= the take-profit", [c["steam_id"] for c in d["candidates"]], ["11", "12"])
    bids = [L("11", 1.00), L("12", 0.99), L("13", 0.98), L("14", 0.975)]
    d = rc.recheck_sell(pos, book(SELLS, bids), now=NOW)
    check("horizon sale (min 0): the bids within 2% of the best", [c["steam_id"] for c in d["candidates"]],
          ["11", "12", "13"])


def K(sid, ref, keys=0, bot=True):
    o = L(sid, 1.0, bot)
    o["price"] = {"keys": keys, "ref": ref, "total_keys": round(keys + ref / 64.6, 4)}
    return o


def test_key():
    print("recheck_key")
    sells = [K("1", 64.77), K("2", 64.88), K("3", 66.0), K("4", 0, keys=1)]
    buys = [K("11", 64.66), K("12", 64.55), K("13", 64.0)]
    for i in range(5, 9):          # enough listings each side for the fence
        sells.append(K(str(i), 64.77))
        buys.append(K(str(i + 10), 64.55))
    d = rc.recheck_key(book(sells, buys), now=NOW)
    check("go, with the best of each side in ref", (d["ok"], d["ask_ref"], d["bid_ref"]), (True, 64.77, 64.66))
    check("buy: sells at most 3 scrap over the bid; priced in keys never", sorted({x["ref"] for x in d["buy"]}), [64.77, 64.88])
    check("sell: bids at most 3 scrap under the ask", sorted({x["ref"] for x in d["sell"]}), [64.55, 64.66])
    d = rc.recheck_key(book(sells, buys, age_s=500), now=NOW)
    check("an old book: no", d["ok"], False)
    d = rc.recheck_key(book(sells, []), now=NOW)
    check("no bid: no", (d["ok"], d["reason"]), (False, "no tradable key bid in metal now"))
    b = dict(book(sells, buys), key_price_ref=64.6)
    check("both sides near the key price: go", rc.recheck_key(b, now=NOW)["ok"], True)
    b["key_price_ref"] = 70.0
    check("more than 2% off the key price: no", rc.recheck_key(b, now=NOW)["ok"], False)
    # a crossed book (2026-11-01 12:41: 63.66 ask / 64.11 bid): the window reaches 3 scrap past
    # the higher of the two on each side, so the sellers just above the bid are tried too
    sells = [K("1", 63.66), K("2", 63.88), K("3", 64.33), K("4", 64.55)]
    buys = [K("11", 64.11), K("12", 63.44), K("13", 63.22)]
    for i in range(5, 9):
        sells.append(K(str(i), 64.33))
        buys.append(K(str(i + 10), 63.44))
    d = rc.recheck_key(book(sells, buys), now=NOW)
    check("crossed: best of each side", (d["ok"], d["ask_ref"], d["bid_ref"]), (True, 63.66, 64.11))
    check("crossed buy: up to 3 scrap over the higher (the bid)", sorted({x["ref"] for x in d["buy"]}), [63.66, 63.88, 64.33])
    check("crossed sell: down to 3 scrap under the lower (the ask)", sorted({x["ref"] for x in d["sell"]}), [63.44, 64.11])


def main():
    test_buy()
    test_sell()
    test_candidates()
    test_key()
    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

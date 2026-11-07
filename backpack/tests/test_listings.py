# -*- coding: utf-8 -*-
"""Offline test for engine.listings (Stage 6, 3.1): which listings the agent may trade with,
and the reason for every one it skips.

    python test_listings.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import sqlite3
import sys

from engine import book as eb
from engine import listings as el
# The public copy ships UNTRUSTED empty: test the rule on two made-up accounts.
eb.UNTRUSTED = {"1000000001": 1, "1000000002": 1}
eb.UNSTABLE_FILLS = frozenset(eb.UNTRUSTED)

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True, instant=True, lid=None, **flags):
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": int(keys), "ref": round(int(round(keys * 100)) % 9 * 0.11, 2), "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant" if instant else "negotiate",
            "strange": False, "spelled": False, "painted": False, "parted": False,
            "id": lid if lid is not None else f"440_{17000000000 + int(sid)}", **flags}


def test_sells():
    print("sell listings: what the agent may buy from")
    book = {"sellOrders": [
        L("1", 1.00, painted=True),                 # cheapest, but a different good
        L("2", 1.01),                               # OK
        L("3", 1.02, bot=False),                    # a human
        L("4", 1.03, instant=False),                # a bot that negotiates
        L("5", 1.04),                               # no trade URL
        L("6", 1.05, lid="440_abc"),                # no asset id
        L("2", 1.06),                               # account 2's second listing
        L("7", 1.07),                               # OK
        L("8", 0.20, bot=False),                    # a human lowball: outside the fence
        L("9", 1.08), L("10", 1.09),
    ], "buyOrders": []}
    urls = {s: f"https://steamcommunity.com/tradeoffer/new/?partner={s}&token=t"
            for s in ("1", "2", "3", "4", "6", "7", "8", "9", "10")}
    rows = {r["steam_id"] + ("b" if r["total_keys"] == 1.06 else ""): r["reason"]
            for r in el.explain(book, urls) if r["side"] == "sell"}
    check("painted = a different good", rows["1"], "different good (painted)")
    check("a human is not bot+instant", rows["3"], "not bot+instant (normal/instant)")
    check("a negotiating bot is not bot+instant", rows["4"], "not bot+instant (bot/negotiate)")
    check("no trade URL", rows["5"], "no trade URL")
    check("no asset id", rows["6"], "no asset id")
    check("one account, one listing (its cheapest)", rows["2b"], "not the account's best listing")
    check("a lowball under the fence", rows["8"], "outside the fence")
    odd = dict(book, sellOrders=book["sellOrders"] + [dict(L("11", 1.075), price={"keys": 1, "ref": 5.38, "total_keys": 1.075})])
    check("a price metal can't pay exactly (5.38 ref)",
          [r["reason"] for r in el.explain(odd, dict(urls, **{"11": "u"})) if r["steam_id"] == "11"][0],
          "price not whole keys + scrap")
    check("payable: whole keys + n x .11 ref", [el.payable(p) for p in (
        {"keys": 1, "ref": 41.77}, {"keys": 0, "ref": 0.11}, {"keys": 1, "ref": 5.38}, {"keys": 1.5, "ref": 0})],
        [True, True, False, False])
    ok = el.tradable_sells(book, urls)
    check("tradable, cheapest first", [r["steam_id"] for r in ok], ["2", "7", "9", "10"])
    check("... with the asset id to request", ok[0]["asset_id"], el.asset_id(book["sellOrders"][1]))
    check("... and the trade URL", ok[0]["trade_url"], urls["2"])


def test_buys_and_untrusted():
    print("buy orders: what the agent may sell into; untrusted accounts")
    bad = next(iter(eb.UNTRUSTED))
    book = {"sellOrders": [], "buyOrders": [
        L(bad, 1.04),                               # untrusted (level 1): in the fence, never a fill
        L("11", 1.03), L("12", 1.02, spelled=True), L("13", 1.01), L("14", 1.00),
        L("15", 9.0),                               # a flier above the fence
    ]}
    urls = {s: "u" for s in (bad, "11", "12", "13", "14", "15")}
    rows = {r["steam_id"]: r["reason"] for r in el.explain(book, urls)}
    check("an untrusted account is never traded with", rows[bad], "untrusted")
    check("a flier above the fence", rows["15"], "outside the fence")
    check("spelled = a different good", rows["12"], "different good (spelled)")
    check("tradable bids, highest first", [r["steam_id"] for r in el.tradable_buys(book, urls)],
          ["11", "13", "14"])
    check("buy orders carry no asset id", el.tradable_buys(book, urls)[0]["asset_id"], None)
    thin = {"sellOrders": [L("1", 1.0), L("2", 1.01)], "buyOrders": []}
    check("under MIN_VALID listings no fence forms: nothing tradable",
          el.explain(thin, {"1": "u", "2": "u"})[0]["reason"], f"no fence (< {eb.MIN_VALID} valid listings)")


def test_trade_urls():
    print("trade_urls from the trade_url table")
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE trade_url (steam_id TEXT PRIMARY KEY, url TEXT, first_seen TEXT, changed_at TEXT)")
    con.executemany("INSERT INTO trade_url VALUES (?, ?, '', '')", [("1", "a"), ("2", "b"), ("3", "c")])
    check("all", el.trade_urls(con), {"1": "a", "2": "b", "3": "c"})
    check("only these (ints are fine)", el.trade_urls(con, [2, "3", "9"]), {"2": "b", "3": "c"})
    check("none asked", el.trade_urls(con, []), {})


def main():
    test_sells()
    test_buys_and_untrusted()
    test_trade_urls()
    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

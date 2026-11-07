# -*- coding: utf-8 -*-
"""Offline test for the take-profit alert (held_alert.py) -- no browser, no network.

  * the rule: >= entry x 1.04 fires, below does not, above MAX_JUMP x entry is a misread
  * only a tradable bid counts: fenced, not UNTRUSTED, bot + instant (a human or a
    negotiate bot over the bar does not fire; a fenced-out flier does not fire)
  * no entry price = watched only
  * edge-triggered: fires once, re-arms after the bid drops back, a failed post retries
  * the webhook URL never reaches a log; no file = logged only
  * through watch_tick: a tick over the bar posts one message

    python test_held_alert.py
"""
import os as _os, sys as _sys  # noqa: E402 -- make collector/ importable from tests/
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import sys
import tempfile

import engine_book as eb
# The public copy ships UNTRUSTED empty: test the rule on two made-up accounts.
eb.UNTRUSTED = {"1000000001": 1, "1000000002": 1}
eb.UNSTABLE_FILLS = frozenset(eb.UNTRUSTED)
import held_alert as ha
import scrape_queue as sq
import snapshot_store as ss
import watchlist as wl

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-60s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def bid(keys, sid, kind="bot", deal="instant", **flags):
    return {"user": f"u{sid}", "steamId": str(sid), "premium": False,
            "price": {"keys": keys, "ref": 0, "total_keys": keys},
            "listingType": kind, "dealType": deal, **flags}


def book(*bids):
    return {"sellOrders": [], "buyOrders": list(bids)}


ENTRY = {"item": "Team Captain", "quality": 6, "entry_keys": 1.0}
# a 4-bid cluster around 1.0, so the fence median is ~1.0
CLUSTER = [bid(1.00, 2), bid(0.99, 3), bid(0.98, 4)]


async def main():
    print("=== the rule ===")
    check("TAKE_PROFIT / MAX_JUMP", (ha.TAKE_PROFIT, ha.MAX_JUMP), (0.04, 3.0))
    hit = ha.check(ENTRY, book(bid(1.05, 1), *CLUSTER))
    check("a bot+instant bid at +5% fires", hit and round(hit.bid, 4), 1.05)
    check("... its return", hit and round(hit.ret, 4), 0.05)
    check("+3% does not", ha.check(ENTRY, book(bid(1.03, 1), *CLUSTER)), None)
    check("exactly +4% fires", ha.check(ENTRY, book(bid(1.04, 1), *CLUSTER)) is not None, True)
    low = {**ENTRY, "entry_keys": 0.30}
    check("over MAX_JUMP x entry is a misread", ha.check(low, book(bid(1.05, 1), *CLUSTER)), None)
    check("no entry price = watched only",
          ha.check({"item": "x", "quality": 6}, book(bid(1.05, 1), *CLUSTER)), None)

    print("\n=== only a tradable bid ===")
    human = book(bid(1.05, 1, kind="friend", deal="negotiate"), *CLUSTER)
    check("a human bid over the bar does not fire", ha.check(ENTRY, human), None)
    neg = book(bid(1.05, 1, deal="negotiate"), *CLUSTER)
    check("a negotiate bot does not fire", ha.check(ENTRY, neg), None)
    sid = next(iter(eb.UNSTABLE_FILLS))
    check("an UNTRUSTED account does not fire", ha.check(ENTRY, book(bid(1.05, sid), *CLUSTER)), None)
    check("a flier the fence cuts does not fire (1.3x median)",
          ha.check(ENTRY, book(bid(1.30, 1), *CLUSTER)), None)
    check("a spelled bid does not fire", ha.check(ENTRY, book(bid(1.05, 1, spelled=True), *CLUSTER)), None)
    mixed = book(bid(1.06, 9, kind="friend", deal="negotiate"), bid(1.05, 1), *CLUSTER)
    h = ha.check(ENTRY, mixed)
    check("the human on top is skipped, the bot below it fires", h and h.steam_id, "1")
    check("too thin to fence (< 3 valid bids) does not fire",
          ha.check(ENTRY, book(bid(1.05, 1), bid(1.0, 2))), None)

    print("\n=== edge-triggered, the webhook ===")
    d = tempfile.mkdtemp()
    hook = _os.path.join(d, "hook")
    open(hook, "w").write("https://discord.com/api/webhooks/1/SECRET\n")
    sent, logs = [], []
    al = ha.Alerter(hook, post=lambda url, text: sent.append((url, text)), log=logs.append)
    over, under = ha.check(ENTRY, book(bid(1.05, 1), *CLUSTER)), None
    check("first tick over the bar posts", (await al.observe(ENTRY, over), len(sent)), (True, 1))
    check("the next tick still over does not", (await al.observe(ENTRY, over), len(sent)), (False, 1))
    await al.observe(ENTRY, under)
    check("back under, then over again posts again", (await al.observe(ENTRY, over), len(sent)), (True, 2))
    check("posted to the file's URL", sent[0][0], "https://discord.com/api/webhooks/1/SECRET")
    check("the message names the item, bid and return",
          all(s in sent[0][1] for s in ("Team Captain", "1.05", "+5.0%", "go try")), True)
    check("the URL never reaches the log", any("SECRET" in l for l in logs), False)

    def boom(url, text):
        raise OSError("refused")
    al2 = ha.Alerter(hook, post=boom, log=logs.append)
    await al2.observe(ENTRY, over)
    check("a failed post re-arms (retried next tick)", al2.firing, set())
    check("... and its log has no URL", any("SECRET" in l for l in logs), False)
    al3 = ha.Alerter(_os.path.join(d, "missing"), post=lambda *a: sent.append(a), log=logs.append)
    n = len(sent)
    check("no webhook file = logged only", (await al3.observe(ENTRY, over), len(sent)), (True, n))
    open(hook, "w").write("not-a-url")
    check("a file without an https URL counts as none", ha._webhook_url(hook), None)

    print("\n=== through watch_tick ===")
    open(hook, "w").write("https://discord.com/api/webhooks/1/SECRET")
    db = ss.connect(_os.path.join(d, "w.db"))
    row = {"item": "Team Captain", "variant": 0, "quality": 6, "source": "app",
           "scrapedAt": ss._now_iso(), "keyRate": 70.0,
           "tradeUrls": {"1": "https://steamcommunity.com/tradeoffer/new/?partner=1&token=t"},
           "sellOrders": [],
           # no total_keys here: the tick folds it in at keyRate, as the stored book has it
           "buyOrders": [{**bid(1, 1), "price": {"keys": 1, "ref": 3.5}}] + [
               {**b, "price": {"keys": b["price"]["keys"], "ref": 0}} for b in CLUSTER]}

    class T:
        async def run(self, jobs):
            for j in jobs:
                yield sq.RawResult(j.id, True, [dict(row)], None, 1)

    sent.clear()
    al = ha.Alerter(hook, post=lambda url, text: sent.append(text), log=lambda *_: None)
    await wl.watch_tick(sq.ScrapeQueue({"bptf": T()}), db, [ENTRY], quiet=True, alerter=al)
    check("1 key + 3.5 ref at 70 ref/key = 1.05 keys -> one alert", len(sent), 1)
    check("the bidder's stored trade URL is in it", "token=t" in (sent[0] if sent else ""), True)
    await wl.watch_tick(sq.ScrapeQueue({"bptf": T()}), db, [ENTRY], quiet=True, alerter=al)
    check("the next tick, still over, sends nothing", len(sent), 1)
    db.dispose()

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


if __name__ == "__main__":
    asyncio.run(main())

# -*- coding: utf-8 -*-
"""Offline test for the on-demand book endpoint (book_server.py) -- no browser.

A real aiohttp server on 127.0.0.1 over a fake bptf transport:
  * 200: the book in stored form (total_keys folded in at the book's rate), trade URLs,
    stored in watch_snapshots with reason 'check' (and a tick's row says 'tick')
  * the request rides at CHECK_PRIORITY, ahead of ticks and the pass
  * 400 (no item, quality 5, a non-number), 502 (the fetch failed), 504 (no book in time)
  * /health; it listens on 127.0.0.1 only

    python test_book_server.py
"""
import os as _os, sys as _sys  # noqa: E402 -- make collector/ importable from tests/
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import sys
import tempfile

import aiohttp

import book_codec
import book_server as bs
import scrape_queue as sq
import scrape_run as sr
import snapshot_store as ss
import watchlist as wl

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-58s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def row(item, quality, rate=70.0):
    return {"item": item, "variant": 0, "quality": quality, "source": "app",
            "scrapedAt": ss._now_iso(), "keyRate": rate, "nBuyTotal": 4, "nSellTotal": 9,
            "tradeUrls": {"7656": "https://steamcommunity.com/tradeoffer/new/?partner=1&token=a"},
            "sellOrders": [{"id": "440_123", "steamId": "s1", "price": {"keys": 2, "ref": 7},
                            "listingType": "bot", "dealType": "instant"}],
            "buyOrders": [{"id": "440_7656_ab", "steamId": "7656", "price": {"keys": 1, "ref": 35}}]}


class FakeTransport:
    def __init__(self, rows, slow=()):
        self.rows, self.slow, self.seen = rows, set(slow), []

    async def run(self, jobs):
        for j in jobs:
            self.seen.append((j.item, j.quality, j.priority))
            if j.item in self.slow:
                await asyncio.sleep(5)
            r = self.rows.get((j.item, j.quality))
            yield (sq.RawResult(j.id, True, [dict(r)], None, 1) if r is not None
                   else sq.RawResult(j.id, False, None, "dead-lettered", 3))


async def main():
    db = ss.connect(_os.path.join(tempfile.mkdtemp(), "b.db"))
    t = FakeTransport({("Team Captain", 6): row("Team Captain", 6)}, slow={"Slow Hat"})
    q = sq.ScrapeQueue({"bptf": t})
    logs = []
    runner = await bs.start(q, db, port=0, timeout=0.5, log=logs.append)
    host, port = runner.addresses[0][:2]
    base = f"http://127.0.0.1:{port}"
    check("listens on 127.0.0.1 only", host, "127.0.0.1")

    async with aiohttp.ClientSession() as http:
        print("=== 200 ===")
        async with http.get(f"{base}/book", params={"item": "Team Captain", "quality": "6"}) as r:
            status, b = r.status, await r.json()
        check("200", status, 200)
        check("item / quality echoed", (b["item"], b["quality"]), ("Team Captain", 6))
        check("the book's own key rate", b["key_price_ref"], 70.0)
        check("total_keys folded in (2 keys + 7 ref at 70)",
              b["sellOrders"][0]["price"]["total_keys"], 2.1)
        check("the listing id comes through (the asset id to request)",
              b["sellOrders"][0]["id"], "440_123")
        check("trade URLs", b["tradeUrls"].get("7656", "")[-7:], "token=a")
        check("totals", (b["n_buy_total"], b["n_sell_total"]), (4, 9))
        check("rode at CHECK_PRIORITY", t.seen[-1][2], bs.CHECK_PRIORITY)
        check("ahead of a tick and the pass", bs.CHECK_PRIORITY < wl.WATCH_PRIORITY < 0, True)
        with db.connect() as c:
            w = c.execute(ss.watch_snapshots.select()).mappings().all()
        check("stored once in watch_snapshots", len(w), 1)
        check("... with reason 'check'", w[0]["reason"], "check")
        check("... watch_id is that row", b["watch_id"], w[0]["id"])
        check("... not in snapshots", ss.snapshot_count(db), 0)
        codec = book_codec.Codec.from_con(db.raw_connection().driver_connection)
        check("the stored book decodes the same",
              codec.decode(w[0]["book_json"])["sellOrders"][0]["price"]["total_keys"], 2.1)

        print("\n=== errors ===")
        for params, want, name in (({"quality": "6"}, 400, "no item -> 400"),
                                   ({"item": "Hat", "quality": "5"}, 400, "quality 5 -> 400"),
                                   ({"item": "Hat", "quality": "x"}, 400, "quality not a number -> 400"),
                                   ({"item": "Gone Hat", "quality": "6"}, 502, "the fetch failed -> 502"),
                                   ({"item": "Slow Hat", "quality": "6"}, 504, "no book in time -> 504")):
            async with http.get(f"{base}/book", params=params) as r:
                check(name, r.status, want)
                body = await r.json()
                check(f"  ... says why", bool(body.get("error")), True)
        check("a failure is logged", any("Gone Hat" in l for l in logs), True)

        async with http.get(f"{base}/health") as r:
            check("/health", (r.status, (await r.json())["ok"]), (200, True))

    await runner.cleanup()

    print("\n=== a tick says 'tick' ===")
    t2 = FakeTransport({("Antlers", 11): row("Antlers", 11)})
    await wl.watch_tick(sq.ScrapeQueue({"bptf": t2}), db, [{"item": "Antlers", "quality": 11}], quiet=True)
    with db.connect() as c:
        reasons = [r[0] for r in c.execute(ss.watch_snapshots.select()
                                           .with_only_columns(ss.watch_snapshots.c.reason)
                                           .order_by(ss.watch_snapshots.c.id))]
    check("reasons in order", reasons, ["check", "tick"])

    print("\n=== an old watch_snapshots table gets the column ===")
    import sqlite3
    p = _os.path.join(tempfile.mkdtemp(), "old.db")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE watch_snapshots (id INTEGER PRIMARY KEY, item TEXT NOT NULL, "
                "variant INTEGER NOT NULL, quality INTEGER NOT NULL, scraped_at TEXT NOT NULL, "
                "ingested_at TEXT NOT NULL, source TEXT NOT NULL, key_price_ref FLOAT, n_buy_page INTEGER, "
                "n_sell_page INTEGER, n_buy_total INTEGER, n_sell_total INTEGER, book_json TEXT NOT NULL)")
    con.execute("INSERT INTO watch_snapshots VALUES (1,'A',0,6,'t','t','app',64,1,1,1,1,'{}')")
    con.commit()
    con.close()
    old = ss.connect(p)
    with old.connect() as c:
        got = c.execute(ss.watch_snapshots.select()).mappings().first()
    check("reason added, old rows NULL", ("reason" in got, got["reason"]), (True, None))
    old.dispose()
    db.dispose()

    print("\n=== the flag ===")
    check("--book-port defaults to 50052", sr.build_parser().parse_args([]).book_port, 50052)
    check("--book-port 0 turns it off", sr.build_parser().parse_args(["--book-port", "0"]).book_port, 0)

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


if __name__ == "__main__":
    asyncio.run(main())

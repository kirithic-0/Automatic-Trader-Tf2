# -*- coding: utf-8 -*-
"""Offline test for the held-item watch (watchlist.py) -- no browser.

  * the file: add / rm / dedupe, a missing file is empty, a bad quality refused
  * a tick: ScrapeQueue results land in watch_snapshots (not snapshots), compressed
    the same way, at the book's own key rate, trade URLs kept
  * the loop: ticks on its own, keeps the last good list when the file breaks
  * priority: a watch book jumps a pass's queue on the shared pool

    python test_watchlist.py
"""
import os as _os, sys as _sys  # noqa: E402 -- make collector/ importable from tests/
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import sys
import tempfile

import book_codec
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


def book_row(item, quality, bid_keys=1.0):
    return {
        "item": item, "variant": 0, "quality": quality, "source": "app",
        "scrapedAt": ss._now_iso(), "keyRate": 70.0,
        "nBuyPage": 1, "nSellPage": 1, "nBuyTotal": 1, "nSellTotal": 1,
        "tradeUrls": {"7656": "https://steamcommunity.com/tradeoffer/new/?partner=1&token=a"},
        "sellOrders": [{"steamId": "s1", "price": {"keys": 2, "ref": 0}}],
        "buyOrders": [{"steamId": "7656", "price": {"keys": bid_keys, "ref": 35}}],
    }


class ByItemTransport:
    """A bptf-shaped transport answering by (item, quality); records the job order."""

    def __init__(self, rows):
        self.rows, self.seen = rows, []

    async def run(self, jobs):
        for job in jobs:
            self.seen.append((job.item, job.quality, job.priority))
            row = self.rows.get((job.item, job.quality))
            if row is None:
                yield sq.RawResult(job.id, False, None, "no such book", 1)
            else:
                yield sq.RawResult(job.id, True, [dict(row)], None, 1)


async def main():
    d = tempfile.mkdtemp()
    path = _os.path.join(d, "watchlist.json")

    print("=== the file ===")
    check("a missing file is an empty list", wl.load(path), [])
    check("add", wl.add("Team Captain", 6, "bought 1.2", path), True)
    check("add again is a no-op", wl.add("Team Captain", 6, path=path), False)
    check("same name, other quality is its own entry", wl.add("Team Captain", 11, path=path), True)
    check("two watched", [(e["item"], e["quality"]) for e in wl.load(path)],
          [("Team Captain", 6), ("Team Captain", 11)])
    check("the note is kept", wl.load(path)[0]["note"], "bought 1.2")
    check("rm", wl.remove("Team Captain", 11, path), True)
    check("rm again", wl.remove("Team Captain", 11, path), False)
    try:
        wl.add("Hat", 5, path=path)
        check("an Unusual is refused", False, True)
    except ValueError:
        check("an Unusual is refused", True, True)
    bad = _os.path.join(d, "bad.json")
    open(bad, "w").write('[{"item": "Hat", "quality": 5}]')
    try:
        wl.load(bad)
        check("a bad quality in the file raises", False, True)
    except ValueError:
        check("a bad quality in the file raises", True, True)
    open(bad, "w").write('[{"item": "A", "quality": 6}, {"item": "A", "quality": 6}]')
    check("the file is deduped on read", len(wl.load(bad)), 1)

    print("\n=== a tick lands in watch_snapshots ===")
    db = ss.connect(_os.path.join(d, "w.db"))
    ss.record_key_rate(db, 64.0, "test")
    rows = {("Team Captain", 6): book_row("Team Captain", 6)}
    no_rate = book_row("Antlers", 11)
    no_rate.pop("keyRate")
    rows[("Antlers", 11)] = no_rate
    t = ByItemTransport(rows)
    q = sq.ScrapeQueue({"bptf": t})
    entries = [{"item": "Team Captain", "quality": 6}, {"item": "Antlers", "quality": 11},
               {"item": "Missing", "quality": 6}]
    ok, failed = await wl.watch_tick(q, db, entries, quiet=True)
    check("two ok, one failed", (ok, failed), (2, 1))
    check("no pass snapshot written", ss.snapshot_count(db), 0)
    with db.connect() as c:
        got = c.execute(ss.watch_snapshots.select()).mappings().all()
    by = {(r["item"], r["quality"]): r for r in got}
    check("two watch rows", len(got), 2)
    check("the book's own key rate", by[("Team Captain", 6)]["key_price_ref"], 70.0)
    check("no rate -> the latest recorded", by[("Antlers", 11)]["key_price_ref"], 64.0)
    check("watch requests ride at WATCH_PRIORITY", {p for _, _, p in t.seen}, {wl.WATCH_PRIORITY})
    codec = book_codec.Codec.from_con(db.raw_connection().driver_connection)
    book = codec.decode(by[("Team Captain", 6)]["book_json"])
    check("the book decodes, total_keys folded in",
          book["buyOrders"][0]["price"]["total_keys"], round(1 + 35 / 70, 4))
    check("trade URLs kept", ss.trade_url_for(db, "7656") is not None, True)
    # a second tick appends; a re-sent identical book (same scraped_at) is not doubled
    dup = book_row("Team Captain", 6)
    dup["scrapedAt"] = by[("Team Captain", 6)]["scraped_at"]
    check("a duplicate tick row is refused", ss.insert_watch_snapshot(db, "Team Captain", dup), None)

    print("\n=== the loop ===")
    open(path, "w").write('[{"item": "Team Captain", "quality": 6}]')
    t2 = ByItemTransport({("Team Captain", 6): book_row("Team Captain", 6, 1.1)})
    await wl.watch_loop(sq.ScrapeQueue({"bptf": t2}), db, path, every=0, quiet=True, ticks=2)
    check("two ticks fetched it twice", len(t2.seen), 2)
    open(path, "w").write("{ not json")
    t3 = ByItemTransport({("Team Captain", 6): book_row("Team Captain", 6, 1.2)})
    await wl.watch_loop(sq.ScrapeQueue({"bptf": t3}), db, path, every=0, quiet=True, ticks=1)
    check("a broken file at start = nothing fetched", len(t3.seen), 0)
    open(path, "w").write('[{"item": "Team Captain", "quality": 6}]')

    class BreaksFile(ByItemTransport):
        async def run(self, jobs):
            async for r in super().run(jobs):
                yield r
            open(path, "w").write("{ not json")   # broken after the first tick

    t5 = BreaksFile({("Team Captain", 6): book_row("Team Captain", 6, 1.3)})
    await wl.watch_loop(sq.ScrapeQueue({"bptf": t5}), db, path, every=0, quiet=True, ticks=2)
    check("a file broken mid-run keeps the last good list", len(t5.seen), 2)
    _os.remove(path)
    t4 = ByItemTransport({})
    await wl.watch_loop(sq.ScrapeQueue({"bptf": t4}), db, path, every=0, quiet=True, ticks=1)
    check("no file = no requests", len(t4.seen), 0)
    db.dispose()

    print("\n=== the CLI flags ===")
    a = sr.build_parser().parse_args(["--db", "x.db", "--watch", "3600"])
    check("--watch is still the pass interval", a.watch, 3600)
    check("the watch is on by default, every 600 s", a.watchlist_every, 600)
    a = sr.build_parser().parse_args(["--watchlist-every", "0"])
    check("--watchlist-every 0 turns it off", a.watchlist_every, 0)

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


if __name__ == "__main__":
    asyncio.run(main())

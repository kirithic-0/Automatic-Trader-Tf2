# -*- coding: utf-8 -*-
"""Offline test for the pass loop (scrape_run) and the replay transport -- no network.

  * collect_pass streams ScrapeQueue results into the REAL snapshot_store (a temp SQLite
    db) through the real writer, at each book's own key rate (or a pinned one);
  * ReplayTransport answers a job with the book a source database stored for that good
    in one pass, and fails a good with no stored book;
  * run() replays a source database pass by pass into a fresh one: same passes (start
    times kept), same books, the roster seeded from the source.

    python tests/test_scrape_run.py
"""
import os as _os, sys as _sys  # noqa: E402 -- make the collector importable from tests/
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import sqlite3
import sys
import tempfile

import book_codec
import replay_source as rs
import scrape_queue as sq
import scrape_run as sr
import snapshot_store as ss

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-54s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def demo_row(item, quality=6, scraped_at=None):
    """A collector row with 3 distinct valid bidders."""
    return {
        "item": item, "variant": 0, "quality": quality, "scrapedAt": scraped_at or ss._now_iso(),
        "source": "app", "ok": True, "effectName": "", "priced": True,
        "counts": {"sell": 1, "buy": 3}, "marketplace": None,
        "nBuyPage": 3, "nSellPage": 1, "nBuyTotal": 3, "nSellTotal": 1,
        "sellOrders": [{"steamId": "s1", "price": {"keys": 14, "ref": 0}, "strange": False, "spelled": False}],
        "buyOrders": [
            {"steamId": "b1", "price": {"keys": 12, "ref": 5}, "strange": False, "spelled": False},
            {"steamId": "b2", "price": {"keys": 11, "ref": 0}, "strange": False, "spelled": False},
            {"steamId": "b3", "price": {"keys": 9, "ref": 0}, "strange": False, "spelled": False},
        ],
    }


class RowTransport:
    """A minimal bptf-shaped transport: a canned row per item (no source db)."""

    def __init__(self, rows):
        self.rows = rows

    async def run(self, jobs):
        for job in jobs:
            row = self.rows.get(job.item)
            if row is None:
                yield sq.RawResult(job.id, False, None, "no row", 1)
            else:
                yield sq.RawResult(job.id, True, [dict(row)], None, 1)


def make_source(path, n_passes=3, items=("Team Captain", "Airdog")):
    """A small collector database: n passes an hour apart, one book per good each."""
    db = ss.connect(path)
    with db.begin() as conn:
        from sqlalchemy import insert
        for it in items:
            conn.execute(insert(ss.roster).values(item=it, quality=6, origin="legacy", admitted=1,
                                                  added_at="t", changed_at="t"))
    starts = []
    for p in range(n_passes):
        t = f"2026-10-20T{p:02d}:00:00+00:00"
        pid = ss.start_pass(db, n_items=len(items), started_at=t)
        starts.append(t)
        for it in items:
            if it == "Airdog" and p == 1:
                continue                      # a good the site did not serve in pass 2
            ss.insert_snapshot(db, it, demo_row(it, scraped_at=f"2026-10-20T{p:02d}:01:00+00:00"), 64.5, pid)
        ss.finish_pass(db, pid, n_pages=len(items))
    db.dispose()
    return starts


async def main():
    print("=== collect_pass: ScrapeQueue results land in the real database ===")
    tmp = _os.path.join(tempfile.mkdtemp(), "run.db")
    db = ss.connect(tmp)
    q = sq.ScrapeQueue({"bptf": RowTransport({"A": demo_row("A"), "B": demo_row("B")})})
    reqs = [sq.bptf_request("A"), sq.bptf_request("B"), sq.bptf_request("C")]   # C: no row
    pass_id = ss.start_pass(db, n_items=3)
    ok, failed = await sr.collect_pass(q, reqs, db_engine=db, key_price_ref=64.0,
                                       pass_id=pass_id, quiet=True)
    check("two ok, one failed", (ok, failed), (2, 1))
    check("two snapshots written", ss.snapshot_count(db), 2)
    db.dispose()

    print("\n=== collect_pass: each book is written at its own key rate ===")
    db = ss.connect(_os.path.join(tempfile.mkdtemp(), "rate.db"))
    ss.record_key_rate(db, 64.0, "test")
    ra, rb = demo_row("A"), demo_row("B")
    ra["keyRate"] = 70.0                      # the site repriced the key
    q = sq.ScrapeQueue({"bptf": RowTransport({"A": ra, "B": rb})})
    await sr.collect_pass(q, [sq.bptf_request("A")], db_engine=db, key_price_ref=64.0,
                          pass_id=None, quiet=True)
    await sr.collect_pass(q, [sq.bptf_request("B")], db_engine=db,
                          key_price_ref=ss.latest_key_rate(db)["ref_per_key"], pass_id=None, quiet=True)
    with db.connect() as c:
        kp = dict(c.execute(ss.snapshots.select().with_only_columns(
            ss.snapshots.c.item, ss.snapshots.c.key_price_ref)).fetchall())
    check("the book's rate is the snapshot's rate", kp["A"], 70.0)
    check("a changed rate is recorded", ss.latest_key_rate(db)["source"], "bptf-app-implied")
    check("a book without a rate falls back to the latest", kp["B"], 70.0)
    db.dispose()

    db2 = ss.connect(_os.path.join(tempfile.mkdtemp(), "pin.db"))
    q = sq.ScrapeQueue({"bptf": RowTransport({"A": ra})})
    await sr.collect_pass(q, [sq.bptf_request("A")], db_engine=db2, key_price_ref=60.0,
                          pass_id=None, quiet=True, pin_rate=True)
    with db2.connect() as c:
        pinned = c.execute(ss.snapshots.select().with_only_columns(ss.snapshots.c.key_price_ref)).scalar()
    check("--key-price pins the rate over the book's", pinned, 60.0)
    check("a pinned run records nothing", ss.latest_key_rate(db2), None)
    db2.dispose()

    print("\n=== ReplayTransport: a stored pass answers the jobs ===")
    src_path = _os.path.join(tempfile.mkdtemp(), "source.db")
    starts = make_source(src_path)
    src = rs.ReplaySource(src_path)
    check("the source's passes, oldest first", [t for _, t in src.passes()], starts)
    t = rs.ReplayTransport(src)
    t.pass_id = src.passes()[1][0]
    q = sq.ScrapeQueue({"bptf": t})
    res = {r.request.item: r for r in await q.collect(
        [sq.bptf_request("Team Captain", priced=True), sq.bptf_request("Airdog")])}
    check("a stored book -> ok", res["Team Captain"].ok, True)
    check("... its own scrape time", res["Team Captain"].data["scrapedAt"], "2026-10-20T01:01:00+00:00")
    check("... its listings", len(res["Team Captain"].data["buyOrders"]), 3)
    check("... the request's meta stamped on", res["Team Captain"].data["priced"], True)
    check("no stored book -> failed", (res["Airdog"].ok, res["Airdog"].error), (False, "no stored book"))

    print("\n=== run(): the whole source replayed into a fresh database ===")
    out = _os.path.join(tempfile.mkdtemp(), "replayed.db")
    args = sr.build_parser().parse_args(["--source", src_path, "--db", out, "--quiet",
                                         "--book-port", "0", "--watchlist-every", "0"])
    await sr.run(args)
    con = sqlite3.connect(out)
    check("one pass per source pass, start times kept",
          [r[0] for r in con.execute("SELECT started_at FROM passes ORDER BY id")], starts)
    check("every stored book replayed (3 passes x 2 goods, less 1)", con.execute("SELECT count(*) FROM snapshots").fetchone()[0], 5)
    check("the roster seeded from the source",
          sorted(r[0] for r in con.execute("SELECT item FROM roster")), ["Airdog", "Team Captain"])
    codec = book_codec.Codec.from_con(con)
    b = codec.decode(con.execute("SELECT book_json FROM snapshots ORDER BY id LIMIT 1").fetchone()[0])
    check("a replayed book decodes to the stored listings", [o["steamId"] for o in b["buyOrders"]],
          ["b1", "b2", "b3"])
    con.close()
    src.close()

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


if __name__ == "__main__":
    asyncio.run(main())

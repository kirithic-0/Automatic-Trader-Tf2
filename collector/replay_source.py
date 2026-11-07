"""Books as input with no website: stored passes replayed through the ScrapeQueue.

The live collector's `bptf` transport (Layer A, ADR 0002) fetched each book from the site.
That transport is not in this repository. `ReplayTransport` takes its place: it answers each
job with the book a source database stored for that good in one pass, so everything above
Layer A -- the queue, the pass loop, the writer, the watchlist, the book server -- runs
unchanged on recorded data (the sample in `sample/`).

    src = ReplaySource("sample/sample.db")
    t = ReplayTransport(src)
    q = scrape_queue.ScrapeQueue({"bptf": t})
    for pass_id, started_at in src.passes():
        t.pass_id = pass_id                 # which stored pass answers the next jobs
        async for r in q.run(reqs): ...

A good with no stored book in that pass is a failed job ("no stored book"), like a book the
site could not serve.
"""
import asyncio
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import book_codec  # noqa: E402
import scrape_queue as sq  # noqa: E402


class ReplaySource:
    """A stored database (the sample, or any collector database), read-only."""

    def __init__(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        self.path = path
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.codec = book_codec.Codec.from_con(self.con)

    def close(self):
        self.con.close()

    def passes(self, limit=None):
        """[(pass_id, started_at)] of every pass with books, oldest first."""
        rows = self.con.execute(
            "SELECT p.id, p.started_at FROM passes p WHERE EXISTS "
            "(SELECT 1 FROM snapshots s WHERE s.pass_id = p.id) ORDER BY p.started_at").fetchall()
        return rows[:limit] if limit else rows

    def roster(self):
        """The source's admitted roster rows (dicts), to seed an empty target."""
        cur = self.con.execute("SELECT * FROM roster WHERE admitted = 1 ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def key_rates(self):
        return self.con.execute("SELECT fetched_at, ref_per_key, source FROM key_price ORDER BY id").fetchall()

    def row(self, pass_id, item, quality):
        """The stored book of (item, quality) in this pass as a collector row, or None."""
        r = self.con.execute(
            "SELECT scraped_at, key_price_ref, n_buy_page, n_sell_page, n_buy_total, n_sell_total, "
            "priced, book_json FROM snapshots WHERE pass_id = ? AND item = ? AND quality = ? "
            "AND variant = 0 ORDER BY id DESC LIMIT 1", (pass_id, item, int(quality))).fetchone()
        if r is None:
            return None
        scraped_at, rate, nbp, nsp, nbt, nst, priced, blob = r
        book = self.codec.decode(blob)
        buys, sells = book.get("buyOrders") or [], book.get("sellOrders") or []
        return {
            "item": item, "variant": 0, "quality": int(quality), "scrapedAt": scraped_at,
            "source": "replay", "effectName": "", "marketplace": None, "ok": True,
            "counts": {"buy": len(buys), "sell": len(sells)},
            "buyOrders": buys, "sellOrders": sells,
            "nBuyPage": nbp, "nSellPage": nsp, "nBuyClean": None, "nSellClean": None,
            "nBuyTotal": nbt, "nSellTotal": nst, "keyRate": rate,
            "priced": None if priced is None else bool(priced),
        }


class ReplayTransport:
    """The `bptf` transport over a ReplaySource: one RawResult per job, from the pass
    `pass_id` (None = the source's newest pass)."""

    def __init__(self, source: ReplaySource, pass_id=None):
        self.source = source
        self.pass_id = pass_id
        self.served = 0

    def _pass(self):
        if self.pass_id is not None:
            return self.pass_id
        ps = self.source.passes()
        return ps[-1][0] if ps else None

    async def run(self, jobs):
        pid = self._pass()
        for job in jobs:
            await asyncio.sleep(0)          # stay a good citizen of the event loop
            row = self.source.row(pid, job.item, job.quality) if pid is not None else None
            if row is None:
                yield sq.RawResult(job.id, False, None, "no stored book", 1)
                continue
            if job.meta:
                row.update({k: v for k, v in job.meta.items() if v is not None})
            self.served += 1
            yield sq.RawResult(job.id, True, [row], None, 1)

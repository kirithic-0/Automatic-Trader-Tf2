"""One book on demand, for the agent's price check before an offer (2026-10-18,
trading/handoff.md Phase 2.5, gap 4).

The agent runs on the user's PC; only the collector's transport can get a book. So
the collector serves a small HTTP endpoint on the VM's 127.0.0.1 (never a public port),
and the PC reaches it through an SSH tunnel, the same way `pull.py` reaches the sync
server:

    ssh -N -L 127.0.0.1:50052:127.0.0.1:50052 azureuser@<vm>
    GET http://127.0.0.1:50052/book?item=Team%20Captain&quality=6
    GET http://127.0.0.1:50052/health

The request goes to the same transport as the pass, ahead of everything (`CHECK_PRIORITY`),
and the answer is the book as stored: both sides with `total_keys` folded in, every
listing's id / steamId / flags, the listers' trade-offer URLs, `scraped_at`. Each book
fetched here is also written to `watch_snapshots` with reason 'check', so what the agent
priced an offer against is kept.

    200 {"item", "quality", "scraped_at", "key_price_ref", "watch_id", "n_buy_total",
         "n_sell_total", "sellOrders": [...], "buyOrders": [...], "tradeUrls": {...}, "ms"}
    400 a bad item / quality      502 the fetch failed      504 no book in `timeout` s
"""
import asyncio
import time

from aiohttp import web

import scrape_queue as sq
import snapshot_store

DEFAULT_PORT = 50052
HOST = "127.0.0.1"               # never 0.0.0.0: SSH is the only way in
CHECK_PRIORITY = -20             # ahead of watch ticks (-10) and the pass (0)
TIMEOUT = 30.0                   # s; a lane's own job timeout is 25 s
MAX_CONCURRENT = 4               # checks in flight at once; more wait their turn


class FetchFailed(RuntimeError):
    pass


async def fetch_book(queue, db_engine, item, quality, reason="check", priority=CHECK_PRIORITY):
    """Fetch one book now and store it in `watch_snapshots`. Returns the stored-form dict.
    Raises ValueError (bad quality) or FetchFailed."""
    req = sq.bptf_request(item, quality=quality, priority=priority)    # ValueError on q5 etc.
    fallback = snapshot_store.latest_key_rate(db_engine)
    fallback = fallback["ref_per_key"] if fallback else None
    t0 = time.time()
    async for result in queue.run([req]):
        if not (result.ok and result.data is not None):
            raise FetchFailed(result.error or "no book")
        row = result.data
        rate = row.get("keyRate") or fallback
        wid = snapshot_store.insert_watch_snapshot(db_engine, item, row, rate, reason=reason)
        book = snapshot_store.priced_book(row, rate)
        return {"item": item, "quality": quality, "scraped_at": row.get("scrapedAt"),
                "key_price_ref": rate, "watch_id": wid,
                "n_buy_total": row.get("nBuyTotal"), "n_sell_total": row.get("nSellTotal"),
                "sellOrders": book["sellOrders"], "buyOrders": book["buyOrders"],
                "tradeUrls": row.get("tradeUrls") or {},
                "ms": round((time.time() - t0) * 1000)}
    raise FetchFailed("no result")


def make_app(queue, db_engine, timeout=TIMEOUT, max_concurrent=MAX_CONCURRENT, log=print):
    slots = asyncio.Semaphore(max_concurrent)
    started = time.time()

    async def book(request):
        item = (request.query.get("item") or "").strip()
        try:
            quality = int(request.query.get("quality", "6"))
        except ValueError:
            return web.json_response({"error": "quality must be 6 or 11"}, status=400)
        if not item:
            return web.json_response({"error": "item is required"}, status=400)
        async with slots:
            try:
                out = await asyncio.wait_for(fetch_book(queue, db_engine, item, quality), timeout)
            except ValueError as e:
                return web.json_response({"error": str(e)}, status=400)
            except asyncio.TimeoutError:
                log(f"[check] {item} q{quality}: no book in {timeout:.0f}s")
                return web.json_response({"error": f"no book in {timeout:.0f}s"}, status=504)
            except FetchFailed as e:
                log(f"[check] {item} q{quality}: failed: {e}")
                return web.json_response({"error": str(e)}, status=502)
        log(f"[check] {item} q{quality}: {len(out['buyOrders'])} buys, "
            f"{len(out['sellOrders'])} sells in {out['ms']} ms")
        return web.json_response(out)

    async def health(_request):
        return web.json_response({"ok": True, "up_s": round(time.time() - started)})

    app = web.Application()
    app.router.add_get("/book", book)
    app.router.add_get("/health", health)
    return app


async def start(queue, db_engine, port=DEFAULT_PORT, host=HOST, **kw):
    """Serve until the returned runner is cleaned up (`await runner.cleanup()`)."""
    runner = web.AppRunner(make_app(queue, db_engine, **kw), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner

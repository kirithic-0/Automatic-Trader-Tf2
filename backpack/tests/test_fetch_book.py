"""The PC client (sync/fetch_book.py) against a real collector book_server (offline).

The server (collector/book_server.py) runs on its own event loop in a
thread over a fake transport; the client is the plain urllib one the agent's checks
are modelled on. Pins: a 200 comes back as the book; the server's reason reaches the
client on a 400 / 502; an address nothing listens on is "unreachable", not a hang; the
summary prints.

    python backpack/tests/test_fetch_book.py
"""
import asyncio
import os
import sys
import tempfile
import threading

import _paths  # noqa: E402
_paths.use_sync()
_paths.use_collector()
import book_server  # noqa: E402
import fetch_book  # noqa: E402
import scrape_queue as sq  # noqa: E402
import snapshot_store as ss  # noqa: E402
from pull import SyncError  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))
    if not ok:
        fails.append(name)


ROW = {"item": "Team Captain", "variant": 0, "quality": 6, "source": "app",
       "scrapedAt": "2026-10-18T15:00:00+00:00", "keyRate": 64.0, "nBuyTotal": 3, "nSellTotal": 2,
       "sellOrders": [{"id": "440_1", "steamId": "s", "user": "seller", "price": {"keys": 0, "ref": 9.6},
                       "listingType": "bot", "dealType": "instant"}],
       "buyOrders": [{"id": "440_b_x", "steamId": "b", "user": "buyer", "price": {"keys": 0, "ref": 9.0},
                      "listingType": "bot", "dealType": "instant", "spelled": True}]}


class Fake:
    async def run(self, jobs):
        for j in jobs:
            if j.item == "Team Captain":
                yield sq.RawResult(j.id, True, [dict(ROW)], None, 1)
            else:
                yield sq.RawResult(j.id, False, None, "dead-lettered", 3)


db = ss.connect(os.path.join(tempfile.mkdtemp(), "c.db"))
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
runner = asyncio.run_coroutine_threadsafe(
    book_server.start(sq.ScrapeQueue({"bptf": Fake()}), db, port=0, log=lambda *_: None), loop).result(10)
target = "127.0.0.1:%d" % runner.addresses[0][1]

b = fetch_book.fetch(target, "Team Captain", 6)
check("200 -> the book", (b["item"], b["quality"], b["watch_id"]), ("Team Captain", 6, 1))
check("prices in keys at the book's rate", b["sellOrders"][0]["price"]["total_keys"], 0.15)
check("the client's default port is the server's", fetch_book.BOOK_PORT, book_server.DEFAULT_PORT)
s = fetch_book.summary(b)
check("the summary names the item and the flags", ("Team Captain q6" in s, "spelled" in s), (True, True))
for args, want in ((("Hat", 5), "HTTP 400"), (("Gone", 6), "HTTP 502: dead-lettered")):
    try:
        fetch_book.fetch(target, *args)
        check(f"{args} raises", "returned", want)
    except SyncError as e:
        check(f"{args} -> {want}", str(e).startswith(want), True)
try:
    fetch_book.fetch("127.0.0.1:1", "Team Captain", timeout=3)
    check("nothing listening -> unreachable", "returned", "SyncError")
except SyncError as e:
    check("nothing listening -> unreachable", "unreachable" in str(e), True)

asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
loop.call_soon_threadsafe(loop.stop)
db.dispose()
print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all passed")

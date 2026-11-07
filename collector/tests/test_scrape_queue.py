# -*- coding: utf-8 -*-
"""Offline unit test for ScrapeQueue (Layer B) -- no real browser, no network.

Drives the queue against a fake lane transport (stand-in for the extracted lane
pool) and the real LocalTransport with an injected fetch, so nothing leaves the
process. Pins the interface from ADR 0002:

  * per-route request helpers set the right route + fields;
  * the bptf route builds one app URL per book and passes the lane's row through;
  * streaming yields exactly one result per request;
  * integer priority orders jobs within a transport (lower first);
  * a transport failure surfaces as ok=False with a reason;
  * a parser that raises surfaces as ok=False "parse failed";
  * bptf and local mix in one batch; the queue owns no database;
  * LocalTransport retries internally and hides the churn;
  * a bad route / missing transport fails at submit, not silently.

    python test_scrape_queue.py
"""
import os as _os, sys as _sys  # noqa: E402 -- make backpack/ importable from tests/
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import sys

import goods
import scrape_queue as sq

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-52s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


class FakeLaneTransport:
    """Stands in for the extracted lane pool (Layer A). Records the order jobs
    arrive (to prove priority ordering) and returns canned bodies per URL."""

    def __init__(self, bodies_by_url=None, fail_ids=()):
        self.bodies_by_url = bodies_by_url or {}
        self.fail_ids = set(fail_ids)
        self.seen = []          # (id, priority) in the order handed over

    async def run(self, jobs):
        # The real bptf transport (the lane pool) parses inline and returns the
        # finished row, so the fake returns a row too (one-element payload).
        for job in jobs:
            self.seen.append((job.id, job.priority))
            if job.id in self.fail_ids:
                yield sq.RawResult(job.id, False, None, "fake: forced fail", 2)
                continue
            row = {"item": job.item, "variant": job.variant,
                   "counts": {"sell": 0, "buy": 0}, "buyOrders": [], "sellOrders": []}
            yield sq.RawResult(job.id, True, [row], None, 1)
            await asyncio.sleep(0)


class Flaky:
    """A sync fetch that fails a few times then succeeds -- to exercise retry."""

    def __init__(self, fail_times, body='{"ok": true}'):
        self.fail_times = fail_times
        self.body = body
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")
        return self.body


async def main():
    print("=== request helpers ===")
    r = sq.bptf_request("Fried Batter", quality=11)
    check("bptf route", (r.route, r.item, r.variant, r.quality, r.url),
          ("bptf", "Fried Batter", 0, 11, None))
    r = sq.local_request("https://pricedb.io/x", priority=-1)
    check("local route + priority", (r.route, r.url, r.priority), ("local", "https://pricedb.io/x", -1))

    print("\n=== bptf route: one app book, parse is a passthrough (lane parses inline) ===")
    urls = sq.ROUTES["bptf"].build_urls(sq.bptf_request("Fried Batter", quality=11))
    check("one app URL: the legacy kind's", urls, [goods.app_url("Fried Batter", goods.legacy(11))])
    check("... strict as LEGACY_STRICT says", "killstreakTier=0" in urls[0], goods.LEGACY_STRICT)
    row = {"counts": {"sell": 0, "buy": 0}, "buyOrders": [], "sellOrders": []}
    data = sq.ROUTES["bptf"].parse(sq.bptf_request("X", 1), [row])
    check("parse passes the lane's row straight through", data["counts"], {"sell": 0, "buy": 0})

    print("\n=== streaming: exactly one result per request ===")
    lane = FakeLaneTransport()
    q = sq.ScrapeQueue({"bptf": lane})
    reqs = [sq.bptf_request("A", 1), sq.bptf_request("B", 2), sq.bptf_request("C", 3)]
    results = await q.collect(reqs)
    check("one result per request", len(results), 3)
    check("every request came back once",
          sorted(r.request.item for r in results), ["A", "B", "C"])
    check("all ok", all(r.ok for r in results), True)

    print("\n=== integer priority orders jobs within a transport (lower first) ===")
    lane = FakeLaneTransport()
    q = sq.ScrapeQueue({"bptf": lane})
    await q.collect([sq.bptf_request("A", 1, priority=5),
                     sq.bptf_request("B", 2, priority=1),
                     sq.bptf_request("C", 3, priority=3)])
    check("handed over lowest-priority-first", [p for _, p in lane.seen], [1, 3, 5])

    print("\n=== a transport failure surfaces as ok=False with a reason ===")
    lane = FakeLaneTransport(fail_ids={0})
    q = sq.ScrapeQueue({"bptf": lane})
    res = await q.collect([sq.bptf_request("A", 1)])
    check("failed request is ok=False", res[0].ok, False)
    check("carries the transport's reason", "forced fail" in (res[0].error or ""), True)
    check("no data on failure", res[0].data, None)

    print("\n=== a parser that raises surfaces as ok=False 'parse failed' ===")
    # local route tries JSON on a body that starts with '{' but is malformed.
    local = sq.LocalTransport(fetch=lambda url: "{ not json", max_attempts=1)
    q = sq.ScrapeQueue({"local": local})
    res = await q.collect([sq.local_request("http://x")])
    check("parse error is ok=False", res[0].ok, False)
    check("says parse failed", (res[0].error or "").startswith("parse failed"), True)

    print("\n=== bptf + local mix in one batch (own transports, no DB) ===")
    lane = FakeLaneTransport()
    local = sq.LocalTransport(fetch=lambda url: '{"feed": [1, 2, 3]}')
    q = sq.ScrapeQueue({"bptf": lane, "local": local})
    res = await q.collect([sq.bptf_request("A", 1),
                           sq.local_request("http://pricedb"),
                           sq.bptf_request("B", 2)])
    check("all three came back", len(res), 3)
    check("all ok", all(r.ok for r in res), True)
    local_res = [r for r in res if r.request.route == "local"][0]
    check("local data is parsed JSON", local_res.data, {"feed": [1, 2, 3]})

    print("\n=== LocalTransport retries internally, hides the churn ===")
    flaky = Flaky(fail_times=2)
    local = sq.LocalTransport(fetch=flaky, max_attempts=3)
    q = sq.ScrapeQueue({"local": local})
    res = await q.collect([sq.local_request("http://x")])
    check("succeeded after retries", res[0].ok, True)
    check("attempts reported", res[0].attempts, 3)
    check("data parsed", res[0].data, {"ok": True})

    local = sq.LocalTransport(fetch=Flaky(fail_times=99), max_attempts=2)
    q = sq.ScrapeQueue({"local": local})
    res = await q.collect([sq.local_request("http://x")])
    check("gives up as ok=False", res[0].ok, False)
    check("says local fetch failed", "local fetch failed" in (res[0].error or ""), True)

    print("\n=== a bad request fails at submit, not silently ===")
    q = sq.ScrapeQueue({"bptf": FakeLaneTransport()})
    try:
        await q.collect([sq.ScrapeRequest(route="nope")])
        check("unknown route raises", False, True)
    except ValueError:
        check("unknown route raises ValueError", True, True)
    q = sq.ScrapeQueue({})                       # no bptf transport wired in
    try:
        await q.collect([sq.bptf_request("A", 1)])
        check("missing transport raises", False, True)
    except ValueError:
        check("missing transport raises ValueError", True, True)

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


asyncio.run(main())

"""ScrapeQueue -- Layer B: work in, parsed results out, no database.

You submit scrape requests and stream back one result per request. The queue
never touches the database and never learns what a "pass" or a "baseline" is
(ADR 0002). Three layers, each aware only of the one below it:

    Layer A  TRANSPORTS           how to get a book (the live site transport, a replay
       |                          of stored books, or plain local HTTP). Retries + pacing live here.
    Layer B  ScrapeQueue          <-- this file
       |                          route + prioritise + stream results
    Layer C  YOUR DB CODE         pass tracking, valid-book, pricedb pairing

Usage::

    q = ScrapeQueue({"bptf": ReplayTransport(src), "local": LocalTransport()})
    reqs = [bptf_request("Team Captain", quality=6),
            local_request("https://pricedb.io/...", priority=-1)]
    async for result in q.run(reqs):
        result.ok        # True / False
        result.data      # parsed row (bptf book | pricedb JSON); None if failed
        result.error     # short reason; None if ok
        result.request   # the request submitted, so you know WHICH item this is
        result.attempts  # how many tries it took

Decisions (ADR 0002): routed and marked at insert time; per-route request
helpers; routes built into this module; integer priority (lower runs first,
within a transport); streaming, exactly one result per request; retries sealed
inside Layer A; the queue owns no persistence.
"""
import asyncio
import json
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import goods

USER_AGENT = "tf2-orderbook-collector"



# ===========================================================================
# what you submit / what you get back
# ===========================================================================
@dataclass
class ScrapeRequest:
    """One unit of work. Build these through the per-route helpers below rather
    than by hand, so a route can never be handed the wrong fields."""
    route: str
    priority: int = 0            # lower runs first, within its transport
    # route-specific fields (only the ones a route needs are set)
    item: Optional[str] = None
    variant: Optional[int] = None
    quality: int = 6            # a Valve quality id on the app route (goods.QIDS)
    url: Optional[str] = None
    # extra fields the transport stamps onto the row (bptf: effect name, supply,
    # priced flag) so a ScrapeQueue-driven scrape produces the same row as today.
    meta: Optional[dict] = None


@dataclass
class ScrapeResult:
    """Exactly one of these per submitted request. `data` is opaque to the queue
    -- whatever the route's parser produced (a bptf book row, pricedb JSON, ...)."""
    ok: bool
    request: ScrapeRequest
    data: object = None
    error: Optional[str] = None
    attempts: int = 0


def bptf_request(item: str, variant: int = 0, priority: int = 0, effect_name: str = "",
                 exist=None, priced=None, quality: int = 6,
                 defindex=None, kind=None) -> ScrapeRequest:
    """One backpack.tf book: the item's listings as next.backpack.tf's app loads
    them. `kind` (a goods.Kind) says how it
    is asked for -- its quality, strict or not -- and rides along in the meta; without
    one it is a legacy book of `quality` (6 or 11). Every book is the craftable, plain
    good; Unusuals (5) are refused until the app's effect filter is verified. `variant`
    is always 0 for these.

    `defindex` (from the roster) rides along so the writer can build the pricedb SKU
    without a name-join. `effect_name`/`exist`/`priced` are stamped onto the stored
    row as before."""
    if kind is not None:
        quality = kind.quality
    if quality not in goods.QIDS:
        raise ValueError(f"quality {quality} is not on the app route "
                         f"(only {goods.QIDS})")
    meta = {"effectName": effect_name, "existCurrent": exist,
            "priced": priced, "defindex": defindex}
    if kind is not None:
        meta.update(kind.meta())
    return ScrapeRequest(route="bptf", priority=priority, item=item, variant=variant,
                         quality=quality, meta=meta)


def local_request(url: str, priority: int = 0) -> ScrapeRequest:
    """A plain HTTP GET from our own IP -- no browser, no proxy (e.g. pricedb)."""
    return ScrapeRequest(route="local", priority=priority, url=url)


# ===========================================================================
# Layer A interface -- what a transport must do
# ===========================================================================
@dataclass
class Job:
    """A transport's unit of work: the URL(s) for one request -- one for a bptf
    book (the classifieds URL, informational) and one
    for a local fetch.

    `item`/`variant`/`meta` carry the request's semantic fields through to a
    transport that needs them (the bptf transport stamps them onto the row);
    the local transport ignores them."""
    id: int
    urls: list
    priority: int = 0
    item: Optional[str] = None
    variant: Optional[int] = None
    quality: int = 6
    meta: Optional[dict] = None


@dataclass
class RawResult:
    """A transport's answer for one Job: the response body per URL (in order), or
    a failure. A transport yields EXACTLY ONE of these per job, after it has
    exhausted its own retries -- the queue never sees the retry churn."""
    id: int
    ok: bool
    bodies: Optional[list] = None
    error: Optional[str] = None
    attempts: int = 0


class Transport:
    """Layer A contract. A transport is handed a list of Jobs (already sorted by
    priority) and yields one RawResult per job as each finishes. It owns its own
    concurrency, pacing and retries.

    `LocalTransport` below is a complete implementation. The `bptf` transport is
    injected from outside: the live site transport (not in this repository) or
    `replay_source.ReplayTransport`, and faked in tests. This class is only documentation -- transports are
    duck-typed, they just need `async def run`."""

    async def run(self, jobs):
        raise NotImplementedError


# ===========================================================================
# routes -- built into this module (ADR 0002). A route bundles a transport with
# how to build its URL(s) and how to parse the response into `data`.
# ===========================================================================
@dataclass
class Route:
    name: str
    transport: str
    build_urls: Callable[[ScrapeRequest], list]
    parse: Callable[[ScrapeRequest, list], object]


def _bptf_urls(req: ScrapeRequest) -> list:
    # One book, one app call. The URL is the one the app shows for this book; the
    # transport may use it or not (a replay ignores it).
    return [goods.app_url(req.item, goods.Kind.of(req.quality, req.meta))]


def _bptf_parse(req: ScrapeRequest, payload: list):
    # The bptf transport parses inline (so it can retry a bad response itself) and
    # hands back the finished row -- so this is a passthrough. (The local route
    # below does its own decoding, because LocalTransport returns raw bodies.)
    return payload[0] if payload else None


def _local_parse(req: ScrapeRequest, bodies: list):
    # JSON when it looks like JSON (pricedb), text otherwise. A malformed body
    # that claims to be JSON raises here, which the queue surfaces as ok=False.
    body = bodies[0] or ""
    if body.lstrip()[:1] in ("{", "["):
        return json.loads(body)
    return body


ROUTES = {
    "bptf": Route("bptf", transport="bptf", build_urls=_bptf_urls, parse=_bptf_parse),
    "local": Route("local", transport="local",
                   build_urls=lambda req: [req.url], parse=_local_parse),
}


# ===========================================================================
# LocalTransport -- a complete Layer A transport for the `local` route
# ===========================================================================
def _http_get(url: str, timeout: float = 30.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


class LocalTransport:
    """Plain HTTP from our own IP: no browser, no proxy. Bounded concurrency and
    a small retry, all sealed in here. `fetch` is injectable so tests never touch
    the network."""

    def __init__(self, fetch: Callable[[str], str] = None, concurrency: int = 4,
                 max_attempts: int = 3):
        self._fetch = fetch or _http_get
        self._concurrency = concurrency
        self._max_attempts = max_attempts

    async def _fetch_one(self, url: str):
        last = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                body = await asyncio.to_thread(self._fetch, url)
                return body, None, attempt
            except Exception as e:
                last = repr(e)
        return None, f"local fetch failed after {self._max_attempts}: {last}", self._max_attempts

    async def run(self, jobs):
        sem = asyncio.Semaphore(self._concurrency)
        out = asyncio.Queue()

        async def one(job: Job):
            async with sem:
                bodies, err, attempts = [], None, 0
                for url in job.urls:
                    body, e, a = await self._fetch_one(url)
                    attempts += a
                    if e:
                        err, bodies = e, None
                        break
                    bodies.append(body)
                await out.put(RawResult(job.id, err is None, bodies, err, attempts))

        tasks = [asyncio.create_task(one(j)) for j in jobs]
        try:
            for _ in range(len(jobs)):
                yield await out.get()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


# ===========================================================================
# ScrapeQueue -- Layer B
# ===========================================================================
class ScrapeQueue:
    """Route requests to their transport, prioritise them, and stream back one
    parsed result per request. Owns no database (ADR 0002)."""

    def __init__(self, transports: dict):
        # {transport_name: Transport}. Injected, so the queue depends on Layer A's
        # interface and never on a concrete lane pool.
        self._transports = transports

    def _plan(self, requests):
        """Assign each request an id, build its job, group jobs by transport.
        Raises early on an unknown route or a route with no transport wired in --
        a bad request should fail when you submit it, not silently vanish."""
        jobs_by_transport = defaultdict(list)
        req_by_id, route_by_id = {}, {}
        for i, req in enumerate(requests):
            route = ROUTES.get(req.route)
            if route is None:
                raise ValueError(f"unknown route {req.route!r}")
            if route.transport not in self._transports:
                raise ValueError(
                    f"route {req.route!r} needs transport {route.transport!r}, "
                    f"which was not given to ScrapeQueue")
            jobs_by_transport[route.transport].append(
                Job(id=i, urls=route.build_urls(req), priority=req.priority,
                    item=req.item, variant=req.variant, quality=req.quality,
                    meta=req.meta))
            req_by_id[i], route_by_id[i] = req, route
        return jobs_by_transport, req_by_id, route_by_id

    async def run(self, requests):
        """Stream `ScrapeResult`s as they complete. Exactly one per request.

        Each transport is driven concurrently, so a `local` request never waits
        behind a big `bptf` batch. Within a transport, jobs are handed over in
        priority order (lower first). A transport failure surfaces as ok=False
        with its reason; a parser that raises surfaces as ok=False "parse failed".
        """
        requests = list(requests)
        if not requests:
            return
        jobs_by_transport, req_by_id, route_by_id = self._plan(requests)

        out = asyncio.Queue()

        async def drive(name, jobs):
            jobs.sort(key=lambda j: j.priority)     # lower runs first, stable
            async for raw in self._transports[name].run(jobs):
                await out.put(raw)

        tasks = [asyncio.create_task(drive(name, jobs))
                 for name, jobs in jobs_by_transport.items()]
        try:
            for _ in range(len(requests)):
                raw = await out.get()
                req, route = req_by_id[raw.id], route_by_id[raw.id]
                if not raw.ok:
                    yield ScrapeResult(False, req, None, raw.error, raw.attempts)
                    continue
                try:
                    data = route.parse(req, raw.bodies)
                except Exception as e:
                    yield ScrapeResult(False, req, None, f"parse failed: {e!r}",
                                       raw.attempts)
                    continue
                yield ScrapeResult(True, req, data, None, raw.attempts)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def collect(self, requests) -> list:
        """Non-streaming convenience: the whole list at the end. Prefer run() for
        a real pass, where you want to persist results as they land."""
        return [r async for r in self.run(requests)]

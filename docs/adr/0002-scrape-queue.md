# ADR 0002 — A routed, streaming scrape queue as its own module

- **Status:** Accepted
- **Date:** 2026-09-13
- **Scope:** `collector/scrape_queue.py`, carved out of the collector's original all-in-one pool

## Context

The first collector did everything in one class. It held the work queue, drove the fetch lanes,
wrote snapshots to the database, tracked passes, refreshed the pricedb feed and paired
baselines. That is three concerns fused into one class.

We want the middle concern on its own: a module you hand scrape work to and that streams parsed
results back, knowing nothing about the database. Three layers, each aware only of the one
below it:

```
Layer A — TRANSPORTS     fetch a URL for a route (the site lanes; a plain HTTP GET)
   │  "fetch this, paced and retried, and give me the response"
Layer B — SCRAPE QUEUE   ← this ADR
   │  "here is work to scrape; stream me parsed results"
Layer C — DB CODE        pass tracking, valid-book checks, pricedb pairing
```

## Decision

Introduce **`ScrapeQueue`** (Layer B). You submit requests, it fetches each one through its
route's transport (Layer A), parses the response and streams the results back. It touches no
database.

### The interface (the whole surface)

```python
q = ScrapeQueue({"bptf": site_transport, "local": LocalTransport()})   # Layer A injected in

reqs = [
    bptf_request("Fried Batter"),              # one book from the site
    local_request("https://pricedb.io/...", priority=-1),  # plain HTTP, jumps the line
    ...
]

async for result in q.run(reqs):               # results stream back as they finish
    result.ok        # True / False
    result.data      # parsed row (a book, or pricedb records); None if failed
    result.error     # short reason ("dead-lettered: 3x empty"); None if ok
    result.request   # the request submitted, so you know WHICH item this is
    result.attempts  # how many tries it took
```

### The decisions behind it

- **Routed, marked at insert time.** Every request carries a `route` that decides its
  transport: `bptf` for a book from the site, `local` for a plain HTTP GET (pricedb). You pick
  the route when you build the request, by name, so a nonsensical combination can be refused.
- **Per-route request helpers.** `bptf_request(item, priority=0, quality=6, ...)` and
  `local_request(url, priority=0)` are the front doors. Each demands exactly the fields its
  route needs, so a wrong combination fails when you write it, not mid-pass.
- **Routes are built into the module.** `bptf` and `local` ship inside `scrape_queue.py`.
  Adding a site later means adding a route recipe *in this module* (a URL builder and a parser),
  not registering one from outside. That is simpler to reason about. The cost is that a new site
  is a small edit here rather than a plug-in.
- **Integer priority, lower runs first.** The default is 0. A lower number runs sooner within
  its transport, and ties are broken by submit order. Because `local` and `bptf` are separate
  transports, a `local` request never waits behind a big `bptf` pass.
- **Streaming, one result per request.** Results arrive as they land, exactly one per request.
  All the retry churn (back-off, re-queue, dead-lettering after N attempts) stays sealed inside
  Layer A. You see only the final verdict.
- **The queue owns no persistence.** It has no `pass_id`, no key rate, no pricedb pairing and no
  snapshot writes. `result.data` is opaque to the queue: it is whatever that route's parser
  produced. All of Layer C lives outside it.

## Consequences

- The queue is reusable and testable with no database and no network. Feed it requests against
  a fake transport and assert on the streamed results (`collector/tests/`).
- The fetch lanes became a plain Layer A transport behind a small fetch interface. The writer,
  the pass tracking and the baselines moved out to one Layer C composition,
  `scrape_run.collect_pass`.
- Adding a site is a route recipe in this module, by deliberate choice. Revisit that only if
  routes start coming from outside the project.
- One reconciliation: the site transport parsed each response inline, so that a response that
  was not the requested book could be retried on the lane. It therefore returns the finished
  row, and the `bptf` route's `parse` is a passthrough. The "route parses the response bodies"
  rule still holds for `local`.

"The scrape queue is work-in, results-out and owns no database" is settled. Don't re-fuse it
with persistence.

## In this repository

The site's Layer A transport is not published. `collector/replay_source.py`'s `ReplayTransport`
takes its place: it answers each `bptf` job with the book the sample database stored for that
good in one pass. That this works with no change above Layer A is the payoff of the
decision: the queue, `scrape_run`'s pass loop, the writer, the watchlist and the book server
run unchanged on recorded data.

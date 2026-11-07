"""The collector's pass loop, wired to a replay of stored books.

The composition root of the three layers (ADR 0002):

    Layer A  a transport               here: replay_source.ReplayTransport (stored books)
    Layer B  ScrapeQueue (scrape_queue) route + prioritise + stream results
    Layer C  here                       stream results into snapshot_store

The live collector ran the same loop every hour with a transport that fetched each book
from backpack.tf; that transport is not in this repository. Replaying the sample through
this loop writes a database the analysis engine reads exactly like the live one:

    python collector/scrape_run.py --db replayed.db                         # all of sample/sample.db
    python collector/scrape_run.py --db replayed.db --passes 3 --item "Team Captain"
    python collector/scrape_run.py --db replayed.db --book-port 50052       # + the book server

Each source pass becomes one pass in `--db`, keeping its original start time, so the engine's
hourly axis is the recorded one.
"""
import argparse
import asyncio
import os
import sys
import time
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import book_codec  # noqa: E402
import book_server  # noqa: E402
import goods  # noqa: E402
import held_alert  # noqa: E402
import replay_source as rs  # noqa: E402
import scrape_queue as sq  # noqa: E402
import snapshot_store  # noqa: E402
import watchlist as wl  # noqa: E402


# ---------------------------------------------------------------------------
# Layer C: the writer and one pass
# ---------------------------------------------------------------------------
async def writer(results_q, db_engine, key_price=None, quiet=False, pass_id=None):
    """Drain (item, row) results into the snapshot log until a None sentinel.

    `key_price` and `pass_id` may be zero-arg callables: the writer outlives a pass, so it
    reads them at insert time. A row's own `keyRate` (the rate the site priced that book at)
    beats the pass-level fallback."""
    if db_engine is None:
        raise ValueError("writer requires a db_engine; the snapshot log is the only persistence")
    kp_of = key_price if callable(key_price) else (lambda: key_price)
    pid_of = pass_id if callable(pass_id) else (lambda: pass_id)
    logged = 0
    while True:
        got = await results_q.get()
        try:
            if got is None:
                if not quiet:
                    print(f"[db] final: {logged} snapshots logged this run")
                return
            item, row = got
            kp = row.get("keyRate") or kp_of()
            try:
                if snapshot_store.insert_snapshot(db_engine, item, row, kp, pid_of()):
                    logged += 1
            except Exception as e:
                print(f"[db] insert failed for {item}#{row.get('variant')}: {e!r}")
        finally:
            results_q.task_done()


async def collect_pass(queue, reqs, *, db_engine, key_price_ref, pass_id, quiet=False,
                       pin_rate=False):
    """Run one pass of `reqs` through the queue and persist every result.

    A snapshot is written at its own rate (`keyRate`); `key_price_ref` is the fallback for
    a book with no listing quoted in keys. A rate that differs from the last one is recorded
    in `key_price` and becomes the fallback. With `pin_rate` (--key-price) the row rates are
    dropped and the pinned one is used throughout. Returns (ok, failed)."""
    rate = [key_price_ref]
    results_q = asyncio.Queue()
    writer_task = asyncio.create_task(writer(results_q, db_engine, lambda: rate[0], quiet,
                                             pass_id=lambda: pass_id))
    ok = failed = 0
    try:
        async for result in queue.run(reqs):
            if result.ok and result.data is not None:
                ok += 1
                row = result.data
                if pin_rate:
                    row.pop("keyRate", None)
                elif row.get("keyRate") and row["keyRate"] != rate[0]:
                    snapshot_store.record_key_rate(db_engine, row["keyRate"], "bptf-app-implied")
                    if not quiet:
                        print(f"[key] {rate[0]} -> {row['keyRate']} ref/key (from the book)")
                    rate[0] = row["keyRate"]
                await results_q.put((result.request.item, row))
            else:
                failed += 1
                if not quiet:
                    r = result.request
                    tag = f"{r.item} q{r.quality}" if r.route == "bptf" else (r.url or "?")
                    print(f"[scrape] FAILED {tag}: {result.error}")
        await results_q.join()
    finally:
        await results_q.put(None)
        try:
            await asyncio.wait_for(writer_task, 60)
        except (asyncio.TimeoutError, Exception) as e:
            print(f"[scrape] writer shutdown: {e!r}")
    return ok, failed


# ---------------------------------------------------------------------------
# the roster
# ---------------------------------------------------------------------------
def roster_kind(e: dict) -> goods.Kind:
    """A roster row -> how to ask for it: a legacy row keeps the legacy request
    (goods.LEGACY_STRICT), a band row is strict."""
    if e.get("origin") == "legacy":
        return goods.legacy(e["quality"])
    return goods.Kind(int(e["quality"]), strict=True)


def cap_per_quality(rost: list, n) -> list:
    """Keep at most `n` goods of each quality, preserving order. `n` falsy -> unchanged."""
    if not n:
        return rost
    out, seen = [], Counter()
    for e in rost:
        if seen[e["quality"]] >= n:
            continue
        seen[e["quality"]] += 1
        out.append(e)
    return out


def seed_roster_from(db_engine, source: rs.ReplaySource) -> int:
    """An empty target takes the source's admitted roster. Returns rows added."""
    from sqlalchemy import insert
    if snapshot_store.roster_entries(db_engine):
        return 0
    rows = source.roster()
    cols = {c.name for c in snapshot_store.roster.columns} - {"id"}
    with db_engine.begin() as conn:
        for r in rows:
            conn.execute(insert(snapshot_store.roster).values(**{k: v for k, v in r.items() if k in cols}))
    return len(rows)


def load_roster(db_engine, args, names=()) -> list:
    """The roster: the database's fixed list of admitted, plain Unique/Strange goods
    (snapshot_store.roster_entries). `names`, --liquid-qualities and --liquid-limit narrow
    what this run collects, not the roster."""
    rost = snapshot_store.roster_entries(db_engine)
    by = Counter(e["origin"] for e in rost)
    print(f"[roster] {len(rost)} goods: " + ", ".join(f"{n} {o}" for o, n in sorted(by.items())))
    try:
        qids = {int(q) for q in str(args.liquid_qualities).split(",") if str(q).strip()}
    except (ValueError, AttributeError):
        qids = set(goods.QIDS)
    rost = [e for e in rost if e["quality"] in (qids or set(goods.QIDS))]
    if names:
        want = set(names)
        rost = [e for e in rost if e["item"] in want]
        missing = want - {e["item"] for e in rost}
        print(f"[roster] narrowed to {len(want)} name(s) -> {len(rost)} goods"
              + (f"; not in the roster: {sorted(missing)}" if missing else ""))
    if getattr(args, "liquid_limit", None):
        rost = cap_per_quality(rost, args.liquid_limit)
        print(f"[roster] capped at {args.liquid_limit}/quality -> {len(rost)} goods")
    return rost


def collect_items(args) -> list:
    names = list(getattr(args, "item", None) or [])
    if getattr(args, "items_file", None):
        with open(args.items_file, encoding="utf-8") as f:
            names += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    return names


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
async def run(args):
    if not args.db:
        raise SystemExit("[abort] no --db given: the database is the only persistence.")
    source = rs.ReplaySource(args.source)
    db_engine = snapshot_store.connect(args.db)
    print(f"[db] {args.db} ({snapshot_store.snapshot_count(db_engine)} existing rows); "
          f"replaying {args.source}")
    added = seed_roster_from(db_engine, source)
    if added:
        print(f"[roster] seeded {added} goods from the source")

    transport = rs.ReplayTransport(source)
    queue = sq.ScrapeQueue({"bptf": transport, "local": sq.LocalTransport()})

    watch_task = None
    if getattr(args, "watchlist_every", 0) and os.path.exists(args.watchlist):
        watch_task = asyncio.create_task(wl.watch_loop(
            queue, db_engine, args.watchlist, args.watchlist_every, args.quiet,
            alerter=held_alert.Alerter(args.alert_webhook_file)))
        print(f"[watch] {args.watchlist} every {args.watchlist_every:.0f}s")

    book_srv = None
    if getattr(args, "book_port", 0):
        try:
            book_srv = await book_server.start(queue, db_engine, port=args.book_port)
            print(f"[check] on-demand books on {book_server.HOST}:{args.book_port}")
        except OSError as e:
            print(f"[check] could not listen on {args.book_port}: {e}")

    try:
        roster = load_roster(db_engine, args, collect_items(args))
        if not roster:
            print("[abort] nothing to collect: the roster is empty.")
            return
        rate = args.key_price
        if not rate:
            last = snapshot_store.latest_key_rate(db_engine)
            rate = last["ref_per_key"] if last else (source.key_rates() or [(None, None, None)])[-1][1]
        run_t0 = time.time()
        for n, (src_pass, started_at) in enumerate(source.passes(args.passes), 1):
            t0 = time.time()
            transport.pass_id = src_pass
            pass_id = snapshot_store.start_pass(db_engine, n_items=len(roster), source="replay",
                                                started_at=started_at)
            reqs = [sq.bptf_request(e["item"], kind=roster_kind(e), defindex=e.get("defindex"),
                                    priced=True) for e in roster]
            ok, failed = await collect_pass(queue, reqs, db_engine=db_engine, key_price_ref=rate,
                                            pass_id=pass_id, quiet=args.quiet,
                                            pin_rate=bool(args.key_price))
            snapshot_store.finish_pass(db_engine, pass_id, n_pages=len(reqs))
            dict_id = book_codec.ensure_dict(args.db)
            if dict_id is not None:
                snapshot_store.reset_codec(db_engine)
                print(f"[db] trained book dictionary {dict_id}: books are stored zstd-compressed from now on")
            print(f"[pass {n}] source pass {src_pass} ({started_at[:16]}): {ok} ok, {failed} failed "
                  f"in {time.time() - t0:.1f}s")
            if args.pace:
                await asyncio.sleep(args.pace)
            if args.stop_after and time.time() - run_t0 >= args.stop_after:
                print(f"[stop] --stop-after {args.stop_after:.0f}s reached after {n} passes")
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[stop] interrupted")
    finally:
        if book_srv is not None:
            await book_srv.cleanup()
        if watch_task is not None:
            watch_task.cancel()
            try:
                await watch_task
            except (asyncio.CancelledError, Exception):
                pass
        source.close()
        db_engine.dispose()


def build_parser():
    p = argparse.ArgumentParser(description="collector pass loop over a replay of stored books")
    p.add_argument("--source", default=os.path.join(os.path.dirname(_HERE), "sample", "sample.db"),
                   help="the stored database to replay (default: sample/sample.db)")
    p.add_argument("--db", default=None, help="the database to write (created if missing)")
    p.add_argument("--passes", type=int, default=None, help="replay only the first N passes")
    p.add_argument("--pace", type=float, default=0.0, help="seconds to wait between passes")
    p.add_argument("--watch", type=float, default=0.0,
                   help="the live collector's pass interval (kept for the CLI; a replay runs "
                        "the stored passes back to back, see --pace)")
    p.add_argument("--stop-after", type=float, default=None, help="stop after this many seconds")
    p.add_argument("--key-price", type=float, default=None,
                   help="pin the ref-per-key rate instead of each book's own")
    p.add_argument("--item", action="append", help="only this item (repeatable)")
    p.add_argument("--items-file", default=None, help="a file of item names, one per line")
    p.add_argument("--liquid-qualities", default=",".join(map(str, goods.QIDS)),
                   help="comma-separated quality ids (default: 6 Unique, 11 Strange)")
    p.add_argument("--liquid-limit", type=int, default=None, help="cap goods PER quality")
    p.add_argument("--watchlist", default=wl.DEFAULT_PATH,
                   help="the held-item watchlist (JSON, re-read every tick; edit with watchlist.py)")
    p.add_argument("--watchlist-every", type=float, default=wl.DEFAULT_EVERY,
                   help="seconds between watchlist ticks into watch_snapshots (default 600; 0 = off)")
    p.add_argument("--book-port", type=int, default=book_server.DEFAULT_PORT,
                   help="serve one book on demand on 127.0.0.1:<port> (book_server.py; "
                        "default 50052; 0 = off)")
    p.add_argument("--alert-webhook-file", default=held_alert.WEBHOOK_FILE,
                   help="a file holding a Discord webhook URL for take-profit alerts "
                        "(kept outside the repo; missing = alerts are logged only)")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

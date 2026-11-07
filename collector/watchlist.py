"""The ~10-min watch of held items (2026-10-18, trading/handoff.md Phase 2.2).

The hourly pass is too slow to see where a held item can sell (a bid seen at the
pass can be gone 20 min later), so the collector also fetches a short list of books
every `--watchlist-every` seconds (600) and writes them to `watch_snapshots`, apart
from the passes. The list is a JSON file on the VM, re-read every tick, so editing it
needs no restart:

    [{"item": "Team Captain", "quality": 6, "entry_keys": 1.2, "added_at": "...", "note": "..."}, ...]

`entry_keys` (the ask paid) arms the take-profit alert (held_alert.py, Phase 2.4); an
entry without it is only watched. Until the agent keeps its own positions (Phase 3.6)
the list is edited by hand:

    python watchlist.py add "Team Captain" --quality 6 --entry 1.2 --note "bought"
    python watchlist.py rm  "Team Captain" --quality 6
    python watchlist.py ls

Watch requests go to the same transport as the pass, ahead of it (`WATCH_PRIORITY`), so a
tick during a pass waits for nothing but the books already in flight. No pricedb
pairing, no pass id: a tick is not a pass.
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import goods
import held_alert
import scrape_queue as sq
import snapshot_store

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "watchlist.json")
DEFAULT_EVERY = 600            # s; the user's "every ~10 min" (2026-10-17)
WATCH_PRIORITY = -10           # lower runs first; pass books are priority 0


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def load(path=DEFAULT_PATH) -> list:
    """The watched entries, deduped on (item, quality). A missing file is an empty
    list; a broken one raises (the loop logs it and keeps the last good list)."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list")
    out, seen = [], set()
    for e in data:
        item, q = (e or {}).get("item"), (e or {}).get("quality")
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}: entry without an item name: {e!r}")
        if q not in goods.QIDS:
            raise ValueError(f"{path}: {item!r} quality {q!r} is not on the app route "
                             f"(only {goods.QIDS})")
        ek = e.get("entry_keys")
        if ek is not None and (not isinstance(ek, (int, float)) or ek <= 0):
            raise ValueError(f"{path}: {item!r} entry_keys must be a positive number")
        if (item, q) not in seen:
            seen.add((item, q))
            out.append(e)
    return out


def save(entries, path=DEFAULT_PATH) -> None:
    """Write atomically: the collector may read the file mid-edit."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def add(item, quality, note="", path=DEFAULT_PATH, entry_keys=None) -> bool:
    """False if (item, quality) is already watched."""
    if quality not in goods.QIDS:
        raise ValueError(f"quality {quality} is not on the app route")
    if entry_keys is not None and entry_keys <= 0:
        raise ValueError("entry_keys must be positive")
    entries = load(path)
    if any(e["item"] == item and e["quality"] == quality for e in entries):
        return False
    entries.append({"item": item, "quality": quality, "entry_keys": entry_keys,
                    "added_at": datetime.now(timezone.utc).isoformat(), "note": note})
    save(entries, path)
    return True


def remove(item, quality, path=DEFAULT_PATH) -> bool:
    """False if it was not watched."""
    entries = load(path)
    keep = [e for e in entries if not (e["item"] == item and e["quality"] == quality)]
    if len(keep) == len(entries):
        return False
    save(keep, path)
    return True


# ---------------------------------------------------------------------------
# the tick (Layer C, beside scrape_run.collect_pass)
# ---------------------------------------------------------------------------
async def watch_tick(queue, db_engine, entries, quiet=False, alerter=None):
    """Fetch every watched book once, write it to `watch_snapshots`, and (with an
    `alerter`) check each entry's take-profit. Returns (ok, failed)."""
    if not entries:
        return 0, 0
    reqs = [sq.bptf_request(e["item"], quality=e["quality"], priority=WATCH_PRIORITY)
            for e in entries]
    fallback = snapshot_store.latest_key_rate(db_engine)
    fallback = fallback["ref_per_key"] if fallback else None
    by_key = {(e["item"], e["quality"]): e for e in entries}
    ok = failed = 0
    async for result in queue.run(reqs):
        r = result.request
        if result.ok and result.data is not None:
            row = result.data
            rate = row.get("keyRate") or fallback
            snapshot_store.insert_watch_snapshot(db_engine, r.item, row, rate, reason="tick")
            ok += 1
            if alerter is not None:
                entry = by_key[(r.item, r.quality)]
                try:
                    hit = held_alert.check(
                        entry, snapshot_store.priced_book(row, rate), row.get("scrapedAt") or "",
                        lambda sid: snapshot_store.trade_url_for(db_engine, sid))
                    await alerter.observe(entry, hit)
                except Exception as e:       # an alert bug never costs the watch
                    print(f"[alert] check failed for {r.item} q{r.quality}: {e!r}")
        else:
            failed += 1
            if not quiet:
                print(f"[watch] FAILED {r.item} q{r.quality}: {result.error}")
    return ok, failed


async def watch_loop(queue, db_engine, path=DEFAULT_PATH, every=DEFAULT_EVERY,
                     quiet=False, ticks=None, alerter=None):
    """Tick every `every` s until cancelled (or after `ticks` ticks, for tests). A
    tick that overruns starts the next one at once; a broken file keeps the last
    good list."""
    entries, n = [], 0
    while ticks is None or n < ticks:
        t0 = time.time()
        try:
            entries = load(path)
        except (OSError, ValueError) as e:
            print(f"[watch] {e} -- keeping the last good list ({len(entries)})")
        if entries:
            try:
                ok, failed = await watch_tick(queue, db_engine, entries, quiet, alerter)
                print(f"[watch] {ok} ok, {failed} failed of {len(entries)} "
                      f"in {time.time() - t0:.0f}s")
            except asyncio.CancelledError:
                raise
            except Exception as e:           # never let the watch take the pass down
                print(f"[watch] tick failed: {e!r}")
        n += 1
        if ticks is not None and n >= ticks:
            break
        await asyncio.sleep(max(0.0, every - (time.time() - t0)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="edit the held-item watchlist")
    p.add_argument("--file", default=DEFAULT_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("add", "rm"):
        s = sub.add_parser(name)
        s.add_argument("item")
        s.add_argument("--quality", type=int, default=6, help="6 Unique, 11 Strange")
        if name == "add":
            s.add_argument("--note", default="")
            s.add_argument("--entry", type=float, default=None,
                           help="keys paid; arms the take-profit alert")
    sub.add_parser("ls")
    a = p.parse_args(argv)
    if a.cmd == "add":
        print("added" if add(a.item, a.quality, a.note, a.file, a.entry) else "already watched")
    elif a.cmd == "rm":
        print("removed" if remove(a.item, a.quality, a.file) else "not watched")
    else:
        entries = load(a.file)
        for e in entries:
            ek = e.get("entry_keys")
            tp = f"entry {ek:g}, alert >= {ek * (1 + held_alert.TAKE_PROFIT):.4g}" if ek else "no entry"
            print(f"q{e['quality']:<3} {e['item']}  [{tp}]  {e.get('added_at', '')}  {e.get('note', '')}")
        print(f"{len(entries)} watched")


if __name__ == "__main__":
    main()

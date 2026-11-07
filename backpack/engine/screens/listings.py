"""Which listings of one item the agent may trade with (Stage 6, step 3.1).

From the item's newest stored book (a pass, or a 10-min watch / on-demand check, whichever
is newer), every listing with why it is or is not tradable (engine.listings): bot +
instant, not untrusted, inside the fence, a trade URL known, the item itself.

    python -m engine listings "Mr. Juice"                 # Unique
    python -m engine listings "Mr. Juice" --quality 11    # Strange
    python -m engine listings "Mr. Juice" --all           # the skipped ones too, with why
    python -m engine listings "Mr. Juice" --json out.json
"""
import json
import sqlite3
import sys

from engine import listings as el
from engine.panel import plain_sql
from engine.screens._common import fit, parser


def latest_book(con, item, quality):
    """(source, scraped/pass time, book dict) of the newest stored book, or None."""
    from book_codec import Codec
    codec = Codec.from_con(con)
    snap = con.execute(
        "SELECT s.book_json, p.started_at FROM snapshots s JOIN passes p ON p.id = s.pass_id "
        f"WHERE s.item = ? AND s.quality = ?{plain_sql(con, alias='s')} ORDER BY s.id DESC LIMIT 1",
        (item, quality)).fetchone()
    try:
        watch = con.execute(
            "SELECT book_json, scraped_at, reason FROM watch_snapshots WHERE item = ? AND quality = ?"
            f"{plain_sql(con, 'watch_snapshots')} "
            "ORDER BY id DESC LIMIT 1", (item, quality)).fetchone()
    except sqlite3.OperationalError:
        watch = None
    if watch and (not snap or str(watch[1]) > str(snap[1])):
        return f"watch ({watch[2]})", watch[1], codec.decode(watch[0])
    if snap:
        return "pass", snap[1], codec.decode(snap[0])
    return None


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("item")
    ap.add_argument("--quality", type=int, default=6)
    ap.add_argument("--all", action="store_true", help="also list the skipped listings and why")
    ap.add_argument("--json", help="write every listing with its reason here")
    args = ap.parse_args(argv)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    got = latest_book(con, args.item, args.quality)
    if not got:
        print(f"No stored book for {args.item!r} q{args.quality}.")
        return 1
    source, when, book = got
    sids = {str(o.get("steamId")) for side in ("sellOrders", "buyOrders") for o in book.get(side) or []}
    rows = el.explain(book, el.trade_urls(con, sids))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"item": args.item, "quality": args.quality, "source": source, "at": when,
                       "listings": rows}, f, indent=1)

    print(f"{args.item} q{args.quality}: newest book from {source} at {str(when)[:19]} UTC")
    for side, title in (("sell", "BUY FROM (sell listings, cheapest first)"),
                        ("buy", "SELL INTO (buy orders, highest first)")):
        side_rows = [r for r in rows if r["side"] == side]
        ok = [r for r in side_rows if r["reason"] is None]
        print(f"\n{title}: {len(ok)} tradable of {len(side_rows)}")
        for r in side_rows if args.all else ok:
            keys = "-" if r["total_keys"] is None else f"{r['total_keys']:.4f}"
            print(f"  {keys:>9}  {fit(str(r['user']), 28):28} {r['steam_id'] or '':17}  "
                  f"{'OK' if r['reason'] is None else r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

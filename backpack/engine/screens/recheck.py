"""Re-check one item on a fresh book right before an offer (Stage 6, step 3.5).

Fetches the item's book from the VM's collector now (`GET /book`, through the SSH tunnel:
sync/fetch_book.py) and decides again with engine.recheck: buy from the cheapest tradable
sell if it still costs at most the pick's ask1 and the book is still in the universe; or
sell into the best tradable bid if it is at least --min. Each fetch is stored on the VM
(`watch_snapshots`, reason 'check'); its `watch_id` goes into the decision, and from there
into the offer log.

    python -m engine recheck "Mr. Juice" --ask1 1.6487 --target 127.0.0.1:50052
    python -m engine recheck "Mr. Juice" --ask1 1.6487 --ssh azureuser@<vm-ip> --key ~/.ssh/k.pem
    python -m engine recheck "Mr. Juice" --sell --ask1 1.6487 --min 1.7146 --target ...
    ... --json decision.json      # the decision, for the Node agent
    python -m engine recheck --key-book --target ...   # the key's own book: buy / sell one for metal

Exit code 0 = go, 1 = don't (the reason is printed), 2 = the fetch failed.
"""
import json
import os
import sys

from engine import recheck as rc
from engine.screens._common import parser

_SYNC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "sync")


def fetch(item, quality, target=None, ssh=None, key=None):
    """The book server's answer for one item (opens a tunnel when --ssh is given)."""
    if _SYNC not in sys.path:
        sys.path.insert(0, _SYNC)
    from fetch_book import BOOK_PORT, fetch as get   # noqa: E402 (sync/ is not a package)
    from pull import Tunnel                         # noqa: E402
    if ssh:
        with Tunnel(ssh, key, BOOK_PORT) as t:
            return get(t, item, quality)
    return get(target, item, quality)


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("item", nargs="?")
    ap.add_argument("--quality", type=int, default=6)
    ap.add_argument("--ask1", type=float,
                    help="the pick's ask1 (buy: the most to pay; sell: the entry, for MAX_JUMP)")
    ap.add_argument("--key-book", dest="key_book", action="store_true",
                    help="the key's own book: buy / sell one key for metal (engine.recheck.recheck_key)")
    ap.add_argument("--sell", action="store_true", help="re-check a sale instead of a buy")
    ap.add_argument("--min", type=float, default=0.0, help="sell: the least to accept (take-profit price)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", metavar="HOST:PORT", help="the book server (tunnel already open)")
    g.add_argument("--ssh", metavar="USER@HOST", help="open an SSH tunnel to the VM for this call")
    ap.add_argument("--key", help="SSH private key (.pem)")
    ap.add_argument("--max-age", type=float, default=rc.MAX_AGE_S, help="seconds (default 120)")
    ap.add_argument("--json", help="write the decision here")
    args = ap.parse_args(argv)
    if args.key_book:
        args.item, args.quality = rc.KEY, 6
    elif not args.item or args.ask1 is None:
        ap.error("an item and --ask1 (or --key-book)")

    try:
        book = fetch(args.item, args.quality, args.target, args.ssh, args.key)
    except Exception as e:   # SyncError and tunnel failures: no book, no decision
        print(f"recheck: fetch failed: {e}", file=sys.stderr)
        return 2
    if args.key_book:
        d = rc.recheck_key(book, max_age_s=args.max_age)
        d.update(item=args.item, quality=args.quality, side="key")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=1, ensure_ascii=False)
        print(f"key: {'GO' if d['ok'] else 'NO'} -- " + (
            f"ask {d['ask_ref']} / bid {d['bid_ref']} ref, {len(d['buy'])} to buy from, {len(d['sell'])} to sell into"
            if d["ok"] else d["reason"]) + f"; book {d['age_s']:.0f} s old, watch_id {d['watch_id']}")
        return 0 if d["ok"] else 1
    pick = {"item": args.item, "quality": args.quality, "ask1": args.ask1}
    d = (rc.recheck_sell(pick, book, args.min, max_age_s=args.max_age) if args.sell
         else rc.recheck_buy(pick, book, max_age_s=args.max_age))
    d.update(item=args.item, quality=args.quality, side="sell" if args.sell else "buy")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)

    l = d.get("listing") or {}
    what = (f"{l.get('total_keys'):.4f} keys, {l.get('user')} ({l.get('steam_id')})" if l else "-")
    print(f"{args.item} q{args.quality} {d['side']}: {'GO' if d['ok'] else 'NO'} -- "
          f"{what}; book {d['age_s']:.0f} s old, watch_id {d['watch_id']}"
          + ("" if d["ok"] else f"\n  why: {d['reason']}"))
    return 0 if d["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

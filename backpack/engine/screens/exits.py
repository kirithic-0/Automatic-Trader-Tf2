"""What to do with each held position now (Stage 6, step 3.7): hold, or sell and why.

Reads the agent's open positions from its ledger (backpack/agent.db) and each item's
newest stored book in trading_app.db (the 10-min watch, an on-demand check or the hourly
pass, whichever is newest -- pull the VM first: sync/pull.py), and applies the model's exit
(engine.exits): take-profit before the horizon, the best bid from the horizon on, `overdue`
past the wait. A SELL here is a plan; the sale itself is re-checked on a fresh book
(`engine recheck --sell --min <min_keys>`).

    python -m engine exits
    python -m engine exits --ledger path/to/agent.db --json exits.json
    python -m engine exits --target 127.0.0.1:50052     # fresh books from the VM instead

With --target / --ssh every position's book is fetched now from the VM's collector (as
`engine recheck` does), so a SELL is already decided on a seconds-old book; the paper
agent runs it this way every ~10 min.
"""
import json
import os
import sqlite3
import sys

from engine import exits as ex
from engine import listings as el
from engine.screens._common import fit, parser
from engine.screens.listings import latest_book
from engine.screens.picks import LEDGER
from engine.screens.recheck import fetch


def open_positions(path):
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM positions WHERE state = 'open' ORDER BY id")]
    finally:
        con.close()


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--json", help="write the decisions here")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--target", metavar="HOST:PORT", help="fetch fresh books from the book server")
    g.add_argument("--ssh", metavar="USER@HOST", help="... through a tunnel opened for this call")
    ap.add_argument("--key", help="SSH private key (.pem)")
    args = ap.parse_args(argv)

    positions = open_positions(args.ledger)
    if not positions:
        print(f"No open positions in {args.ledger}.")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump([], f)
        return 0
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    out = []
    for p in positions:
        if args.target or args.ssh:
            try:
                book = fetch(p["item"], p["quality"], args.target, args.ssh, args.key)
            except Exception as e:   # no book now: hold, and say why
                out.append({"position_id": p["id"], "item": p["item"], "quality": p["quality"],
                            "action": ex.HOLD, "why": f"fetch failed: {e}"})
                continue
            out.append(dict(ex.decide(p, book), book_source="fresh", book_at=book.get("scraped_at"),
                            watch_id=book.get("watch_id")))
            continue
        got = latest_book(con, p["item"], p["quality"])
        if not got:
            out.append({"position_id": p["id"], "item": p["item"], "action": ex.HOLD, "why": "no stored book"})
            continue
        source, when, book = got
        sids = {str(o.get("steamId")) for side in ("sellOrders", "buyOrders") for o in book.get(side) or []}
        book = dict(book, tradeUrls=el.trade_urls(con, sids), scraped_at=when)
        out.append(dict(ex.decide(p, book), book_source=source, book_at=str(when)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)

    print(f"{len(out)} open position(s):\n")
    for d in out:
        l = d.get("listing") or {}
        bid = f"{l['total_keys']:.4f}" if l.get("total_keys") is not None else "-"
        print(f"{d['action'].upper():4} {fit(str(d['item']), 34):34} {d['why']:28} best bid {bid:>8}  "
              f"min {d.get('min_keys', 0):.4f}  {d.get('hours_to_horizon', float('nan')):+.1f} h to horizon")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

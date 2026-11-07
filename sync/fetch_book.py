"""Ask the VM's collector for one fresh book (handoff Phase 2.5, gap 4) -- the PC side.

The collector serves `GET /book?item=&quality=` on the VM's 127.0.0.1:50052
(`collector/book_server.py`); this opens the SSH tunnel to it, the same
way `pull.py` does, and prints the book. The Node agent will call the same endpoint over
its own tunnel; this is the reference client and the way to try it by hand.

    python sync/fetch_book.py "Team Captain" --quality 6 --ssh azureuser@<vm-ip> --key ~/.ssh/<your-key>.pem
    python sync/fetch_book.py "Team Captain" --target 127.0.0.1:50052 --json     # tunnel already open

Each book fetched this way is stored on the VM (`watch_snapshots`, reason 'check') and
reaches the PC on the next pull.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pull import SyncError, Tunnel  # noqa: E402

BOOK_PORT = 50052            # = book_server.DEFAULT_PORT on the VM


def fetch(target, item, quality=6, timeout=45.0):
    """The book as a dict; raises SyncError with the server's reason on a non-200."""
    url = f"http://{target}/book?" + urllib.parse.urlencode({"item": item, "quality": quality})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            why = json.load(e).get("error")
        except ValueError:
            why = e.reason
        raise SyncError(f"HTTP {e.code}: {why}") from None
    except urllib.error.URLError as e:
        raise SyncError(f"collector unreachable: {e.reason}") from None


def summary(b, n=5):
    lines = [f"{b['item']} q{b['quality']}  scraped {b['scraped_at']}  {b['ms']} ms  "
             f"(key {b['key_price_ref']} ref; {b['n_buy_total']} buys / {b['n_sell_total']} "
             f"sells listed; watch_id {b['watch_id']})"]
    for side, rev in (("sellOrders", False), ("buyOrders", True)):
        rows = sorted((o for o in b[side] if (o.get("price") or {}).get("total_keys") is not None),
                      key=lambda o: o["price"]["total_keys"], reverse=rev)[:n]
        lines.append(f"  {side} (best {n}, unfenced):")
        for o in rows:
            flags = ",".join(f for f in ("strange", "spelled", "painted", "parted") if o.get(f))
            lines.append(f"    {o['price']['total_keys']:>9.4f}  {o.get('listingType', ''):<6} "
                         f"{o.get('dealType', ''):<9} {o.get('user', '')} {flags}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("item")
    ap.add_argument("--quality", type=int, default=6, help="6 Unique, 11 Strange")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ssh", metavar="USER@HOST", help="open an SSH tunnel to the VM")
    g.add_argument("--target", metavar="HOST:PORT", help="talk to this address directly")
    ap.add_argument("--key", help="SSH private key (.pem)")
    ap.add_argument("--json", action="store_true", help="print the whole book as JSON")
    a = ap.parse_args(argv)
    try:
        if a.ssh:
            with Tunnel(a.ssh, a.key, BOOK_PORT) as target:
                b = fetch(target, a.item, a.quality)
        else:
            b = fetch(a.target, a.item, a.quality)
    except SyncError as e:
        print(f"[book] {e}", file=sys.stderr)
        return 1
    print(json.dumps(b, ensure_ascii=False, indent=1) if a.json else summary(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())

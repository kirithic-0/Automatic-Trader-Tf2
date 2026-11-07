"""How sample/sample.db was built from the full collection (kept for the record; the full
database is private, so this cannot be re-run from this repository).

    python sample/build_sample.py --full path/to/trading_app.db --out sample/sample.db

What it keeps:
  * 7 days of hourly passes (--start, --days) and 50 goods (--goods): admitted, plain Unique
    and Strange goods, drawn with a fixed seed from the 300 with the most listings in the window;
  * every book of those goods in those passes, every listing in it, with three changes:
    `steamId` -> a random 12-hex-digit code (one code per account, so an account is the same
    account across books; the mapping is never written anywhere), and `user`, `id`, `ua`
    dropped;
  * the passes, the goods' roster rows, the key-rate history, the bot registry (`bot_account`,
    `bot_group_member`) re-keyed to the same codes.
What it leaves out: trade-offer URLs (`trade_url`), Steam profiles (`steam_profile`), the
user's bot labels (`bot_label`), third-party price baselines (`snapshot_baseline`,
`pricedb_items`), the held-item watch (`watch_snapshots`).

The books are re-compressed with a zstd dictionary trained on the sample itself.
"""
import argparse
import json
import os
import random
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "collector"))

import book_codec  # noqa: E402
import bot_registry  # noqa: E402
import snapshot_store  # noqa: E402

DROP = ("user", "id", "ua")
PLAIN = "craftable = 1 AND elevated = 0 AND australium = 0 AND crate = 0 AND target = 0"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--full", required=True)
    p.add_argument("--out", default=os.path.join(HERE, "sample.db"))
    p.add_argument("--start", default="2026-10-17")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--goods", type=int, default=50)
    p.add_argument("--pool", type=int, default=300)
    p.add_argument("--seed", type=int, default=2026)
    a = p.parse_args(argv)
    if os.path.exists(a.out):
        raise SystemExit(f"{a.out} exists; delete it first")

    src = sqlite3.connect(f"file:{a.full}?mode=ro", uri=True)
    codec = book_codec.Codec.from_con(src)
    end = (datetime.fromisoformat(a.start) + timedelta(days=a.days)).date().isoformat()
    passes = src.execute("SELECT * FROM passes WHERE started_at >= ? AND started_at < ? ORDER BY id",
                         (a.start, end)).fetchall()
    pids = [r[0] for r in passes]
    lo, hi = min(pids), max(pids)
    ranked = src.execute(
        f"SELECT s.item, s.quality, SUM(COALESCE(s.n_buy_page, 0) + COALESCE(s.n_sell_page, 0)) AS n "
        f"FROM snapshots s JOIN roster r ON r.item = s.item AND r.quality = s.quality AND r.admitted = 1 "
        f"AND r.craftable = 1 AND r.elevated = 0 AND r.australium = 0 AND r.crate = 0 AND r.target = 0 "
        f"WHERE s.pass_id BETWEEN ? AND ? AND s.variant = 0 AND s.quality IN (6, 11) "
        f"AND s.craftable = 1 AND s.elevated = 0 AND s.australium = 0 AND s.crate = 0 AND s.target = 0 "
        f"GROUP BY s.item, s.quality ORDER BY n DESC LIMIT ?", (lo, hi, a.pool)).fetchall()
    chosen = sorted(random.Random(a.seed).sample([(i, q) for i, q, _ in ranked], a.goods))
    print(f"{len(passes)} passes {passes[0][1][:16]} .. {passes[-1][1][:16]}; {len(chosen)} goods")

    code = {}

    def anon(sid):
        if sid is None:
            return None
        sid = str(sid)
        if sid not in code:
            c = secrets.token_hex(6)
            while c in code.values():
                c = secrets.token_hex(6)
            code[sid] = c
        return code[sid]

    def clean(o):
        o = {k: v for k, v in o.items() if k not in DROP}
        o["steamId"] = anon(o.get("steamId"))
        return o

    eng = snapshot_store.connect(a.out)          # the collector's schema
    eng.dispose()
    bot_registry.connect(a.out).close()          # + the bot registry's tables
    book_codec.connect(a.out).close()            # + book_dict
    out = sqlite3.connect(a.out)
    pcols = [d[1] for d in src.execute("PRAGMA table_info(passes)")]
    out.executemany(f"INSERT INTO passes ({', '.join(pcols)}) VALUES ({', '.join('?' * len(pcols))})", passes)
    rcols = [d[1] for d in src.execute("PRAGMA table_info(roster)")]
    for item, q in chosen:
        for r in src.execute(f"SELECT * FROM roster WHERE item = ? AND quality = ? AND {PLAIN}", (item, q)):
            out.execute(f"INSERT INTO roster ({', '.join(rcols)}) VALUES ({', '.join('?' * len(rcols))})", r)
    out.executemany("INSERT INTO key_price (id, fetched_at, ref_per_key, source) VALUES (?, ?, ?, ?)",
                    src.execute("SELECT id, fetched_at, ref_per_key, source FROM key_price WHERE fetched_at < ?",
                                (end,)).fetchall())
    scols = [d[1] for d in src.execute("PRAGMA table_info(snapshots)")]
    bj = scols.index("book_json")
    n = 0
    for item, q in chosen:
        for r in src.execute(f"SELECT * FROM snapshots WHERE item = ? AND quality = ? AND variant = 0 "
                             f"AND pass_id BETWEEN ? AND ? AND {PLAIN} ORDER BY id", (item, q, lo, hi)):
            book = codec.decode(r[bj])
            book = {side: [clean(o) for o in book.get(side) or []] for side in ("sellOrders", "buyOrders")}
            row = list(r)
            row[bj] = json.dumps(book, ensure_ascii=False)
            out.execute(f"INSERT INTO snapshots ({', '.join(scols)}) VALUES ({', '.join('?' * len(scols))})", row)
            n += 1
    for sid, first_snap, first_seen, added in src.execute("SELECT * FROM bot_account"):
        if str(sid) in code:
            out.execute("INSERT INTO bot_account VALUES (?, ?, ?, ?)", (code[str(sid)], first_snap, first_seen, added))
    for sid, grp, first_seen in src.execute("SELECT * FROM bot_group_member"):
        if str(sid) in code:
            out.execute("INSERT INTO bot_group_member VALUES (?, ?, ?)", (code[str(sid)], grp, first_seen))
    out.commit()
    print(f"{n} books, {len(code)} accounts re-keyed")
    code.clear()                                 # the mapping is never stored
    import zstandard as zstd
    texts = [t for (t,) in out.execute("SELECT book_json FROM snapshots ORDER BY random() LIMIT 3000")]
    samples = [book_codec.dumps(json.loads(t)) for t in texts]
    trained = 1
    d = zstd.train_dictionary(book_codec.DICT_SIZE, samples, dict_id=trained)
    out.execute("INSERT INTO book_dict (dict_id, created_at, trained_on_pass, n_samples, dict) "
                "VALUES (?, ?, NULL, ?, ?)", (trained, book_codec._now(), len(samples), d.as_bytes()))
    out.commit()
    book_codec.compress_plain(out)
    out.commit()
    out.execute("VACUUM")
    out.execute("PRAGMA journal_mode=DELETE")      # a read-only open leaves no -wal/-shm files
    out.close()
    print(f"dictionary {trained}; {os.path.getsize(a.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

"""How a snapshot's book is stored: zstd with a trained dictionary (2026-10-04).

`snapshots.book_json` holds the book in one of two forms:

  - TEXT: plain JSON. Every row of `trading.db`, and `trading_app.db` rows written
    before the database had a dictionary.
  - BLOB: one zstd frame of the compact JSON, compressed with a dictionary kept in
    the same database (`book_dict`). The frame header names its dictionary, so a
    row needs no extra column. `typeof(book_json)` tells the two apart in SQL.

Measured on passes 1-8 of trading_app.db (docs/data-model.md, "Storage cost of
`book_json`"): ~51 MB of JSON a pass -> ~4 MB; compressing a pass costs ~0.1 s,
decompressing ~0.03 s (json.loads of the pass is ~0.6 s). SQL can no longer look
inside a compressed book (json_each / json_extract): read it through `Codec`.

**Never delete a `book_dict` row.** Every book compressed with it becomes
unreadable. A retrained dictionary gets the next id; old rows keep theirs.

    codec = Codec.from_con(con)      # a sqlite3 connection
    book = codec.decode(value)       # book_json value (str or bytes) -> dict
    blob = codec.encode(book)        # dict -> bytes with the newest dictionary (None if none)

A fresh database starts plain: `ensure_dict` (run by scrape_run after each pass)
trains the first dictionary from the first full pass and compresses that pass.

    python book_codec.py migrate [--db trading_app.db]   # train if none, compress plain rows, VACUUM
    python book_codec.py train   [--db ...]               # a new dictionary from the latest full pass
    python book_codec.py show <snapshot id> [--db ...]    # one book as readable JSON
    python book_codec.py stats   [--db ...]               # rows and bytes by stored form
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import zstandard as zstd

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_app.db")
LEVEL = 3                 # zstd-19 was 3.75 vs 4.0 MB a pass but 19 s a pass to compress
DICT_SIZE = 112_640       # zstd's default; 113 KB stored once per dictionary
MIN_BOOKS = 500           # a pass smaller than this is too thin to train on

SCHEMA = """
CREATE TABLE IF NOT EXISTS book_dict (
    dict_id INTEGER PRIMARY KEY,      -- = the id zstd writes into every frame's header
    created_at TEXT NOT NULL,
    trained_on_pass INTEGER,
    n_samples INTEGER,
    dict BLOB NOT NULL
)"""


def dumps(book) -> bytes:
    """The compact JSON that gets compressed (no spaces; non-ASCII kept as is)."""
    return json.dumps(book, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Codec:
    """Encode/decode books against the dictionaries of one database. Safe to share
    across threads: a compressor/decompressor is made per call (~26 us / ~8 us with
    the dictionary precomputed), never shared."""

    def __init__(self, dicts=()):
        """`dicts`: (dict_id, dict bytes) pairs; the highest id encodes."""
        self._dicts = {}
        for dict_id, raw in dicts:
            d = zstd.ZstdCompressionDict(bytes(raw))
            if d.dict_id() != dict_id:
                raise ValueError(f"book_dict row {dict_id} holds a dictionary with id {d.dict_id()}")
            self._dicts[dict_id] = d
        self.newest = max(self._dicts) if self._dicts else None
        if self.newest is not None:
            self._dicts[self.newest].precompute_compress(level=LEVEL)

    @classmethod
    def from_con(cls, con):
        """Load every dictionary of this database (none if it has no `book_dict`)."""
        if not _has_dict_table(con):
            return cls()
        return cls(con.execute("SELECT dict_id, dict FROM book_dict").fetchall())

    def encode(self, book):
        """dict -> one zstd frame with the newest dictionary; None if there is none yet
        (the caller stores plain JSON)."""
        if self.newest is None:
            return None
        return zstd.ZstdCompressor(level=LEVEL, dict_data=self._dicts[self.newest]).compress(dumps(book))

    def decode(self, value):
        """A `book_json` value -> the book dict. Plain JSON text, or a zstd frame."""
        if value is None:
            return {}
        if isinstance(value, str):
            return json.loads(value)
        raw = bytes(value)
        dict_id = zstd.get_frame_parameters(raw).dict_id
        d = self._dicts.get(dict_id)
        if d is None:
            raise LookupError(f"book compressed with dictionary {dict_id}, which is not in "
                              f"book_dict (known: {sorted(self._dicts)})")
        return json.loads(zstd.ZstdDecompressor(dict_data=d).decompress(raw))


def connect(db, create=True):
    """`create=False` for read-only use (show / stats on trading.db must not add a table)."""
    con = sqlite3.connect(db, timeout=60)     # the scraper may be writing (WAL)
    if create:
        con.execute(SCHEMA)
    return con


def _has_dict_table(con):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_dict'").fetchone())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _latest_full_pass(con, min_books):
    row = con.execute("SELECT pass_id FROM snapshots WHERE pass_id IS NOT NULL GROUP BY pass_id "
                      "HAVING count(*) >= ? ORDER BY pass_id DESC LIMIT 1", (min_books,)).fetchone()
    return row[0] if row else None


def train(con, pass_id=None, min_books=MIN_BOOKS, dict_size=DICT_SIZE):
    """Train a dictionary from the books of one pass (default: the latest pass with at
    least `min_books` books), store it under the next id, and return that id. None if
    no pass is big enough. Takes ~1 s on a 2,879-book pass."""
    if pass_id is None:
        pass_id = _latest_full_pass(con, min_books)
        if pass_id is None:
            return None
    codec = Codec.from_con(con)
    samples = [dumps(codec.decode(v)) for (v,) in
               con.execute("SELECT book_json FROM snapshots WHERE pass_id = ?", (pass_id,))]
    if len(samples) < min_books:
        return None
    dict_id = (con.execute("SELECT COALESCE(MAX(dict_id), 0) FROM book_dict").fetchone()[0]) + 1
    d = zstd.train_dictionary(dict_size, samples, dict_id=dict_id)
    con.execute("INSERT INTO book_dict (dict_id, created_at, trained_on_pass, n_samples, dict) "
                "VALUES (?, ?, ?, ?, ?)", (dict_id, _now(), pass_id, len(samples), d.as_bytes()))
    con.commit()
    return dict_id


def compress_plain(con, batch=500):
    """Compress every row still stored as plain JSON with the newest dictionary.
    Each row is checked (decode(encode(book)) == book) before it is written.
    Returns the number of rows rewritten."""
    codec = Codec.from_con(con)
    if codec.newest is None:
        return 0
    done = 0
    while True:
        rows = con.execute("SELECT id, book_json FROM snapshots WHERE typeof(book_json) = 'text' "
                           "LIMIT ?", (batch,)).fetchall()
        if not rows:
            return done
        out = []
        for sid, text in rows:
            book = json.loads(text)
            blob = codec.encode(book)
            if codec.decode(blob) != book:
                raise RuntimeError(f"snapshot {sid}: compressed book does not decode back to itself")
            out.append((blob, sid))
        con.executemany("UPDATE snapshots SET book_json = ? WHERE id = ?", out)
        con.commit()
        done += len(out)


def ensure_dict(db, min_books=MIN_BOOKS):
    """The scraper's hook after each pass: if the database has no dictionary yet and a
    full pass exists, train one and compress the plain rows. Returns the new dict id,
    or None if nothing changed (the usual case)."""
    con = connect(db)
    try:
        if con.execute("SELECT 1 FROM book_dict").fetchone():
            return None
        dict_id = train(con, min_books=min_books)
        if dict_id is not None:
            compress_plain(con)
        return dict_id
    finally:
        con.close()


# -- CLI --------------------------------------------------------------------

def _stats(con):
    out = {}
    for kind, n, size in con.execute("SELECT typeof(book_json), count(*), sum(length(book_json)) "
                                     "FROM snapshots GROUP BY 1"):
        out[kind] = (n, size or 0)
    return out


def _print_stats(con, db):
    print(f"{db}: {os.path.getsize(db) / 1e6:.1f} MB on disk")
    for kind, (n, size) in sorted(_stats(con).items()):
        label = {"text": "plain JSON", "blob": "zstd"}.get(kind, kind)
        print(f"  {label:10s} {n:7d} books  {size / 1e6:8.1f} MB  {size / max(n, 1) / 1e3:6.1f} KB a book")
    if not _has_dict_table(con):
        return
    for dict_id, created, p, n in con.execute(
            "SELECT dict_id, created_at, trained_on_pass, n_samples FROM book_dict ORDER BY dict_id"):
        print(f"  dictionary {dict_id}: trained {created[:16]} on pass {p} ({n} books)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    db_after = argparse.ArgumentParser(add_help=False)     # --db also accepted after the command
    db_after.add_argument("--db", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", parents=[db_after], help="train if no dictionary, compress plain rows, VACUUM")
    t = sub.add_parser("train", parents=[db_after],
                       help="train a new dictionary (new rows use it; old rows keep theirs)")
    t.add_argument("--pass-id", type=int)
    s = sub.add_parser("show", parents=[db_after], help="print one book as readable JSON")
    s.add_argument("snapshot_id", type=int)
    sub.add_parser("stats", parents=[db_after], help="rows and bytes by stored form")
    args = ap.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")      # item and user names carry emoji

    con = connect(args.db, create=args.cmd in ("migrate", "train"))
    try:
        if args.cmd == "show":
            row = con.execute("SELECT book_json FROM snapshots WHERE id = ?", (args.snapshot_id,)).fetchone()
            if row is None:
                print(f"no snapshot {args.snapshot_id}", file=sys.stderr)
                return 2
            print(json.dumps(Codec.from_con(con).decode(row[0]), indent=1, ensure_ascii=False))
        elif args.cmd == "stats":
            _print_stats(con, args.db)
        elif args.cmd == "train":
            dict_id = train(con, args.pass_id)
            print(f"dictionary {dict_id} stored" if dict_id else "no pass big enough to train on")
        elif args.cmd == "migrate":
            _print_stats(con, args.db)
            if not con.execute("SELECT 1 FROM book_dict").fetchone():
                dict_id = train(con)
                if dict_id is None:
                    print("no pass big enough to train on -- nothing to do")
                    return 1
                print(f"trained dictionary {dict_id}")
            n = compress_plain(con)
            print(f"compressed {n} plain books; VACUUM ...")
            con.execute("VACUUM")
            _print_stats(con, args.db)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

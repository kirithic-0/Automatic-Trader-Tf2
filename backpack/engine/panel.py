"""trading_app.db (or trading.db) -> the panel: one row per (Unique/Strange) snapshot,
read-rules applied.

    panel = load_panel()            # DataFrame, sorted by series then time

Columns: snapshot_id, pass_id, t (UTC pass start), item, quality, variant,
effect_name, label, every BookRead field (engine/book.py), pdb_buy, pdb_sell
(the pricedb suggested prices paired to that snapshot; NaN when unpaired).

A **series** is one (item, quality, variant). Rows are keyed by pass_id (never
scraped_at, which is spread over a pass) and timed by the pass's wall-clock
start, per the data-model analysis rules.

The per-snapshot book reads are cached in `<cache_dir_for(db)>/panel_v<READ_KEY>.parquet`
beside this package's parent. The cache is disposable: delete it and the next
load rebuilds it from the db. New snapshots are appended incrementally
(snapshots is append-only); if the db was reset under the cache, it rebuilds.
The db is only ever opened read-only.

Which db (2026-10-17): `DEFAULT_DB` is `trading_app.db`, the live replica the collector
syncs here; the frozen classifieds history is `trading.db` (`--db trading`). The two are
never mixed (their book depths differ), and each has its own cache folder
(`cache_dir_for`). App books are zstd BLOBs: every reader decodes through
`book_decoder(con)`, never `json.loads`.
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd

from book_codec import Codec
from engine.book import BOOK_FIELDS, READ_KEY, read_book

QUALITIES = (6, 11)                    # Unique + Strange: where the fences are tuned
QUALITY_NAME = {1: "Genuine", 3: "Vintage", 5: "Unusual", 6: "Unique", 11: "Strange",
                13: "Haunted", 14: "Collector's"}
SERIES = ["item", "quality", "variant"]

# Which good a book is, beyond (item, quality) -- the collector's kind columns (2026-10-31).
# A series is only (item, quality, variant), so the engine reads the plain good alone:
# craftable, not Strange-elevated, not Australium, no crate series, not a Strangifier.
# Rows written before the columns existed are all plain.
PLAIN_KIND = {"craftable": 1, "elevated": 0, "australium": 0, "crate": 0, "target": 0}
# What load_panel reads: "admitted" = universe_sql (the agent, every screen). The private
# repository also had "all" (every good collected, for a per-group backtest); it is not in
# this copy, and asking for it raises.
SCOPES = ("admitted",)

_BACKPACK = Path(__file__).resolve().parent.parent
APP_DB = str(_BACKPACK / "trading_app.db")
TRADING_DB = str(_BACKPACK / "trading.db")
DEFAULT_DB = APP_DB
DB_ALIASES = {"app": APP_DB, "trading": TRADING_DB}
DEFAULT_CACHE_DIR = str(_BACKPACK / "engine_cache")          # trading.db's
APP_CACHE_DIR = str(_BACKPACK / "engine_cache_app")          # trading_app.db's

_META = ["snapshot_id", "pass_id", "item", "quality", "variant", "effect_name"]


def resolve_db(db):
    """A db path, or the alias 'app' / 'trading'."""
    return DB_ALIASES.get(db, db) if db is not None else DEFAULT_DB


def cache_dir_for(db):
    """Where the caches of `db` live: trading.db keeps engine_cache/, trading_app.db gets
    engine_cache_app/ (never share one: the panel cache would rebuild on every switch),
    any other db a folder beside it."""
    p = Path(resolve_db(db)).resolve()
    if p == Path(TRADING_DB).resolve():
        return DEFAULT_CACHE_DIR
    if p == Path(APP_DB).resolve():
        return APP_CACHE_DIR
    return str(p.parent / f"engine_cache_{p.stem}")


def connect_ro(db):
    """A read-only sqlite3 connection to the db."""
    return sqlite3.connect(Path(resolve_db(db)).resolve().as_uri() + "?mode=ro", uri=True)


def _columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def plain_sql(con, table="snapshots", alias=None):
    """' AND craftable = 1 AND elevated = 0 ...' (PLAIN_KIND) when `table` has the kind
    columns, else '' (an older db: every row is plain). Append it to any read of a series by
    (item, quality): once other kinds share a name (a Non-Craftable Team Captain is item
    'Team Captain', quality 6), a read without it mixes two goods."""
    if not set(PLAIN_KIND) <= _columns(con, table):
        return ""
    p = f"{alias}." if alias else ""
    # IFNULL: a replica that added a column without its default still reads plain.
    return "".join(f" AND IFNULL({p}{c}, {v}) = {v}" for c, v in PLAIN_KIND.items())


def admitted_sql(con, alias=None):
    """' AND <the plain good> AND <admitted in the roster>' -- the gate without the quality
    scope. The roster part applies once the collector's `roster` table is here with rows (an
    empty one is a roster not seeded yet, not 'nothing admitted'); a db without the kind
    columns or a roster (trading.db) gets '' and reads as it always did."""
    p = f"{alias}." if alias else ""
    sql = plain_sql(con, "snapshots", alias)
    if "item" in _columns(con, "roster") and con.execute("SELECT 1 FROM roster LIMIT 1").fetchone():
        sql += (f" AND ({p}item, {p}quality) IN (SELECT item, quality FROM roster "
                f"WHERE admitted = 1{plain_sql(con, 'roster')})")
    return sql


def universe_sql(con, alias=None):
    """The WHERE condition for the snapshots the engine reads: Unique/Strange (QUALITIES),
    the plain good, and only items the roster admits (admitted_sql). The collector watches
    more than the engine trades; a group of items enters only when the user admits it
    (wider-roster plan, 2026-10-31). Changing who is admitted changes the panel cache's
    stamp, which then re-counts and rebuilds."""
    p = f"{alias}." if alias else ""
    return f"{p}quality IN ({','.join(map(str, QUALITIES))})" + admitted_sql(con, alias)


def book_decoder(con):
    """`book_json` value -> book dict, for this db: plain JSON (trading.db) or a zstd
    frame (trading_app.db, book_codec). An unreadable JSON text reads as {}."""
    codec = Codec.from_con(con)

    def decode(value):
        try:
            return codec.decode(value)
        except ValueError:
            return {}
    return decode


def write_cache(df, path):
    """Write a cache parquet whole or not at all: a temp file, then os.replace, so a reader
    in another process (the agent's picks, `engine panel`) never opens half a file. A file
    another process holds open on Windows cannot be replaced: the write is skipped (a cache
    is disposable; the next load appends again)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_new(con, after_id):
    rows = []
    decode = book_decoder(con)
    for sid, pid, item, qual, var, eff, bj in con.execute(
            "SELECT id, pass_id, item, quality, variant, effect_name, book_json "
            f"FROM snapshots WHERE {universe_sql(con)} AND id > ? ORDER BY id",
            (after_id,)):
        rows.append((sid, pid, item, qual, var, eff, *read_book(decode(bj)).__dict__.values()))
    return pd.DataFrame(rows, columns=_META + BOOK_FIELDS)


def _stamp(con, last):
    """What a panel cache was built against, cheap to re-check (~0.1 s): the Unique/Strange row
    count up to its newest book (an index-only count; a db reset or rewrite moves it), the gate
    itself, and the admitted roster. Counting through the gate costs ~7 s on 1.4M rows (it reads
    every row's kind columns), so that count runs only when the stamp moved."""
    q = ",".join(str(x) for x in QUALITIES)
    n = con.execute(f"SELECT count(*) FROM snapshots WHERE quality IN ({q}) AND id <= ?",
                    (last,)).fetchone()[0]
    return {"last": int(last), "n_quality": int(n), "gate": universe_sql(con), "admitted": admitted_hash(con)}


def admitted_hash(con):
    """A hash of the admitted roster ('' without a roster): what moves when a group is admitted."""
    if "item" not in _columns(con, "roster"):       # (an empty roster hashes as such: fine)
        return ""
    rows = con.execute("SELECT item, quality FROM roster WHERE admitted = 1"
                       f"{plain_sql(con, 'roster')} ORDER BY item, quality").fetchall()
    return hashlib.sha1(json.dumps(rows).encode()).hexdigest()


def _read_stamp(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_stamp(stamp, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stamp, f)
    except OSError:
        pass                    # disposable: the next load just counts through the gate once



def _book_reads(con, cache_dir, use_cache):
    path = os.path.join(cache_dir, f"panel_v{READ_KEY}.parquet")
    stamp_path = path + ".stamp.json"
    cached = None
    restamp = False
    if use_cache and os.path.exists(path):
        cached = pd.read_parquet(path)
        if set(BOOK_FIELDS) - set(cached.columns):   # BookRead gained a field: re-read all
            cached = None
        elif len(cached):
            last = int(cached.snapshot_id.max())
            if _read_stamp(stamp_path) != _stamp(con, last):
                n_db = con.execute(f"SELECT count(*) FROM snapshots WHERE {universe_sql(con)} "
                                   "AND id <= ?", (last,)).fetchone()[0]
                if n_db != len(cached):      # db reset / rewritten, or the admitted set changed
                    cached = None
                restamp = True
    after = int(cached.snapshot_id.max()) if cached is not None and len(cached) else 0
    new = _read_new(con, after)
    if cached is None:
        out = new
    elif len(new):
        out = pd.concat([cached, new], ignore_index=True)
    else:
        out = cached
    if use_cache and (cached is None or len(new)):
        write_cache(out, path)
        restamp = True
    if use_cache and restamp and len(out):
        _write_stamp(_stamp(con, int(out.snapshot_id.max())), stamp_path)
    return out


def load_panel(db=DEFAULT_DB, cache_dir=None, use_cache=True, scope="admitted"):
    """The panel for every snapshot the engine reads in `db` (universe_sql; module doc).
    `scope` is "admitted", the only one in this copy (SCOPES)."""
    if scope not in SCOPES:
        raise ValueError(f"scope: one of {SCOPES}, not {scope!r}")
    cache_dir = cache_dir or cache_dir_for(db)
    con = connect_ro(db)
    try:
        reads = _book_reads(con, cache_dir, use_cache)
        passes = pd.read_sql("SELECT id AS pass_id, started_at FROM passes", con)
        base = pd.read_sql("SELECT snapshot_id, buy_total_keys AS pdb_buy, "
                           "sell_total_keys AS pdb_sell FROM snapshot_baseline", con)
    finally:
        con.close()

    passes["t"] = pd.to_datetime(passes.started_at, utc=True, format="ISO8601")
    df = (reads.dropna(subset=["pass_id"])
               .astype({"pass_id": "int64"})
               .merge(passes[["pass_id", "t"]], on="pass_id", how="inner")
               .merge(base.drop_duplicates("snapshot_id"), on="snapshot_id", how="left"))
    df = df.dropna(subset=["t"])
    # one row per series per pass (a re-scrape inside a pass keeps the latest)
    df = df.sort_values("snapshot_id").drop_duplicates(SERIES + ["pass_id"], keep="last")

    tag = df.quality.map(QUALITY_NAME).fillna(df.quality.astype(str))
    eff = df.effect_name.fillna("")
    df["label"] = df.item + " (" + tag + eff.where(eff == "", "/" + eff) + ")"
    order = SERIES + ["t"]
    return df.sort_values(order).reset_index(drop=True)


def latest_pass(panel):
    """pass_id of the most recent pass present in the panel."""
    return int(panel.loc[panel.t.idxmax(), "pass_id"])

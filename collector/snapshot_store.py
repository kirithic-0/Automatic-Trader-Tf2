"""
SQLite store for backpack.tf order-book snapshots (SQLAlchemy Core).

One rule: **raw capture never contains a judgment call.** Only observed facts are
stored; anything that needs a rule (filter Strange, dedupe bots, pick "the" ask)
is computed on read from `book_json`, never materialised here.

  1. RAW  — `snapshots`. One immutable row per (item, variant) per pass: only
     what was observed. The full book is `book_json`; the flat columns beside it
     are provenance and pre-trim counts, nothing derived. No bid, no ask — those
     involve a rule and a rule can be wrong, so they do not live here.

  2. SIDE TABLES at their own cadence — `marketplace_price` (daily stats pass),
     plus `passes`, `effects`, `pricedb_items`, `snapshot_baseline`, `key_price`.

Written with SQLAlchemy **Core** (not the ORM): the schema is `Table` objects and
queries use the expression language, so the same code moves from SQLite to
Postgres by changing only the URL in `connect()`.

Public surface used by the scraper:
    eng = connect("trading.db")                              # -> Engine
    sid = insert_snapshot(eng, item, row, key_price_ref, pass_id)
`row` is a `bptf_app.parse_search()`-shaped dict (older rows came from the
retired `extract_classifieds()` / `extract_stats()`).

`book_json` is written zstd-compressed once the database has a dictionary
(`book_codec`, 2026-10-04), plain JSON before that. Read it through
`book_codec.Codec`, never `json.loads` directly.
"""
import json
from datetime import datetime, timezone

import book_codec

from sqlalchemy import (
    Boolean, Column, Float, Index, Integer, MetaData, Table, Text,
    UniqueConstraint, create_engine, event, func, insert, select, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

metadata = MetaData()


def _kind_columns():
    """Which good a book is, beyond (item, quality) (2026-10-31). The collector writes
    only the plain good -- craftable, not Strange-elevated, not Australium, no crate
    series, no Strangifier target -- so every row holds the defaults (PLAIN_KIND). Readers
    of a series by (item, quality) still pin these (the engine's plain_sql)."""
    return [
        Column("craftable", Integer, nullable=False, server_default="1"),
        Column("elevated", Integer, nullable=False, server_default="0"),    # 11 = Strange-elevated
        Column("australium", Integer, nullable=False, server_default="0"),
        Column("crate", Integer, nullable=False, server_default="0"),       # crate / case series
        Column("target", Integer, nullable=False, server_default="0"),      # a Strangifier's target defindex
    ]


# ===========================================================================
# LAYER 1 — raw capture
# ===========================================================================
# Only observed facts. Every column is either provenance (who/when/where) or a
# count taken straight off the page. Anything needing a rule lives in Layer 2.
snapshots = Table(
    "snapshots", metadata,
    Column("id", Integer, primary_key=True),
    Column("pass_id", Integer),                   # -> passes.id
    Column("item", Text, nullable=False),
    Column("variant", Integer, nullable=False),   # = effect/particle id (0 for liquid items)
    Column("quality", Integer, nullable=False, server_default="5"),  # Valve quality id: 5 Unusual, 6 Unique, 11 Strange
    Column("effect_name", Text),                  # denormalised from effects, for readable queries
    Column("scraped_at", Text, nullable=False),   # ISO8601 UTC, from the scrape (per-page)
    Column("ingested_at", Text, nullable=False),  # when this row was written
    Column("source", Text, nullable=False),       # 'app' (since 2026-09-29) | 'classifieds' | 'stats'
    Column("exist_current", Integer),             # supply, from /unusual/<item>
    Column("priced", Integer),                    # 1 = on bp.tf's priced list
    # The rate every price in book_json was denominated against. An input that
    # was true at capture time, not a derivation — so it belongs to raw.
    Column("key_price_ref", Float),
    # Pre-trim counts. The book stored below is capped (top 7 a side), so these
    # are the ONLY record of how deep the page actually was. n_buy_page == 10
    # means the page was full and more bids exist (the autopricer's >=10 gate);
    # n_buy_clean is after Strange/spell removal, before the cap. Without these
    # the count is censored at the cap and carries no information.
    # On 'app' rows (next.backpack.tf's listing call, stored unfiltered):
    # n_*_page = listings returned (<= 30 a side), n_*_total = the side's full
    # count as the site reports it, n_*_clean = NULL (a read rule now).
    Column("n_buy_page", Integer),
    Column("n_buy_clean", Integer),
    Column("n_sell_page", Integer),
    Column("n_sell_clean", Integer),
    Column("n_buy_total", Integer),
    Column("n_sell_total", Integer),
    # The real payload. {"buyOrders":[...],"sellOrders":[...]}; each listing
    # carries user, steamId, premium, price{keys,ref,usd,raw,total_keys},
    # listingType, dealType, strange, spelled. total_keys is null on unpriced
    # listings. Everything downstream is recomputed from here.
    # Stored as plain JSON TEXT, or (SQLite only) a zstd BLOB once the database
    # has a dictionary -- book_codec.Codec reads both.
    Column("book_json", Text, nullable=False),
    *_kind_columns(),
    # Identity includes quality: a base item exists in several qualities at once (a
    # Unique and a Strange "Team Captain" are different markets) and every liquid item
    # has variant=0, so (item, variant) alone would collide across qualities.
    UniqueConstraint("item", "quality", "variant", "scraped_at",
                     name="uq_snap_item_quality_variant_time"),
)

Index("idx_snap_item_quality_variant_time",
      snapshots.c.item, snapshots.c.quality, snapshots.c.variant, snapshots.c.scraped_at)
Index("idx_snap_pass", snapshots.c.pass_id)


# ===========================================================================
# SIDE TABLE — Marketplace.tf price, on its own (daily) cadence
# ===========================================================================
# classifieds carries no Marketplace.tf box, so this comes from the slower
# stats-page pass. Its own table rather than 92%-NULL columns on a 2-hourly
# snapshots row.
marketplace_price = Table(
    "marketplace_price", metadata,
    Column("id", Integer, primary_key=True),
    Column("item", Text, nullable=False),
    Column("variant", Integer, nullable=False),
    Column("observed_at", Text, nullable=False),
    Column("usd", Float),
    Column("available", Integer),
    Column("source", Text, nullable=False),             # 'stats'
    UniqueConstraint("item", "variant", "observed_at", name="uq_mkt_item_variant_time"),
)
Index("idx_mkt_item_variant", marketplace_price.c.item, marketplace_price.c.variant)


# ===========================================================================
# side tables — unchanged
# ===========================================================================
effects = Table(
    "effects", metadata,
    Column("effect_id", Integer, primary_key=True),
    Column("effect_name", Text, nullable=False),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
)

# The pricedb baseline in force at each snapshot, fetched by the same run. Kept
# separate because a baseline fetch can fail independently of the page scrape,
# and the snapshot must be durable without waiting on a second network call.
snapshot_baseline = Table(
    "snapshot_baseline", metadata,
    Column("id", Integer, primary_key=True),
    Column("snapshot_id", Integer, nullable=False),   # -> snapshots.id
    Column("pass_id", Integer),
    Column("item", Text),
    Column("variant", Integer),
    Column("sku", Text),
    Column("scraped_at", Text),
    Column("fetched_at", Text, nullable=False),
    Column("status", Integer),                        # HTTP; 404 = SKU not carried
    Column("price_time", Integer),                    # pricedb's compute timestamp
    Column("buy_keys", Float),
    Column("buy_metal", Float),
    Column("sell_keys", Float),
    Column("sell_metal", Float),
    Column("buy_total_keys", Float),
    Column("sell_total_keys", Float),
    Column("key_price_ref", Float),
    UniqueConstraint("snapshot_id", name="uq_snapshot_baseline_snapshot"),
)
Index("idx_snapshot_baseline_sku", snapshot_baseline.c.sku, snapshot_baseline.c.price_time)
Index("idx_baseline_pass", snapshot_baseline.c.pass_id)

# One row per collection pass. scraped_at is per-page (spread over 2-4 min), so a
# pass gets an identity rather than being inferred from the clock.
passes = Table(
    "passes", metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", Text, nullable=False),
    Column("ended_at", Text),
    Column("n_pages", Integer),
    Column("n_items", Integer),
    Column("source", Text, nullable=False),
)
Index("idx_passes_started", passes.c.started_at)

pricedb_items = Table(
    "pricedb_items", metadata,
    Column("sku", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("defindex", Integer),
    Column("effect_id", Integer),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
)
Index("idx_pricedb_items_name", pricedb_items.c.name)

# key -> ref rate, cached with a TTL. One row per refresh, newest wins,
# append-only. In the DB so the TTL survives a restart.
key_price = Table(
    "key_price", metadata,
    Column("id", Integer, primary_key=True),
    Column("fetched_at", Text, nullable=False),
    Column("ref_per_key", Float, nullable=False),
    Column("source", Text, nullable=False),
)
Index("idx_key_price_fetched", key_price.c.fetched_at)


# One row per lister: its Steam trade-offer URL, from `user.tradeOfferUrl` on the app's
# listings (2026-10-17). Per account, not per listing, so it lives here and not in
# book_json. Written only when new or changed (changed_at), so a pass costs no writes
# for accounts already known; a null URL never overwrites a stored one.
trade_url = Table(
    "trade_url", metadata,
    Column("steam_id", Text, primary_key=True),
    Column("url", Text, nullable=False),
    Column("first_seen", Text, nullable=False),
    Column("changed_at", Text, nullable=False),
)
Index("idx_trade_url_changed", trade_url.c.changed_at)


# The ~10-min watch of held items (2026-10-18, handoff Phase 2.2). The same raw row
# as `snapshots` -- same columns, same book_json codec -- but its own table, so the
# hourly passes stay one clean row per item per pass and a reader never mistakes a
# watch tick for a pass. No pass_id (a tick is not a pass) and no pricedb pairing.
watch_snapshots = Table(
    "watch_snapshots", metadata,
    Column("id", Integer, primary_key=True),
    Column("item", Text, nullable=False),
    Column("variant", Integer, nullable=False),
    Column("quality", Integer, nullable=False),
    Column("scraped_at", Text, nullable=False),
    Column("ingested_at", Text, nullable=False),
    Column("source", Text, nullable=False),        # 'app'
    Column("key_price_ref", Float),
    Column("n_buy_page", Integer),
    Column("n_sell_page", Integer),
    Column("n_buy_total", Integer),
    Column("n_sell_total", Integer),
    Column("book_json", Text, nullable=False),     # zstd BLOB or plain JSON: book_codec
    # why it was fetched: 'tick' (the 10-min watch) | 'check' (the agent's price check
    # before an offer, book_server.py). NULL = a tick from before the column (2026-10-18).
    Column("reason", Text),
    *_kind_columns(),
    UniqueConstraint("item", "quality", "variant", "scraped_at",
                     name="uq_watch_item_quality_variant_time"),
)
Index("idx_watch_item_quality_variant_time", watch_snapshots.c.item,
      watch_snapshots.c.quality, watch_snapshots.c.variant, watch_snapshots.c.scraped_at)


# What the collector watches, and what the engine may trade: a fixed list of goods, one row
# each -- (item, quality) plus the kind columns. The collector scrapes the admitted, plain
# Unique/Strange rows (roster_entries) and never adds to the list; on a database with no
# roster yet it seeds one from what was collected (seed_roster). `origin` 'legacy' = watched
# before 2026-10-31; 'band' = a cosmetic added 2026-10-31 from bp.tf's pricelist (5 ref to
# 10 keys). The engine on the PC reads only `admitted = 1`. The pricelist columns
# (target_name, full_name, type, price_ref, last_seen) are as that load left them.
roster = Table(
    "roster", metadata,
    Column("id", Integer, primary_key=True),
    Column("item", Text, nullable=False),           # the app's itemName
    Column("quality", Integer, nullable=False),
    *_kind_columns(),
    Column("target_name", Text),                    # unused: always NULL on a plain good
    Column("full_name", Text),                      # bp.tf's display name
    Column("defindex", Integer),
    Column("type", Text),                           # bp.tf's /spreadsheet Type: Cosmetic, ...
    Column("origin", Text, nullable=False),         # 'legacy' | 'band'
    Column("admitted", Integer, nullable=False, server_default="0"),
    Column("price_ref", Float),                     # community price (ref) at its pricelist load
    Column("added_at", Text, nullable=False),
    Column("last_seen", Text),                      # the pricelist load that last listed it
    Column("changed_at", Text, nullable=False),     # bumped on every change: the sync's cursor
    UniqueConstraint("item", "quality", "craftable", "elevated", "australium", "crate", "target",
                     name="uq_roster_good"),
)
Index("idx_roster_changed", roster.c.changed_at)


# ---------------------------------------------------------------------------
# engine / migration
# ---------------------------------------------------------------------------
def _apply_sqlite_pragmas(dbapi_conn, _record):
    """WAL + relaxed sync so a reader (feature extractor / pandas) can run while
    the scraper appends. No-op on non-SQLite backends."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


def connect(db_path: str) -> Engine:
    """Open (creating if needed) the store and return an Engine.

    A filesystem path (SQLite) or a full SQLAlchemy URL — anything with '://' is
    treated as a URL, so the move to Postgres later is one line at the call site.
    """
    if "://" in db_path:
        url = db_path
        is_sqlite = db_path.startswith("sqlite")
    else:
        url = "sqlite:///" + db_path.replace("\\", "/")   # Windows-safe URL
        is_sqlite = True
    engine = create_engine(url)
    if is_sqlite:
        event.listen(engine, "connect", _apply_sqlite_pragmas)
        _migrate_snapshots_quality(engine)     # before create_all (may rebuild the table)
    metadata.create_all(engine)
    if is_sqlite:
        _add_missing_columns(engine)
    _add_missing_indexes(engine)
    return engine


def _migrate_snapshots_quality(engine: Engine) -> None:
    """Add `quality` to `snapshots` and move the unique key to
    (item, quality, variant, scraped_at) -- 2026-09-14.

    SQLite cannot alter a table-level UNIQUE, so a pre-quality table is rebuilt: rename
    it aside, let `create_all` build the new schema, copy the old rows back with
    quality=5 (they are all Unusual), then drop the copy. No-op on a fresh DB (nothing
    to rename) or one already carrying `quality`.
    """
    from sqlalchemy import text
    with engine.begin() as conn:
        info = conn.execute(text("PRAGMA table_info(snapshots)")).fetchall()
        if not info or "quality" in {r[1] for r in info}:
            return                             # fresh DB, or already migrated
        old_cols = [r[1] for r in info]
        conn.execute(text("DROP TABLE IF EXISTS _snapshots_old"))
        conn.execute(text("ALTER TABLE snapshots RENAME TO _snapshots_old"))
    snapshots.create(bind=engine)              # build the new-schema table now
    with engine.begin() as conn:
        cols = ", ".join(old_cols)
        conn.execute(text(f"INSERT INTO snapshots ({cols}, quality) "
                          f"SELECT {cols}, 5 FROM _snapshots_old"))
        conn.execute(text("DROP TABLE _snapshots_old"))


def _add_missing_indexes(engine: Engine) -> None:
    """Create indexes added after a database was first built (create_all skips
    indexes of an already-existing table)."""
    for table in metadata.tables.values():
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except Exception:
                pass          # an index is an optimisation, never a reason to fail


def _add_missing_columns(engine: Engine) -> None:
    """Add columns introduced after a table was first created. SQLite ADD COLUMN
    is O(1) and old rows read NULL — the honest answer for data never collected —
    or the column's default when it has one (the kind columns: old rows ARE the
    plain good, so they read 1 / 0, not 'unknown')."""
    from sqlalchemy import text
    with engine.begin() as conn:
        for table in (snapshots, snapshot_baseline, marketplace_price, watch_snapshots):
            have = {r[1] for r in conn.execute(
                text("PRAGMA table_info(%s)" % table.name))}
            if not have:
                continue                  # create_all just made it
            for col in table.columns:
                if col.name not in have:
                    sql_type = "INTEGER" if col.type.python_type in (int, bool) else (
                        "FLOAT" if col.type.python_type is float else "TEXT")
                    default = col.server_default
                    tail = (" NOT NULL DEFAULT %s" % default.arg) if default is not None else ""
                    conn.execute(text("ALTER TABLE %s ADD COLUMN %s %s%s"
                                      % (table.name, col.name, sql_type, tail)))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# price conversion (the one denomination used everywhere)
# ---------------------------------------------------------------------------
def to_keys(price: dict, key_price_ref):
    """A listing price -> one decimal keys figure, or None if it cannot be formed.

    `{"keys": 13, "ref": 18.55}` with a 64.56 ref key becomes 13.287. Listings
    quoted purely in ref still convert; a listing with no price at all (a
    negotiate-only sell) stays None rather than becoming a misleading 0.
    """
    if not price:
        return None
    keys, ref = price.get("keys") or 0.0, price.get("ref") or 0.0
    if not keys and not ref:
        return None
    if ref and not key_price_ref:
        return round(keys, 4) if keys else None   # no rate: refuse to guess
    return round(keys + (ref / key_price_ref if ref else 0.0), 4)


# ---------------------------------------------------------------------------
# LAYER 1 write path
# ---------------------------------------------------------------------------
def _source_of(row: dict) -> str:
    if row.get("source"):
        return row["source"]
    url = row.get("url") or ""
    return "classifieds" if "/classifieds" in url else "stats"


def _with_total_keys(orders, key_price_ref):
    """Fold total_keys into each listing's price block (idempotent)."""
    out = []
    for o in orders:
        price = o.get("price") or {}
        out.append({**o, "price": {**price, "total_keys": to_keys(price, key_price_ref)}})
    return out


_codecs = {}      # engine -> book_codec.Codec, loaded once; reset_codec after training


def book_codec_for(engine: Engine):
    """The codec this database's books are written with (an empty one -- plain JSON
    -- off SQLite or before the first dictionary is trained)."""
    codec = _codecs.get(engine)
    if codec is None:
        codec = book_codec.Codec()
        if engine.dialect.name == "sqlite":
            with engine.connect() as conn:
                codec = book_codec.Codec.from_con(conn.connection.driver_connection)
        _codecs[engine] = codec
    return codec


def reset_codec(engine: Engine) -> None:
    """Forget the cached codec, so the next insert picks up a new dictionary."""
    _codecs.pop(engine, None)


def priced_book(row: dict, key_price_ref) -> dict:
    """The book as stored: both sides with total_keys folded in at this rate."""
    return {"sellOrders": _with_total_keys(row.get("sellOrders") or [], key_price_ref),
            "buyOrders": _with_total_keys(row.get("buyOrders") or [], key_price_ref)}


def _row_values(item: str, row: dict, key_price_ref, pass_id=None, codec=None) -> dict:
    sells = row.get("sellOrders") or []
    buys = row.get("buyOrders") or []
    counts = row.get("counts") or {}
    book = {"sellOrders": _with_total_keys(sells, key_price_ref),
            "buyOrders": _with_total_keys(buys, key_price_ref)}
    blob = codec.encode(book) if codec is not None else None
    return {
        "item": item,
        "pass_id": pass_id,
        "variant": row.get("variant"),
        "quality": row.get("quality", 5),   # default 5 (Unusual) for pre-quality rows
        "effect_name": row.get("effectName"),
        "scraped_at": row.get("scrapedAt") or _now_iso(),
        "ingested_at": _now_iso(),
        "source": _source_of(row),
        "exist_current": row.get("existCurrent"),
        "priced": None if row.get("priced") is None else (1 if row.get("priced") else 0),
        "key_price_ref": key_price_ref,
        # Pre-trim counts. extract_classifieds supplies nBuyPage/nBuyClean/... ;
        # the stats fallback does not, so default to the stored (already-small)
        # book length, which for stats IS the page depth.
        "n_buy_page": row.get("nBuyPage", counts.get("buy", len(buys))),
        "n_buy_clean": row.get("nBuyClean", counts.get("buy", len(buys))),
        "n_sell_page": row.get("nSellPage", counts.get("sell", len(sells))),
        "n_sell_clean": row.get("nSellClean", counts.get("sell", len(sells))),
        "n_buy_total": row.get("nBuyTotal"),
        "n_sell_total": row.get("nSellTotal"),
        "book_json": blob if blob is not None else json.dumps(book, ensure_ascii=False),
        **PLAIN_KIND,
    }


# The kind columns of the plain good: every row the collector writes.
PLAIN_KIND = {"craftable": 1, "elevated": 0, "australium": 0, "crate": 0, "target": 0}


def insert_snapshot(engine: Engine, item: str, row: dict, key_price_ref=None,
                    pass_id=None):
    """Append one raw snapshot. Returns the new row's **id**, or None if a row for
    this (item, variant, scraped_at) already existed (idempotent re-runs are safe).

    The id is what pairs the pricedb baseline to this exact snapshot. No bid, ask
    or valid-book columns are written — those are a rule, so they are computed on
    read from `book_json`, never stored.
    """
    values = _row_values(item, row, key_price_ref, pass_id, codec=book_codec_for(engine))
    try:
        with engine.begin() as conn:
            res = conn.execute(insert(snapshots).values(**values))
            pk = res.inserted_primary_key
            _upsert_trade_urls(conn, row.get("tradeUrls"))
            return pk[0] if pk else None
    except IntegrityError:
        return None        # duplicate (item, variant, scraped_at)


_WATCH_COLS = {c.name for c in watch_snapshots.columns} - {"id"}


def insert_watch_snapshot(engine: Engine, item: str, row: dict, key_price_ref=None,
                          reason="tick"):
    """Append one watched book to `watch_snapshots` (the same row as `insert_snapshot`
    writes, minus the pass fields): `reason` 'tick' or 'check'. Trade-offer URLs are kept
    too. Returns the new id, or None for a duplicate (item, quality, variant, scraped_at)."""
    values = _row_values(item, row, key_price_ref, codec=book_codec_for(engine))
    values = {k: v for k, v in values.items() if k in _WATCH_COLS}
    values["reason"] = reason
    try:
        with engine.begin() as conn:
            res = conn.execute(insert(watch_snapshots).values(**values))
            pk = res.inserted_primary_key
            _upsert_trade_urls(conn, row.get("tradeUrls"))
            return pk[0] if pk else None
    except IntegrityError:
        return None


def _upsert_trade_urls(conn, urls) -> int:
    """Store each {steamid: url} that is new or changed. Returns rows written."""
    if not urls:
        return 0
    ids = list(urls)
    known = {r[0]: r[1] for r in conn.execute(
        select(trade_url.c.steam_id, trade_url.c.url).where(trade_url.c.steam_id.in_(ids)))}
    now, n = _now_iso(), 0
    for sid, url in urls.items():
        if sid not in known:
            conn.execute(insert(trade_url).values(steam_id=sid, url=url,
                                                  first_seen=now, changed_at=now))
        elif known[sid] != url:
            conn.execute(update(trade_url).where(trade_url.c.steam_id == sid)
                         .values(url=url, changed_at=now))
        else:
            continue
        n += 1
    return n


def trade_url_for(engine: Engine, steam_id) -> str | None:
    """The stored trade-offer URL of one account, or None."""
    with engine.begin() as conn:
        return conn.execute(select(trade_url.c.url)
                            .where(trade_url.c.steam_id == str(steam_id))).scalar()


def insert_marketplace(engine: Engine, item, variant, usd, available,
                       source="stats", observed_at=None) -> bool:
    """Record one Marketplace.tf observation. False if this (item, variant,
    observed_at) was already stored."""
    if usd is None and available is None:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(insert(marketplace_price).values(
                item=item, variant=variant, observed_at=observed_at or _now_iso(),
                usd=usd, available=available, source=source))
        return True
    except IntegrityError:
        return False




# ---------------------------------------------------------------------------
# passes
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------
def seed_roster(engine: Engine) -> int:
    """First run with a roster: every (item, quality) this database has ever collected
    becomes a 'legacy' roster row, admitted -- the books the engine already trades on.
    No-op once the roster has rows. Returns how many were seeded."""
    from sqlalchemy import text
    with engine.begin() as conn:
        if conn.execute(text("SELECT count(*) FROM roster")).scalar():
            return 0
        now = _now_iso()
        res = conn.execute(text(
            "INSERT INTO roster (item, quality, origin, admitted, added_at, changed_at) "
            "SELECT DISTINCT item, quality, 'legacy', 1, :now, :now FROM snapshots "
            "WHERE quality IN (6, 11) AND craftable = 1 AND elevated = 0 AND australium = 0 "
            "AND crate = 0 AND target = 0"), {"now": now})
        return res.rowcount or 0


def roster_entries(engine: Engine) -> list:
    """The goods to scrape: every admitted roster row of a plain Unique/Strange good
    (dicts), legacy first, in a stable order."""
    from sqlalchemy import text
    plain = " AND ".join(f"{k} = {v}" for k, v in PLAIN_KIND.items())
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(
            f"SELECT * FROM roster WHERE admitted = 1 AND quality IN (6, 11) AND {plain} "
            "ORDER BY origin = 'band', id")).mappings()]


def start_pass(engine: Engine, n_items=None, source: str = "live",
               started_at: str | None = None) -> int | None:
    with engine.begin() as conn:
        res = conn.execute(insert(passes).values(
            started_at=started_at or _now_iso(), n_items=n_items, source=source))
        pk = res.inserted_primary_key
        return pk[0] if pk else None


def finish_pass(engine: Engine, pass_id, n_pages=None, ended_at: str | None = None):
    """A pass with no ended_at was interrupted — worth seeing, not papering over."""
    if not pass_id:
        return
    with engine.begin() as conn:
        conn.execute(update(passes).where(passes.c.id == pass_id).values(
            ended_at=ended_at or _now_iso(), n_pages=n_pages))


# ---------------------------------------------------------------------------
# effects map
# ---------------------------------------------------------------------------
def upsert_effects(engine: Engine, rows) -> int:
    """Record/refresh the global effect id -> name map. Returns rows newly added."""
    if not rows:
        return 0
    now = _now_iso()
    added = 0
    with engine.begin() as conn:
        known = {r[0]: r[1] for r in conn.execute(select(effects.c.effect_id,
                                                         effects.c.effect_name))}
        for e in rows:
            eid, name = e.get("effect_id"), (e.get("effect_name") or "").strip()
            if eid is None or not name:
                continue
            if eid in known:
                conn.execute(effects.update().where(effects.c.effect_id == eid)
                             .values(effect_name=name, last_seen=now))
            else:
                conn.execute(insert(effects).values(
                    effect_id=eid, effect_name=name, first_seen=now, last_seen=now))
                added += 1
    return added


def effect_map(engine: Engine) -> dict:
    with engine.begin() as conn:
        return {r[0]: r[1] for r in conn.execute(select(effects.c.effect_id,
                                                        effects.c.effect_name))}


# ---------------------------------------------------------------------------
# key rate cache
# ---------------------------------------------------------------------------
def record_key_rate(engine: Engine, ref_per_key: float, source: str) -> bool:
    """Append one observation of the key -> ref rate. False if unusable (to_keys
    divides by it, so a <=0 value would mis-denominate a whole pass)."""
    if not ref_per_key or ref_per_key <= 0:
        return False
    with engine.begin() as conn:
        conn.execute(insert(key_price).values(
            fetched_at=_now_iso(), ref_per_key=float(ref_per_key), source=source))
    return True


def latest_key_rate(engine: Engine, max_age_hours=None):
    """The newest recorded rate, or None if there is none / it is too old.
    `max_age_hours=None` = newest at any age (last-resort fallback)."""
    with engine.begin() as conn:
        row = conn.execute(
            select(key_price.c.ref_per_key, key_price.c.fetched_at, key_price.c.source)
            .order_by(key_price.c.id.desc()).limit(1)).fetchone()
    if row is None:
        return None
    try:
        fetched = datetime.fromisoformat(row[1])
    except ValueError:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0
    if max_age_hours is not None and age_h > max_age_hours:
        return None
    return {"ref_per_key": row[0], "fetched_at": row[1], "source": row[2],
            "age_hours": age_h}


# ---------------------------------------------------------------------------
# pricedb baseline
# ---------------------------------------------------------------------------
def insert_baseline(engine: Engine, values: dict) -> bool:
    """Append one paired baseline row. False if this snapshot was already paired."""
    try:
        with engine.begin() as conn:
            conn.execute(insert(snapshot_baseline).values(**values))
        return True
    except IntegrityError:
        return False


def upsert_pricedb_items(engine: Engine, rows, chunk: int = 2000) -> int:
    """Refresh the pricedb name -> sku map. first_seen survives the upsert, so the
    table records when a SKU entered the feed."""
    if not rows:
        return 0
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    now = _now_iso()
    payload = [{**r, "first_seen": now, "last_seen": now} for r in rows]
    with engine.begin() as conn:
        for i in range(0, len(payload), chunk):
            stmt = sqlite_insert(pricedb_items).values(payload[i:i + chunk])
            conn.execute(stmt.on_conflict_do_update(
                index_elements=["sku"],
                set_={"name": stmt.excluded.name,
                      "defindex": stmt.excluded.defindex,
                      "effect_id": stmt.excluded.effect_id,
                      "last_seen": stmt.excluded.last_seen}))
    return len(payload)


def pricedb_name_map(engine: Engine) -> dict:
    """`{pricedb name: (sku, effect_id)}` — read once per run, used per page."""
    stmt = select(pricedb_items.c.name, pricedb_items.c.sku, pricedb_items.c.effect_id)
    with engine.connect() as conn:
        return {r[0]: (r[1], r[2]) for r in conn.execute(stmt)}


# ---------------------------------------------------------------------------
# counts
# ---------------------------------------------------------------------------
def baseline_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(snapshot_baseline)).scalar_one()


def snapshot_count(engine: Engine, item: str | None = None) -> int:
    stmt = select(func.count()).select_from(snapshots)
    if item is not None:
        stmt = stmt.where(snapshots.c.item == item)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


if __name__ == "__main__":
    # self-check on a throwaway temp-file db
    import os
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "snap_selfcheck.db")
    eng = connect(path)
    demo = {
        "item": "Demo", "variant": 7, "url": "https://backpack.tf/classifieds?x",
        "scrapedAt": _now_iso(), "ok": True, "effectName": "Demo Effect",
        "existCurrent": 42, "priced": True,
        "counts": {"sell": 2, "buy": 4},
        "nBuyPage": 10, "nBuyClean": 4, "nSellPage": 2, "nSellClean": 2,
        "sellOrders": [{"steamId": "s1", "price": {"keys": 14, "ref": 0}, "strange": False, "spelled": False},
                       {"steamId": "s2", "price": {"keys": 13, "ref": 22}, "strange": False, "spelled": False}],
        "buyOrders": [{"steamId": "b1", "price": {"keys": 12, "ref": 5}, "strange": False, "spelled": False},
                      {"steamId": "b2", "price": {"keys": 11, "ref": 0}, "strange": False, "spelled": False},
                      {"steamId": "b2", "price": {"keys": 10, "ref": 0}, "strange": False, "spelled": False},  # dup acct
                      {"steamId": "b3", "price": {"keys": 9, "ref": 0}, "strange": False, "spelled": False}],
    }
    sid = insert_snapshot(eng, "Demo", demo, key_price_ref=60.0)
    print("inserted id:", sid, "| dup:", insert_snapshot(eng, "Demo", demo, key_price_ref=60.0))
    with eng.connect() as c:
        s = c.execute(select(snapshots)).mappings().first()
        print("snapshot:", dict(s) if s else None)
    eng.dispose()
    print("ok; db at", path)

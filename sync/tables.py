"""How each table of trading_app.db is synced -- shared by server.py and pull.py.

Shared by sync/pull.py (on the PC) and sync/server.py (on the VM), with dbsync.proto and
its stubs.

The VM's DB is the only writer; the PC's copy is a read-only replica. So a sync
never has to merge two writers, only copy what the replica lacks. Each table gets
the cheapest mode that is still exact for how the collector writes it:

  append   rows are only ever inserted, with a growing integer rowid. The cursor is
           the replica's own max(rowid): nothing to store, and an interrupted pull
           resumes where it stopped. SQLite hands out rowids under its one writer
           lock, so committed rows are always a prefix -- a later id never commits
           before an earlier one.
  changed  rows are inserted and later touched; the column given is bumped on each
           touch. Pull rows with column >= the replica's max(column), upsert them.
  full     small tables updated in place with no change column: re-send all rows.

Deletes are not synced (the collector never deletes; reset_series.py is the one
exception -- after a reset, start the replica over, pull.py says so).

ORDER MATTERS: book_dict goes last. A snapshot's zstd frame names the dictionary it
was written with, and a dictionary is always committed before any book using it.
Pulling book_dict after snapshots therefore guarantees every pulled book can be
decoded; pulling it first could miss one trained in between.
"""

TABLES = [
    # table              mode       change column
    ("passes",            "full",    None),         # ended_at / counts filled in when a pass ends
    ("snapshots",         "append",  None),
    ("snapshot_baseline", "append",  None),
    ("marketplace_price", "append",  None),
    ("key_price",         "append",  None),
    ("effects",           "changed", "last_seen"),
    ("pricedb_items",     "changed", "last_seen"),
    ("bot_account",       "full",    None),         # no integer key; small
    ("bot_group_member",  "full",    None),
    ("steam_profile",     "append",  None),
    ("bot_label",         "append",  None),
    ("bot_sync",          "append",  None),
    ("trade_url",         "changed", "changed_at"),   # touched only when a URL is new or changes
    ("watch_snapshots",   "append",  None),         # the ~10-min watch of held items (watchlist.py)
    ("roster",            "changed", "changed_at"),   # what is watched / admitted; touched on change (2026-10-31)
    ("book_dict",         "append",  None),         # LAST -- see above
]

MODES = {name: (mode, col) for name, mode, col in TABLES}

DEFAULT_PORT = 50051

# One batch is cut at whichever comes first. A compressed book is ~2 KB, so this is
# a few MB a message -- under MAX_MESSAGE with room for a fat row.
BATCH_ROWS = 2000
BATCH_BYTES = 4 * 1024 * 1024
MAX_MESSAGE = 64 * 1024 * 1024

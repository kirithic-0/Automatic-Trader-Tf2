"""Offline test for the gRPC sync: a real server.py and pull.py over a temp source DB.

Pins: a fresh replica gets every table, ids and BLOBs byte for byte; a second pull
brings only new rows (append) and updated rows (changed / full); a column the
collector added later appears in the replica; book_dict is pulled after snapshots;
an unknown table is refused by the server; a replica ahead of the source stops
the pull instead of skipping rows.

    python backpack/tests/test_sync.py
"""
import os
import sqlite3
import sys
import tempfile

import _paths  # noqa: E402
_paths.use_sync()             # pull.py (this repo)
_paths.use_collector_sync()   # server.py (the collector repo); skips this file without it
import grpc  # noqa: E402
import dbsync_pb2 as pb  # noqa: E402
import dbsync_pb2_grpc as pb_grpc  # noqa: E402
import pull  # noqa: E402
import server  # noqa: E402
from tables import TABLES  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))
    if not ok:
        fails.append(name)


SCHEMA = """
CREATE TABLE passes (id INTEGER NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, n_pages INTEGER,
    n_items INTEGER, source TEXT NOT NULL, PRIMARY KEY (id));
CREATE TABLE snapshots (id INTEGER NOT NULL, pass_id INTEGER, item TEXT NOT NULL, variant INTEGER NOT NULL,
    quality INTEGER DEFAULT '5' NOT NULL, key_price_ref FLOAT, book_json TEXT NOT NULL, PRIMARY KEY (id));
CREATE INDEX idx_snap_pass ON snapshots (pass_id);
CREATE TABLE effects (effect_id INTEGER NOT NULL, effect_name TEXT NOT NULL, first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL, PRIMARY KEY (effect_id));
CREATE TABLE bot_group_member (steamId TEXT NOT NULL, grp TEXT NOT NULL, first_seen_at TEXT NOT NULL,
    PRIMARY KEY (steamId, grp));
CREATE TABLE book_dict (dict_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, trained_on_pass INTEGER,
    n_samples INTEGER, dict BLOB NOT NULL);
CREATE TABLE stray (id INTEGER PRIMARY KEY, x TEXT);
"""


def dump(path, table):
    con = sqlite3.connect(path)
    try:
        return con.execute(f'SELECT * FROM "{table}" ORDER BY 1, 2').fetchall()
    finally:
        con.close()


tmp = tempfile.mkdtemp()
src, dst = os.path.join(tmp, "vm.db"), os.path.join(tmp, "pc.db")
s = sqlite3.connect(src)
s.execute("PRAGMA journal_mode=WAL")
s.executescript(SCHEMA)
s.execute("INSERT INTO passes VALUES (1, '2026-10-17T00:00', '2026-10-17T00:03', 2, 2, 'app')")
s.execute("INSERT INTO passes VALUES (2, '2026-10-17T01:00', NULL, NULL, NULL, 'app')")   # still running
blob = bytes(range(256)) * 40
s.execute("INSERT INTO snapshots VALUES (1, 1, 'Team Captain', 0, 6, 61.5, ?)", (blob,))
s.execute("INSERT INTO snapshots VALUES (2, 1, 'Bill''s Hat', 0, 11, NULL, '{\"buyOrders\": []}')")
s.execute("INSERT INTO effects VALUES (13, 'Burning Flames', '2026-10-01', '2026-10-01')")
s.execute("INSERT INTO bot_group_member VALUES ('7656', 'TF2Autobot', '2026-10-01')")
s.execute("INSERT INTO book_dict VALUES (1, '2026-10-04', 1, 400, ?)", (b"\x37\xa4\x30\xec" + b"d" * 500,))
s.execute("INSERT INTO stray VALUES (1, 'not synced')")
s.commit()

srv, port = server.serve(src, port=0, block=False)
target = f"127.0.0.1:{port}"


def run():
    with grpc.insecure_channel(target) as ch:
        con = sqlite3.connect(dst)
        try:
            return pull.pull_once(pb_grpc.DbSyncStub(ch), con, log=lambda *_: None)
        finally:
            con.close()


print("first pull into an empty replica")
w = run()
for t in ("passes", "snapshots", "effects", "bot_group_member", "book_dict"):
    check(f"{t} identical", dump(dst, t), dump(src, t))
check("the BLOB survives byte for byte", dump(dst, "snapshots")[0][-1], blob)
check("the index came across", sqlite3.connect(dst).execute(
    "SELECT count(*) FROM sqlite_master WHERE name = 'idx_snap_pass'").fetchone()[0], 1)
check("a table not in tables.py is not synced", dump(dst, "stray"), [])
check("rows written, all new on an empty replica", (w["snapshots"], w["book_dict"]), ((2, 2), (1, 1)))
check("book_dict is the last table pulled", TABLES[-1][0], "book_dict")

print("second pull: only what changed")
s.execute("UPDATE passes SET ended_at = '2026-10-17T01:03', n_pages = 1 WHERE id = 2")
s.execute("ALTER TABLE snapshots ADD COLUMN n_buy_total INTEGER")
s.execute("INSERT INTO snapshots VALUES (3, 2, 'Team Captain', 0, 6, 62.0, 'x', 14)")
s.execute("UPDATE effects SET last_seen = '2026-10-17' WHERE effect_id = 13")
s.execute("INSERT INTO effects VALUES (701, 'Hot', '2026-10-17', '2026-10-17')")
s.execute("INSERT INTO book_dict VALUES (2, '2026-10-17', 2, 400, ?)", (b"e" * 300,))
# a table the collector creates after the replica exists (watch_snapshots, 2026-10-18)
s.execute("CREATE TABLE watch_snapshots (id INTEGER NOT NULL, item TEXT NOT NULL, variant INTEGER NOT NULL, "
          "quality INTEGER NOT NULL, scraped_at TEXT NOT NULL, book_json TEXT NOT NULL, PRIMARY KEY (id))")
s.execute("CREATE INDEX idx_watch_item_quality_variant_time ON watch_snapshots (item, quality, variant, scraped_at)")
s.execute("INSERT INTO watch_snapshots VALUES (1, 'Team Captain', 0, 6, '2026-10-18T13:55', ?)", (blob,))
s.execute("INSERT INTO watch_snapshots VALUES (2, 'Team Captain', 0, 6, '2026-10-18T14:05', ?)", (blob,))
s.commit()
w = run()
check("append sent only the new snapshot", w["snapshots"], (1, 1))
check("the new column exists in the replica, old rows NULL",
      [r[-1] for r in dump(dst, "snapshots")], [None, None, 14])
check("the running pass got its end", dump(dst, "passes"), dump(src, "passes"))
check("changed: the touched + the new effect", dump(dst, "effects"), dump(src, "effects"))
check("changed sent only rows >= the replica's last_seen: 1 new, 1 refreshed", w["effects"], (2, 1))
check("full re-sends the whole table: 0 new, 2 refreshed", w["passes"], (2, 0))
check("the summary splits new from refreshed", pull.summary(w),
      "new: snapshots +1, effects +1, watch_snapshots +2, book_dict +1  |  refreshed: passes 2, effects 1, bot_group_member 1")
check("the new dictionary arrived", dump(dst, "book_dict"), dump(src, "book_dict"))
check("a table created on the VM later arrives whole", dump(dst, "watch_snapshots"),
      dump(src, "watch_snapshots"))
check("... with its index", sqlite3.connect(dst).execute(
    "SELECT count(*) FROM sqlite_master WHERE name = 'idx_watch_item_quality_variant_time'").fetchone()[0], 1)
s.execute("INSERT INTO watch_snapshots VALUES (3, 'Team Captain', 0, 6, '2026-10-18T14:15', 'x')")
s.commit()
w = run()
check("watch_snapshots appends only the new tick", w["watch_snapshots"], (1, 1))

print("third pull: nothing new")
w = run()
check("append tables send nothing", (w["snapshots"], w["watch_snapshots"], w["book_dict"]),
      ((0, 0), (0, 0), (0, 0)))
check("nothing new reads as such", pull.summary(w).startswith("new: nothing  |  refreshed:"), True)

print("fourth pull: a column with a default, and the roster (2026-10-31)")
s.execute("ALTER TABLE snapshots ADD COLUMN craftable INTEGER NOT NULL DEFAULT 1")
s.execute("INSERT INTO snapshots (id, pass_id, item, variant, quality, book_json, craftable) "
          "VALUES (4, 2, 'Team Captain', 0, 6, 'x', 0)")
s.execute("CREATE TABLE roster (id INTEGER NOT NULL, item TEXT NOT NULL, quality INTEGER NOT NULL, "
          "admitted INTEGER DEFAULT '0' NOT NULL, changed_at TEXT NOT NULL, PRIMARY KEY (id))")
s.execute("INSERT INTO roster VALUES (1, 'Team Captain', 6, 1, '2026-10-31T10:00')")
s.execute("INSERT INTO roster VALUES (2, 'Buzz Killer', 13, 0, '2026-10-31T10:00')")
s.commit()
w = run()
check("old rows read the new column's default, not NULL",
      sqlite3.connect(dst).execute("SELECT id, craftable FROM snapshots ORDER BY id").fetchall(),
      [(1, 1), (2, 1), (3, 1), (4, 0)])
check("the roster arrives whole", dump(dst, "roster"), dump(src, "roster"))
s.execute("UPDATE roster SET admitted = 1, changed_at = '2026-11-07T10:00' WHERE id = 2")   # graduated
s.commit()
w = run()
check("an admission arrives in place (rows >= the replica's last change: 2 sent, 0 new)",
      (w["roster"], dump(dst, "roster")), ((2, 0), dump(src, "roster")))

print("guards")
with grpc.insecure_channel(target) as ch:
    try:
        list(pb_grpc.DbSyncStub(ch).Pull(pb.PullRequest(table="stray; DROP TABLE snapshots")))
        check("an unknown table is refused", "served", "INVALID_ARGUMENT")
    except grpc.RpcError as e:
        check("an unknown table is refused", e.code(), grpc.StatusCode.INVALID_ARGUMENT)
d = sqlite3.connect(dst)
d.execute("INSERT INTO snapshots (id, pass_id, item, variant, quality, book_json) VALUES (99, 2, 'x', 0, 6, 'x')")
d.commit()
d.close()
try:
    run()
    check("a replica ahead of the source stops the pull", "pulled", "SyncError")
except pull.SyncError as e:
    check("a replica ahead of the source stops the pull", "ahead of the VM" in str(e), True)
check("the source was never written", dump(src, "snapshots")[-1][0], 4)

srv.stop(0)
s.close()
print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all passed")

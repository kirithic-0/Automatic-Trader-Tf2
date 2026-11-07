# -*- coding: utf-8 -*-
"""Offline test for the engine's universe gate (engine/panel.py universe_sql / plain_sql): the
engine reads Unique/Strange plain goods only, and once the collector's `roster` table is here,
only admitted items. A db from before the kind columns reads exactly as it did.

    python test_universe.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import os as _os
import json
import sqlite3
import sys
import tempfile

from engine import panel as P
from engine.screens import listings as SL

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def book(top):
    o = lambda sid, k: {"steamId": sid, "price": {"total_keys": k}, "listingType": "bot",
                        "dealType": "instant", "id": f"440_{sid}"}
    return json.dumps({"buyOrders": [o("1", top)], "sellOrders": [o("2", top + 0.01)]})


KINDS = ("craftable INTEGER DEFAULT 1, elevated INTEGER DEFAULT 0, australium INTEGER DEFAULT 0, "
         "crate INTEGER DEFAULT 0, target INTEGER DEFAULT 0")


def make(rows, kinds=False, roster=None):
    """rows: (item, quality, pass_id, top, {kind overrides}); roster: (item, quality, admitted, {kinds})."""
    path = _os.path.join(tempfile.mkdtemp(), "app.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE passes (id INTEGER PRIMARY KEY, started_at TEXT)")
    con.executemany("INSERT INTO passes VALUES (?, ?)", [(1, "2026-10-31T10:00:00+00:00"), (2, "2026-10-31T11:00:00+00:00")])
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, pass_id INT, item TEXT, quality INT, "
                "variant INT DEFAULT 0, effect_name TEXT, scraped_at TEXT, book_json TEXT"
                + (", " + KINDS if kinds else "") + ")")
    con.execute("CREATE TABLE snapshot_baseline (snapshot_id INT, buy_total_keys REAL, sell_total_keys REAL)")
    for item, q, pid, top, kd in rows:
        cols = ["pass_id", "item", "quality", "scraped_at", "book_json", *kd]
        con.execute(f"INSERT INTO snapshots ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    (pid, item, q, f"2026-10-31T1{pid - 1}:00:00", book(top), *kd.values()))
    if roster is not None:
        con.execute(f"CREATE TABLE roster (item TEXT, quality INT, {KINDS}, admitted INTEGER)")
        for item, q, adm, kd in roster:
            cols = ["item", "quality", "admitted", *kd]
            con.execute(f"INSERT INTO roster ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        (item, q, adm, *kd.values()))
    con.commit()
    con.close()
    return path


def series(path):
    df = P.load_panel(path, use_cache=False)
    return sorted({(r.item, int(r.quality)) for r in df.itertuples()})


def test_old_db():
    print("a db from before the kind columns: Unique/Strange, as before")
    db = make([("Hat", 6, 1, 0.1, {}), ("Hat", 11, 1, 0.5, {}), ("Hat", 13, 1, 0.9, {})])
    con = P.connect_ro(db)
    check("no kind columns -> no plain filter", P.plain_sql(con), "")
    check("universe = quality only", P.universe_sql(con), "quality IN (6,11)")
    con.close()
    check("Haunted is not read", series(db), [("Hat", 6), ("Hat", 11)])


def test_kinds():
    print("kind columns: the plain good only")
    db = make([("Hat", 6, 1, 0.1, {}), ("Hat", 6, 1, 0.3, {"craftable": 0}),
               ("Hat", 6, 2, 0.2, {}), ("Hat", 6, 2, 0.6, {"craftable": 0}),
               ("Crate", 6, 1, 0.1, {"crate": 10}), ("SMG", 11, 1, 2.0, {"australium": 1}),
               ("Strangifier", 6, 1, 0.2, {"target": 103}), ("Cap", 13, 1, 1.0, {"elevated": 11}),
               ("Hat", 6, 2, 0.5, {"craftable": None})], kinds=True)
    df = P.load_panel(db, use_cache=False)
    check("only the craftable Hat series", sorted(set(zip(df.item, df.quality))), [("Hat", 6)])
    check("... one row per pass, not two", len(df), 2)
    check("... the craftable snapshots (ids 1, 3), a NULL kind read as plain (id 9)",
          sorted(df.snapshot_id), [1, 9])
    con = P.connect_ro(db)
    src, t, bk = SL.latest_book(con, "Hat", 6)
    check("latest_book: the newest plain book (the NULL-kind one), never the non-craftable one",
          bk["buyOrders"][0]["price"]["total_keys"], 0.5)
    check("plain_sql with an alias", P.plain_sql(con, alias="s").startswith(" AND IFNULL(s.craftable, 1) = 1"), True)
    con.close()


def test_roster():
    print("roster: admitted items only")
    rows = [("Hat", 6, 1, 0.1, {}), ("Cap", 6, 1, 0.2, {}), ("Cap", 11, 1, 0.6, {}),
            ("Taunt: X", 6, 1, 0.3, {}), ("Hat", 6, 1, 0.5, {"craftable": 0})]
    ros = [("Hat", 6, 1, {}), ("Cap", 6, 1, {}), ("Cap", 11, 0, {}), ("Taunt: X", 6, 0, {}),
           ("Hat", 6, 1, {"craftable": 0})]      # admitted, but not the plain good: still not read
    check("an empty roster (not seeded yet) does not gate",
          series(make(rows, kinds=True, roster=[])), [("Cap", 6), ("Cap", 11), ("Hat", 6), ("Taunt: X", 6)])
    db = make(rows, kinds=True, roster=ros)
    check("admitted plain Unique/Strange only", series(db), [("Cap", 6), ("Hat", 6)])
    cache = tempfile.mkdtemp()
    n0 = len(P.load_panel(db, cache_dir=cache))
    con = sqlite3.connect(db)
    con.execute("UPDATE roster SET admitted = 1 WHERE item = 'Taunt: X'")
    con.commit()
    con.close()
    check("admitting a group rebuilds the cache with its history", len(P.load_panel(db, cache_dir=cache)), n0 + 1)


if __name__ == "__main__":
    test_old_db()
    test_kinds()
    test_roster()
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)

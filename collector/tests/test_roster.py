# -*- coding: utf-8 -*-
"""Offline tests for the roster and the kind columns -- no browser, no network:

  * snapshot_store: an existing database gains the kind columns with their defaults
    (its old rows read as the plain good, never NULL); every row is stored as the
    plain good; the roster is seeded once from what was collected; roster_entries
    gives only the admitted, plain Unique/Strange goods;
  * scrape_run: roster_kind, cap_per_quality;

    python tests/test_roster.py
"""
import os as _os, sys as _sys  # noqa: E402 -- make the collector importable from tests/
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sqlite3
import sys
import tempfile

import goods
import scrape_run as sr
import snapshot_store as ss

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-66s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def old_db():
    """A database as the collector left it before 2026-10-31: no kind columns, no roster."""
    path = _os.path.join(tempfile.mkdtemp(), "app.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, pass_id INTEGER, item TEXT NOT NULL, "
                "variant INTEGER NOT NULL, quality INTEGER DEFAULT '5' NOT NULL, effect_name TEXT, "
                "scraped_at TEXT NOT NULL, ingested_at TEXT NOT NULL, source TEXT NOT NULL, "
                "exist_current INTEGER, priced INTEGER, key_price_ref FLOAT, n_buy_page INTEGER, "
                "n_buy_clean INTEGER, n_sell_page INTEGER, n_sell_clean INTEGER, n_buy_total INTEGER, "
                "n_sell_total INTEGER, book_json TEXT NOT NULL, "
                "CONSTRAINT uq_snap_item_quality_variant_time UNIQUE (item, quality, variant, scraped_at))")
    con.executemany("INSERT INTO snapshots (pass_id, item, variant, quality, scraped_at, ingested_at, source, "
                    "book_json) VALUES (1, ?, 0, ?, ?, 'now', 'app', '{}')",
                    [("Team Captain", 6, "t1"), ("Team Captain", 11, "t2"), ("Airdog", 6, "t3"),
                     ("Team Captain", 6, "t4")])
    con.commit()
    con.close()
    return path


def test_store():
    print("=== snapshot_store: kind columns and the roster ===")
    path = old_db()
    eng = ss.connect(path)
    con = sqlite3.connect(path)
    cols = {r[1]: (r[2], r[4]) for r in con.execute("PRAGMA table_info(snapshots)")}
    check("kind columns added with their defaults",
          [cols.get(c) for c in ("craftable", "elevated", "australium", "crate", "target")],
          [("INTEGER", "1"), ("INTEGER", "0"), ("INTEGER", "0"), ("INTEGER", "0"), ("INTEGER", "0")])
    check("old rows read as the plain good, not NULL",
          con.execute("SELECT DISTINCT craftable, elevated, australium, crate, target FROM snapshots").fetchall(),
          [(1, 0, 0, 0, 0)])
    row = {"variant": 0, "quality": 6, "scrapedAt": "t5", "sellOrders": [], "buyOrders": [],
           **goods.Kind(6).meta()}
    ss.insert_snapshot(eng, "Team Captain", row, 64.5, 2)
    ss.insert_watch_snapshot(eng, "Airdog", {"variant": 0, "quality": 6, "scrapedAt": "t7"}, 64.5)
    check("a new row is the plain good", con.execute(
        "SELECT craftable, elevated, australium, crate, target FROM snapshots WHERE scraped_at = 't5'"
    ).fetchall(), [(1, 0, 0, 0, 0)])
    check("so is a watch row", con.execute("SELECT craftable, crate FROM watch_snapshots").fetchall(), [(1, 0)])

    con.execute("INSERT INTO snapshots (pass_id, item, variant, quality, scraped_at, ingested_at, source, "
                "book_json, craftable) VALUES (1, 'Team Captain', 0, 6, 't8', 'now', 'app', '{}', 0)")
    con.commit()
    check("seed: every collected plain Unique/Strange good (not the non-craftable one)",
          ss.seed_roster(eng), 3)      # Team Captain 6, Team Captain 11, Airdog 6
    check("seed is once", ss.seed_roster(eng), 0)
    check("seeded rows are legacy, admitted", con.execute(
        "SELECT DISTINCT origin, admitted FROM roster").fetchall(), [("legacy", 1)])

    # Rows a database may hold that the collector never scrapes.
    con.executemany("INSERT INTO roster (item, quality, craftable, origin, admitted, added_at, changed_at) "
                    "VALUES (?, ?, ?, 'band', ?, 'now', 'now')",
                    [("Head Hunter", 6, 1, 1), ("Buzz Killer", 13, 1, 1), ("Odd Hat", 6, 1, 0),
                     ("Bill's Hat", 6, 0, 1)])
    con.execute("INSERT INTO roster (item, quality, crate, origin, admitted, added_at, changed_at) "
                "VALUES ('Mann Co. Supply Crate', 6, 10, 'band', 1, 'now', 'now')")
    con.commit()
    got = [(r["item"], r["quality"], r["origin"]) for r in ss.roster_entries(eng)]
    check("roster_entries: admitted plain Unique/Strange only, legacy first",
          (sorted(got[:3]), got[3:]),
          ([("Airdog", 6, "legacy"), ("Team Captain", 6, "legacy"), ("Team Captain", 11, "legacy")],
           [("Head Hunter", 6, "band")]))
    con.close()


def test_scrape_run():
    print("\n=== scrape_run: roster_kind, cap_per_quality ===")
    check("a legacy row keeps the legacy request", sr.roster_kind({"origin": "legacy", "quality": 11}),
          goods.legacy(11))
    check("a band row is strict", sr.roster_kind({"origin": "band", "quality": 6}),
          goods.Kind(6, strict=True))
    mix = [{"item": f"u{i}", "quality": 6} for i in range(5)] + [{"item": f"s{i}", "quality": 11} for i in range(3)]
    capped = sr.cap_per_quality(mix, 2)
    check("cap: at most n per quality", [e["item"] for e in capped], ["u0", "u1", "s0", "s1"])
    check("a quality with fewer than n keeps all", sum(e["quality"] == 11 for e in sr.cap_per_quality(mix, 10)), 3)
    check("n falsy -> unchanged", len(sr.cap_per_quality(mix, 0)), len(mix))


if __name__ == "__main__":
    test_store()
    test_scrape_run()
    print("\n  %d FAILED: %s" % (len(fails), ", ".join(fails)) if fails else "\n  all checks passed")
    sys.exit(1 if fails else 0)

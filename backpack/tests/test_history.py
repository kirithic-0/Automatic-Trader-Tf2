# -*- coding: utf-8 -*-
"""Offline test for engine history: one item's books over time, from the hourly passes, the
watch books and (with old=True) the frozen db, read by the engine's rules.

    python test_history.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import os as _os
import json
import sqlite3
import sys
import tempfile

from engine.screens import history as h

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True):
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": 0, "ref": 0, "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False, "id": f"440_{sid}"}


def book(top):
    """A book whose top buy order is `top` (a human's, so not tradable when it is account 1)."""
    buys = [L("1", top, bot=False)] + [L(str(i), round(top - 0.002 * i, 4)) for i in range(2, 8)]
    sells = [L(str(i + 10), round(top + 0.01 + 0.002 * i, 4)) for i in range(6)]
    return json.dumps({"buyOrders": buys, "sellOrders": sells})


def make(path, rows, watch=None, urls=True):
    con = sqlite3.connect(path)
    cols = "item TEXT, quality INT, variant INT, scraped_at TEXT, book_json TEXT, key_price_ref REAL DEFAULT 60.5"
    ins = "INSERT INTO {} (item, quality, variant, scraped_at, book_json) VALUES (?, ?, 0, ?, ?)"
    con.execute(f"CREATE TABLE snapshots ({cols})")
    con.executemany(ins.format("snapshots"), rows)
    if watch is not None:
        con.execute(f"CREATE TABLE watch_snapshots ({cols})")
        con.executemany(ins.format("watch_snapshots"), watch)
    if urls:
        con.execute("CREATE TABLE trade_url (steam_id TEXT, url TEXT)")
        con.executemany("INSERT INTO trade_url VALUES (?, ?)",
                        [(str(i), f"https://steamcommunity.com/tradeoffer/new/?partner={i}&token=t") for i in range(1, 20)])
    con.commit()
    con.close()


def test_history():
    print("one item over time")
    d = tempfile.mkdtemp()
    app, old = _os.path.join(d, "app.db"), _os.path.join(d, "old.db")
    make(app, [("Hat", 6, "2026-10-29T10:00", book(0.11)), ("Hat", 6, "2026-10-29T11:00", book(0.12)),
               ("Hat", 11, "2026-10-29T10:00", book(0.5)), ("Other Hat", 6, "2026-10-29T10:00", book(0.2))],
         watch=[("Hat", 6, "2026-10-29T10:30", book(0.115))])
    make(old, [("Hat", 6, "2026-09-20T10:00", book(0.10))], urls=False)
    pts = h.history("Hat", 6, app_db=app, old_db=old)
    check("passes + watch, oldest first, one quality", [(p["t"][11:16], p["src"]) for p in pts],
          [("10:00", "pass"), ("10:30", "watch"), ("11:00", "pass")])
    check("bid1 = the top buy order", [p["bid1"] for p in pts], [0.11, 0.115, 0.12])
    check("its account", pts[0]["bid1_user"], "u1")
    check("tbid = the best tradable bid (the human's is not)", pts[0]["tbid"], 0.106)
    check("ask1 = the lowest sell", pts[0]["ask1"], 0.12)
    check("old=True adds the frozen db", [p["src"] for p in h.history("Hat", 6, old=True, app_db=app, old_db=old)][0], "old")
    check("since", len(h.history("Hat", 6, since="2026-10-29T10:15", app_db=app, old_db=old)), 2)
    check("resolve: any case", h.resolve("hat", app_db=app), ("Hat", []))
    check("resolve: a unique part", h.resolve("other", app_db=app), ("Other Hat", []))
    check("resolve: an ambiguous part lists both", h.resolve("ha", app_db=app), (None, ["Hat", "Other Hat"]))

    print("one stored book, explained")
    check("each point names its book", [(p["src"], p["id"]) for p in pts], [("pass", 1), ("watch", 1), ("pass", 2)])
    b = h.book("watch", 1, app_db=app)
    check("the right book: its time and key rate", (b["t"], b["key_ref"]), ("2026-10-29T10:30", 60.5))
    top = b["buys"][0]
    check("the top buy is the human's: kept by the engine...", (top["tk"], top["reason"], top["role"]), (0.115, "kept", "bid1"))
    check("... but the agent cannot sell into it", top["agent"], "not bot+instant (normal/instant)")
    check("the next one, a bot: the agent can (None)", b["buys"][1]["agent"], None)
    check("sells carry the agent's verdict too", "agent" in b["sells"][0], True)
    check("a missing book: None", h.book("pass", 99, app_db=app), None)
    check("items: every (item, quality), the plain good, admitted (no roster yet)", h.items(app_db=app),
          [["Hat", 6, h.PLAIN_CODE, None, 1], ["Hat", 11, h.PLAIN_CODE, None, 1], ["Other Hat", 6, h.PLAIN_CODE, None, 1]])
    con = sqlite3.connect(app)
    for c, d in (("craftable", 1), ("elevated", 0), ("australium", 0), ("crate", 0), ("target", 0)):
        con.execute(f"ALTER TABLE snapshots ADD COLUMN {c} INTEGER NOT NULL DEFAULT {d}")
    con.execute("INSERT INTO snapshots (item, quality, variant, scraped_at, book_json, craftable) "
                "VALUES ('Hat', 6, 0, '2026-10-29T12:00', ?, 0)", (book(0.9),))
    con.execute("CREATE TABLE roster (item TEXT, quality INT, craftable INT, elevated INT, australium INT, "
                "crate INT, target INT, target_name TEXT, admitted INT)")
    con.executemany("INSERT INTO roster VALUES (?, ?, ?, 0, 0, 0, 0, NULL, ?)",
                    [("Hat", 6, 1, 1), ("Hat", 6, 0, 0)])
    con.commit()
    con.close()
    check("kinds: the plain Hat (passes + its watch book) leaves out the Non-Craftable book",
          [p["bid1"] for p in h.history("Hat", 6, app_db=app, old_db=old)], [0.11, 0.115, 0.12])
    check("kinds: the Non-Craftable Hat is its own good (watch books are plain: none)",
          [p["bid1"] for p in h.history("Hat", 6, app_db=app, old_db=old, kind="0.0.0.0.0")], [0.9])
    check("items: the roster, each good with its kind and admission", h.items(app_db=app),
          [["Hat", 6, "1.0.0.0.0", None, 1], ["Hat", 6, "0.0.0.0.0", None, 0]])
    try:
        h.history("Hat", 6, app_db=app, kind="1; DROP TABLE x")
        check("a bad kind code is refused", "accepted", "ValueError")
    except ValueError:
        check("a bad kind code is refused", "ValueError", "ValueError")


if __name__ == "__main__":
    test_history()
    print("\nFAILED: " + ", ".join(fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)

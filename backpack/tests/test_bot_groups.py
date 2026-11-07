# -*- coding: utf-8 -*-
"""Offline test for engine.bot_groups: the observation table, pricing mode, the group key
and the fleet check, over a throwaway trading.db with a hand-built market.

The market: 40 hourly passes, 2 items. Three unregistered humans and most bots move
their buy price up 1% a pass, so the book median moves every pass. Pins: S (gladiator,
never reprices) -> gladiator:static; R (gladiator, tracks) -> gladiator:repricing; F1/F2
(a scrapyard fleet, identical prices) -> one group "scrapyard" and a 100% fleet check;
X1/X2 (tf2syntax, in NOT_ONE_OPERATOR) -> provider x mode, not a fleet; T (5 passes) ->
thin; a human mark drops an account; the window makes S thin; gap_ref is the exact
ref gap at rank 1; the cache appends new snapshots and matches a fresh read.

    python test_bot_groups.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
_paths.use_collector()
import os as _os
import json
import sqlite3
import sys
import tempfile

import bot_registry as br
from engine.bot_groups import behaviour_groups, fleet_check, load_bot_obs, pricing_modes

fails = []
KREF = 50.0


def check(name, got, want):
    ok = got == want
    print("  %-4s %-60s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True):
    ref = round(keys * KREF, 2)
    return {"steamId": sid, "user": "u" + sid, "premium": False,
            "price": {"keys": 0, "ref": ref, "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False}


def book(k, item):
    x = round(1.0 * 1.01 ** k, 4)                  # the moving price
    buys = [L("H1", x, bot=False), L("H2", x, bot=False), L("H3", x, bot=False),
            L("R", x), L("F1", x), L("F2", x), L("S", 1.0),
            L("X1", x), L("X2", round(x * 0.9, 4))]
    if k < 5:
        buys.append(L("T", x))
    if item == "Cap" and k == 0:                   # one scrap over the pack at rank 1
        buys.append(L("C", round(x + 0.11 / KREF, 6)))
    return {"buyOrders": buys, "sellOrders": []}


def add_pass(con, k):
    con.execute("INSERT INTO passes (id, started_at) VALUES (?, ?)",
                (k + 1, "2026-09-20T%02d:00:00+00:00" % k if k < 24
                 else "2026-09-21T%02d:00:00+00:00" % (k - 24)))
    for item in ("Cap", "Fez"):
        con.execute("INSERT INTO snapshots (pass_id, item, quality, key_price_ref, scraped_at, "
                    "book_json) VALUES (?, ?, 6, ?, '2026-09-20T00:00:00+00:00', ?)",
                    (k + 1, item, KREF, json.dumps(book(k, item))))


def main():
    tmp = tempfile.mkdtemp()
    db = _os.path.join(tmp, "trading.db")
    cache = _os.path.join(tmp, "cache")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE passes (id INTEGER PRIMARY KEY, started_at TEXT)")
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, pass_id INTEGER, item TEXT, "
                "quality INTEGER, key_price_ref REAL, scraped_at TEXT, book_json TEXT)")
    for k in range(40):
        add_pass(con, k)
    con.commit()
    con.close()

    # registry: every bot-badged account, providers by hand (the roster path is tested
    # in test_bot_registry.py)
    rc = br.connect(db)
    for sid in ("R", "S", "F1", "F2", "X1", "X2", "T", "C", "HB"):
        rc.execute("INSERT INTO bot_account VALUES (?, 1, '2026-09-20', '2026-09-25')", (sid,))
    rc.commit()
    rc.close()
    for sid, prov in (("R", "gladiator"), ("S", "gladiator"), ("F1", "scrapyard"),
                      ("F2", "scrapyard"), ("X1", "tf2syntax"), ("X2", "tf2syntax"),
                      ("T", "tf2autobot"), ("HB", "human")):
        br.mark(sid, prov, db=db)

    print("observations")
    obs = load_bot_obs(db, cache_dir=cache)
    check("one row per account x item x side x pass (9 + T's 5 + C's 1 per item)",
          len(obs), 2 * (40 * 9 + 5) + 1)
    top = obs[(obs.item == "Cap") & (obs.pass_id == 1) & (obs["rank"] == 1)].iloc[0]
    check("rank 1 is the one-scrap overbid, exact ref gap 0.11", (top.steamId, float(top.gap_ref)),
          ("C", 0.11))

    print("pricing mode")
    m = pricing_modes(obs).set_index("steamId")
    check("S never reprices -> static", (m.pricing_mode["S"], float(m.reprice_rate["S"])), ("static", 0.0))
    check("R tracks the book -> repricing", (m.pricing_mode["R"], float(m.track_rate["R"])), ("repricing", 1.0))
    check("T: 4 book moves per item -> thin", m.pricing_mode["T"], "thin")
    m5 = pricing_modes(obs, window_h=5).set_index("steamId")
    check("a 5 h window leaves S too little data -> thin", m5.pricing_mode["S"], "thin")

    print("groups")
    g = behaviour_groups(db, obs=obs).set_index("steamId")
    check("fleet members share one group", (g.group["F1"], g.group["F2"]), ("scrapyard", "scrapyard"))
    check("gladiator splits by pricing mode", (g.group["S"], g.group["R"]),
          ("gladiator:static", "gladiator:repricing"))
    check("NOT_ONE_OPERATOR fleet -> provider x mode", g.group["X1"], "tf2syntax:repricing")
    check("thin tf2autobot bot", g.group["T"], "tf2autobot:thin")
    check("unknown provider, no transitions -> unknown:thin", g.group["C"], "unknown:thin")
    check("human mark is not a bot", "HB" in g.index, False)

    print("fleet check")
    fc = fleet_check(obs, behaviour_groups(db, obs=obs)).set_index("provider")
    check("scrapyard prices as one", float(fc.same_price["scrapyard"]), 1.0)
    check("tf2syntax does not (X2 bids 10% under X1)", float(fc.same_price["tf2syntax"]), 0.0)
    check("gladiator S vs R agree only at pass 1 of 40 (of the last 40)",
          round(float(fc.same_price["gladiator"]), 3), round(2 / 80, 3))
    check("baseline computed over cross-provider pairs", fc.attrs["baseline_pairs"] > 0, True)

    print("cache")
    con = sqlite3.connect(db)
    add_pass(con, 40)
    con.commit()
    con.close()
    warm = load_bot_obs(db, cache_dir=cache)
    fresh = load_bot_obs(db, use_cache=False)
    check("cache appends the new pass", len(warm), len(obs) + 2 * 9 - 0)
    check("cached read == fresh read", sorted(map(tuple, warm[["steamId", "item", "pass_id", "price"]].values))
          == sorted(map(tuple, fresh[["steamId", "item", "pass_id", "price"]].values)), True)

    print("\n%d failure(s)" % len(fails) if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

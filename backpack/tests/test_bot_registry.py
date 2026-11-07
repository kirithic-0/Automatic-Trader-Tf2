# -*- coding: utf-8 -*-
"""Offline test for the static bot registry: bot_registry.py (collection) + engine/bots.py (rule).

A throwaway trading.db with a few hand-built books and a fake Steam (no network).
Pins: only bot-badged accounts are registered (any quality, incremental by
watermark); roster members are recorded only for registered accounts and stay
once seen; profile XML is parsed and stored once; mark() refuses unregistered
accounts and unknown providers; the provider precedence (manual > dedicated roster
> TF2Autobot group > unknown, dedicated conflict -> unknown); human -> not a bot;
account_stats counts and its incremental cache.

    python test_bot_registry.py
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
from engine.bots import account_stats, provider_for, registry

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-58s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, bot=True, user=None):
    return {"steamId": sid, "user": user or ("u" + sid), "price": {"total_keys": 1.0},
            "listingType": "bot" if bot else "normal", "premium": False}


def add_snapshot(con, item, quality, buys, sells):
    con.execute("INSERT INTO snapshots (pass_id, item, quality, scraped_at, book_json) "
                "VALUES (1, ?, ?, '2026-09-20T00:00:00+00:00', ?)",
                (item, quality, json.dumps({"buyOrders": buys, "sellOrders": sells})))


PROFILE = """<?xml version="1.0" encoding="UTF-8"?><profile>
<steamID64>{sid}</steamID64><steamID><![CDATA[Bot {sid}]]></steamID>
<privacyState>public</privacyState><vacBanned>0</vacBanned>
<tradeBanState>None</tradeBanState><isLimitedAccount>0</isLimitedAccount>
<memberSince>November 9, 2021</memberSince>
<summary><![CDATA[owner: <a href="x">someone</a>]]></summary>
<groups><group isPrimary="1"><groupID64>1</groupID64>
<groupName><![CDATA[Meet The Stats]]></groupName><groupURL><![CDATA[meetthestats]]></groupURL>
</group></groups></profile>"""


def roster_xml(ids, page=1, pages=1):
    return ("<?xml version='1.0'?><memberList><totalPages>%d</totalPages><currentPage>%d</currentPage>"
            "<members>%s</members></memberList>"
            % (pages, page, "".join("<steamID64>%s</steamID64>" % i for i in ids)))


ROSTERS = {"gladiatortfbots": [["G1", "BOTH", "H1"]],          # H1: human, never badged
           "merchanttfbots": [["M1", "BOTH"]],
           "TF2Autobot": [["A1", "G1"], ["A2", "H1"]]}         # two pages
BASE = br.STEAMID64_BASE
QS = {"QMOD": BASE + 101, "QOWN": BASE + 102, "QOFF": BASE + 103, "QPLAIN": BASE + 104}


def members_html(blocks):
    """A Steam group members page: [(account-id, rank title or None)]."""
    out = []
    for aid, rank in blocks:
        icon = '<div class="rank_icon" title="%s"><img class="officerIcon"></div>' % rank if rank else ""
        out.append('<div class="member_block " data-miniprofile="%d">%s'
                   '<a class="linkFriend" href="x">n</a></div>' % (aid - BASE, icon))
    return "<html>" + "".join(out) + "</html>"


QS_PAGES = [[(QS["QOWN"], "Group Owner"), (QS["QMOD"], "Group Moderator")],
            [(QS["QOFF"], "Group Moderator"), (QS["QPLAIN"], None)]]   # ranks span 2 pages
fetched = []


API_BOTS = ["GAPI", "GBOTH", "NEVERSEEN"]     # gladiator.tf/api/bots


def fake_fetch(url, retries=3):
    fetched.append(url)
    if url == br.API_ROSTERS["api:gladiator.tf"]:
        return 200, json.dumps({"success": 1, "bots": API_BOTS})
    if "/profiles/" in url:
        sid = url.split("/profiles/")[1].split("/")[0]
        return (404, "") if sid == "A2" else (200, PROFILE.format(sid=sid))
    grp = url.split("/groups/")[1].split("/")[0]
    page = int(url.rsplit("p=", 1)[1])
    if "/members?" in url:
        return 200, members_html(QS_PAGES[page - 1])
    pages = ROSTERS[grp]
    return 200, roster_xml(pages[page - 1], page, len(pages))


def main():
    br.fetch = fake_fetch
    br.PACE_S = 0
    br.GROUPS = ("gladiatortfbots", "api:gladiator.tf", "merchanttfbots", "TF2Autobot",
                 "qsstore@moderators")
    ROSTERS["gladiatortfbots"][0].append("GBOTH")
    tmp = tempfile.mkdtemp()
    db = _os.path.join(tmp, "trading.db")
    cache = _os.path.join(tmp, "cache")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, pass_id INTEGER, item TEXT, "
                "quality INTEGER, scraped_at TEXT, book_json TEXT)")
    add_snapshot(con, "Stogie", 6, [L("G1"), L("A1"), L("H1", bot=False)], [L("M1"), L("X1")])
    add_snapshot(con, "Stogie", 5, [L("BOTH")], [L("A2"), L("X1", bot=False)])   # Unusual counts too
    add_snapshot(con, "Fez", 6, [L(str(QS["QMOD"])), L(str(QS["QOWN"])), L(str(QS["QPLAIN"]))], [])
    add_snapshot(con, "Tam", 6, [L("GAPI"), L("GBOTH")], [])
    con.commit()
    con.close()

    print("sync #1")
    br.sync(db, out=lambda *a: None)
    con = sqlite3.connect(db)
    accts = sorted(r[0] for r in con.execute("SELECT steamId FROM bot_account"))
    check("registers every badged account, any quality", accts,
          sorted(["A1", "A2", "BOTH", "G1", "M1", "X1", str(QS["QMOD"]), str(QS["QOWN"]),
                  str(QS["QPLAIN"]), "GAPI", "GBOTH"]))
    check("api:gladiator.tf: registered bots on gladiator's own list",
          sorted(r[0] for r in con.execute("SELECT steamId FROM bot_group_member "
                                            "WHERE grp='api:gladiator.tf'")), ["GAPI", "GBOTH"])
    check("qsstore@moderators: moderators only, registered only, across pages",
          sorted(r[0] for r in con.execute("SELECT steamId FROM bot_group_member "
                                            "WHERE grp='qsstore@moderators'")), [str(QS["QMOD"])])
    members = sorted(con.execute("SELECT steamId, grp FROM bot_group_member "
                                 "WHERE grp NOT IN ('qsstore@moderators', 'api:gladiator.tf') "
                                 "AND steamId != 'GBOTH'").fetchall())
    check("roster members: registered accounts only (no H1), paged",
          members, [("A1", "TF2Autobot"), ("A2", "TF2Autobot"), ("BOTH", "gladiatortfbots"),
                    ("BOTH", "merchanttfbots"), ("G1", "TF2Autobot"), ("G1", "gladiatortfbots"),
                    ("M1", "merchanttfbots")])
    prof = dict(con.execute("SELECT steamId, error FROM steam_profile").fetchall())
    check("one profile fetch per account; 404 stored as an error", prof.get("A2"), "HTTP 404")
    check("profile parsed", con.execute("SELECT name, member_since, summary FROM steam_profile "
                                        "WHERE steamId='G1'").fetchone(),
          ("Bot G1", "November 9, 2021", "owner: someone"))

    print("sync #2 (new snapshot, G1 dropped from its roster)")
    add_snapshot(con, "Cap", 11, [L("N1")], [])
    con.commit()
    con.close()
    ROSTERS["gladiatortfbots"] = [["BOTH"]]
    fetched.clear()
    br.sync(db, out=lambda *a: None)
    con = sqlite3.connect(db)
    check("incremental: new badged account added", con.execute(
        "SELECT count(*) FROM bot_account WHERE steamId='N1'").fetchone()[0], 1)
    check("membership permanent once seen", con.execute(
        "SELECT count(*) FROM bot_group_member WHERE steamId='G1' AND grp='gladiatortfbots'"
    ).fetchone()[0], 1)
    profiled = sorted(u.split("/profiles/")[1].split("/")[0] for u in fetched if "/profiles/" in u)
    check("profiles: only new + previously failed are fetched", profiled, ["A2", "N1"])
    con.close()

    print("sync #3 (Steam 429s a roster)")
    real_fetch, calls = br.fetch, []

    def limited(url, retries=3):
        calls.append(url)
        if "/groups/" in url:
            return 429, ""
        return real_fetch(url, retries)
    br.fetch = limited
    logged = []
    br.sync(db, out=logged.append)
    br.fetch = real_fetch
    steam_calls = [u for u in calls if "/groups/" in u]
    check("after the first Steam 429 the other Steam rosters are skipped", len(steam_calls), 1)
    check("the gladiator.tf API roster still runs", any("gladiator.tf/api" in u for u in calls), True)
    check("skips are logged", sum("skipped (Steam rate-limited)" in m for m in logged), 3)

    print("mark")
    for args, want in [(("H1", "human"), "not a registered"),
                       (("A1", "gladiatr"), "unknown provider")]:
        try:
            br.mark(*args, db=db)
            check("mark refuses %s" % (args,), "accepted", want)
        except ValueError as e:
            check("mark refuses %s" % (args,), want in str(e), True)
    br.mark("X1", "human", note="trades by hand", db=db)
    br.mark("A1", "other_bot", db=db)
    br.mark("A1", "shopbot", new=True, db=db)                 # latest wins; --new adds a name
    br.mark("N1", "shopbot", db=db)                           # now a known provider

    print("registry")
    reg = registry(db).set_index("steamId")
    got = {sid: (reg.provider[sid], reg.provider_source[sid]) for sid in reg.index}
    check("G1: dedicated roster beats TF2Autobot", got["G1"], ("gladiator", "roster:gladiatortfbots"))
    check("M1: merchant roster", got["M1"], ("merchanttf", "roster:merchanttfbots"))
    check("gladiator.tf API list alone -> gladiator", got["GAPI"], ("gladiator", "roster:api:gladiator.tf"))
    check("in both gladiator lists -> gladiator, no conflict",
          (got["GBOTH"][0], bool(reg.conflict["GBOTH"])), ("gladiator", False))
    check("qsstore moderator -> quicksell; owner is not", (got[str(QS["QMOD"])][0],
          got[str(QS["QOWN"])][0]), ("quicksell", "unknown"))
    check("BOTH: two dedicated rosters -> unknown + conflict",
          (got["BOTH"][0], bool(reg.conflict["BOTH"])), ("unknown", True))
    check("A2: TF2Autobot group + badge", got["A2"], ("tf2autobot", "group+badge:TF2Autobot"))
    check("A1: manual mark wins, latest one", got["A1"], ("shopbot", "manual"))
    check("X1: human mark -> not a bot", (got["X1"][0], bool(reg.is_bot["X1"])), ("human", False))
    check("profile groups joined in", "meetthestats" in reg.groups["G1"], True)
    check("rule: no evidence -> unknown", provider_for(None, set()), ("unknown", "none", False))

    print("account_stats")
    st = account_stats(db, cache_dir=cache).set_index("steamId")
    check("X1: 2 listings, half badged, 2 item-qualities", (st.n_listings["X1"], st.badge_share["X1"],
                                                  st.n_items["X1"]), (2, 0.5, 2))
    con = sqlite3.connect(db)
    add_snapshot(con, "Cap", 11, [L("N1", user="renamed")], [])
    con.commit()
    con.close()
    st = account_stats(db, cache_dir=cache).set_index("steamId")
    check("incremental cache adds, latest name wins", (st.n_listings["N1"], st.listing_name["N1"],
                                                       st.n_items["N1"]), (2, "renamed", 1))
    check("fresh read agrees with cache", account_stats(db, use_cache=False)
          .set_index("steamId").n_listings.to_dict(), st.n_listings.to_dict())

    print("\n%d failure(s)" % len(fails) if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

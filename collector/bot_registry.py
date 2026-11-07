"""The static bot registry: who each bot account is. Collection side -- writes trading_app.db.

    python bot_registry.py sync [--refresh-profiles]     # new bots, rosters, profiles
    python bot_registry.py mark <steamId> <provider> [--note TEXT] [--new]

Browse the result (read side): `python -m engine bots [--unknown]`.

The registry holds only *facts*, append-only, in four raw tables:

- `bot_account(steamId, first_snapshot_id, first_seen_at, added_at)` -- every
  account that has carried bp.tf's bot badge (`listingType == "bot"`) on any
  listing, any quality. Only badged accounts are registered or markable.
- `bot_group_member(steamId, grp, first_seen_at)` -- a registered account seen in
  one of the GROUPS rosters (a Steam group, `<group>@<rank>`, or `api:<provider>`). Permanent once seen; a later pull that no longer lists
  it changes nothing.
- `steam_profile(steamId, fetched_at, ...)` -- the account's public Steam profile
  XML (raw + parsed), one row per fetch. Fetched once per account; re-fetch with
  --refresh-profiles.
- `bot_label(steamId, provider, set_at, note)` -- your manual marks; latest wins.
- `bot_sync` -- one row per sync: the snapshot watermark and what each step did.

The provider *rule* (manual > dedicated roster > TF2Autobot group > unknown) is not
here: it is read-side, in engine/bots.py (`registry()`).

Steam: profile XML (`/profiles/<id>/?xml=1`) and group rosters
(`/groups/<g>/memberslistxml/?xml=1&p=N`, 1,000 ids a page) are plain HTTP, no
Cloudflare. Measured 2026-09-24: 40+ profile fetches at 1/s all 200. The
inventory endpoint 429'd on the first request, so it is not used.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import book_codec

DEFAULT_DB = str(Path(__file__).resolve().parent / "trading_app.db")

# The Steam groups whose rosters sync pulls. What each one *means* (provider, and
# whether membership alone is enough) is the read-side rule: engine/bots.py GROUP_RULE.
# Collection and engine never import each other -- keep the two lists in step.
# "<group>@<rank>" pulls only the members holding that rank from the group's HTML
# member page (the XML roster carries no ranks): qsstore's moderators are the
# Quicksell.store bots, its owner (zol) is the human who runs them.
GROUPS = ("gladiatortfbots", "api:gladiator.tf", "merchanttfbots", "TF2Autobot", "qsstore@moderators",
          # small fleet groups (2026-09-25, each a single roster page)
          "itemfortbots", "elsestore", "tf2autobot-upgrade", "TF2Syntax",
          "SafiStores", "tweakmaxing", "sentrytf", "sawtradeshop", "bravobot", "Gn1dor43_TF2")
RANK_TITLE = {"moderators": "Group Moderator", "owner": "Group Owner"}
# "api:<name>" rosters are a provider's own published bot list, not a Steam group.
# gladiator.tf/api/bots is what its Instant Trade userscript uses to recognise its bots
# on backpack.tf: {"success": 1, "bots": [steamID64, ...]} (302 on 2026-09-25).
API_ROSTERS = {"api:gladiator.tf": "https://gladiator.tf/api/bots"}
PROVIDERS = ("gladiator", "tf2autobot", "merchanttf", "scrapyard", "quicksell",
             # fleets with their own group (rosters, 2026-09-25)
             "itemfort", "trademann", "polishbot", "tf2syntax", "safi", "japantrip",
             "sentrytf", "superanimalworld", "bravobot", "gn1dor43",
             # fleets by name + shared summary, no roster (manual marks, 2026-09-25)
             "ctradetf", "junkertf", "paintedhats", "clone", "gradle", "sfuminator",
             "hex", "ozenreg", "cobratf",
             "other_bot", "human", "unknown")

PROFILE_URL = "https://steamcommunity.com/profiles/{sid}/?xml=1"
ROSTER_URL = "https://steamcommunity.com/groups/{grp}/memberslistxml/?xml=1&p={page}"
MEMBERS_URL = "https://steamcommunity.com/groups/{grp}/members?p={page}"
STEAMID64_BASE = 0x0110000100000000   # Steam's SteamID64 base; + the 32-bit account id in data-miniprofile
UA = "Mozilla/5.0"
PACE_S = 1.0                     # one Steam request a second

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_account (
    steamId TEXT PRIMARY KEY,
    first_snapshot_id INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_group_member (
    steamId TEXT NOT NULL,
    grp TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (steamId, grp)
);
CREATE TABLE IF NOT EXISTS steam_profile (
    id INTEGER PRIMARY KEY,
    steamId TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status INTEGER,
    error TEXT,
    name TEXT,
    privacy TEXT,
    member_since TEXT,
    trade_ban TEXT,
    is_limited INTEGER,
    vac_banned INTEGER,
    groups_json TEXT,
    summary TEXT,
    raw_xml TEXT
);
CREATE INDEX IF NOT EXISTS ix_steam_profile_sid ON steam_profile (steamId);
CREATE TABLE IF NOT EXISTS bot_label (
    id INTEGER PRIMARY KEY,
    steamId TEXT NOT NULL,
    provider TEXT NOT NULL,
    set_at TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS bot_sync (
    id INTEGER PRIMARY KEY,
    synced_at TEXT NOT NULL,
    max_snapshot_id INTEGER NOT NULL,
    n_new_accounts INTEGER,
    rosters_json TEXT,
    n_profiles INTEGER,
    n_profile_errors INTEGER
);
"""



def now():
    return datetime.now(timezone.utc).isoformat()


def connect(db=DEFAULT_DB):
    con = sqlite3.connect(db, timeout=60)      # the scraper may be writing (WAL)
    con.executescript(SCHEMA)
    return con


# -- Steam ------------------------------------------------------------------

def fetch(url, retries=5):
    """(status, body). Backs off on 429/5xx (15, 30, 60, 120 s); the final status is
    returned, not raised. The roster endpoint's 429 can outlast the whole backoff
    (2026-09-24: 429 on merchanttfbots + TF2Autobot through 15+30+60+120 s, while
    profile XML kept answering 200) -- sync then skips it until next time."""
    status, body = None, ""
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, body = e.code, ""
            if e.code not in (429, 500, 502, 503):
                return status, body
        except (urllib.error.URLError, TimeoutError) as e:
            status, body = None, str(e)
        if attempt < retries - 1:
            time.sleep(PACE_S * 15 * 2 ** attempt)
    return status, body


def parse_roster(xml_text):
    """memberslistxml page -> (steamId64 list, totalPages)."""
    root = ET.fromstring(xml_text)
    ids = [e.text.strip() for e in root.iter("steamID64") if e.text]
    pages = root.findtext("totalPages")
    return ids, int(pages) if pages and pages.strip().isdigit() else 1


def parse_ranked(html):
    """Group members HTML page -> [(steamId64, rank title or None)] in page order."""
    out = []
    for block in re.split(r'<div class="member_block[ "]', html)[1:]:
        aid = re.search(r'data-miniprofile="(\d+)"', block)
        if not aid:
            continue
        rank = re.search(r'class="rank_icon" title="([^"]+)"', block)
        out.append((str(STEAMID64_BASE + int(aid.group(1))), rank.group(1) if rank else None))
    return out


def fetch_ranked(grp, rank):
    """The members of `grp` holding `rank`. Steam lists ranked members first, so pages
    are read until one contains an unranked member (or is empty)."""
    ids, page = [], 1
    while True:
        status, body = fetch(MEMBERS_URL.format(grp=grp, page=page))
        if status != 200:
            raise RuntimeError(f"ranks {grp} p{page}: HTTP {status}")
        members = parse_ranked(body)
        ids += [sid for sid, r in members if r == RANK_TITLE[rank]]
        time.sleep(PACE_S)
        if not members or any(r is None for _, r in members):
            return ids
        page += 1


def parse_api_roster(body):
    """A provider's published bot list -> steamId64s. Raises on anything but success."""
    data = json.loads(body)
    if not data.get("success") or not isinstance(data.get("bots"), list):
        raise RuntimeError("unexpected bot list shape")
    return [str(b) for b in data["bots"]]


def fetch_roster(grp):
    """Every steamId64 in a group's public member list; "<group>@<rank>" -> that rank only;
    "api:<name>" -> that provider's own bot list."""
    if grp in API_ROSTERS:
        status, body = fetch(API_ROSTERS[grp])
        if status != 200:
            raise RuntimeError(f"roster {grp}: HTTP {status}")
        return parse_api_roster(body)
    if "@" in grp:
        return fetch_ranked(*grp.split("@", 1))
    ids, page, pages = [], 1, 1
    while page <= pages:
        status, body = fetch(ROSTER_URL.format(grp=grp, page=page))
        if status != 200:
            raise RuntimeError(f"roster {grp} p{page}: HTTP {status}")
        got, pages = parse_roster(body)
        ids += got
        page += 1
        time.sleep(PACE_S)
    return ids


def _strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_profile(xml_text):
    """Profile XML -> dict of the columns steam_profile stores (raw_xml aside)."""
    root = ET.fromstring(xml_text)
    if root.tag != "profile":
        return {"error": _strip_html(root.findtext("error") or root.tag)}

    def txt(tag):
        v = root.findtext(tag)
        return v.strip() if v is not None else None

    def num(tag):
        v = txt(tag)
        return int(v) if v is not None and v.isdigit() else None

    groups = []
    for g in root.iter("group"):
        groups.append({"id64": (g.findtext("groupID64") or "").strip(),
                       "url": (g.findtext("groupURL") or "").strip() or None,
                       "name": (g.findtext("groupName") or "").strip() or None,
                       "primary": g.get("isPrimary") == "1"})
    return {"error": None, "name": txt("steamID"), "privacy": txt("privacyState"),
            "member_since": txt("memberSince"), "trade_ban": txt("tradeBanState"),
            "is_limited": num("isLimitedAccount"), "vac_banned": num("vacBanned"),
            "groups_json": json.dumps(groups), "summary": _strip_html(txt("summary"))}


# -- sync -------------------------------------------------------------------

def register_new(con):
    """Add every newly badged account since the last sync. Returns (n_new, watermark)."""
    last = con.execute("SELECT COALESCE(MAX(max_snapshot_id), 0) FROM bot_sync").fetchone()[0]
    top = con.execute("SELECT COALESCE(MAX(id), 0) FROM snapshots").fetchone()[0]
    # Python, not json_each: books are zstd-compressed since 2026-10-04 (book_codec).
    codec = book_codec.Codec.from_con(con)
    first = {}                                 # steamId -> [min snapshot id, min scraped_at]
    for snap_id, scraped_at, value in con.execute(
            "SELECT id, scraped_at, book_json FROM snapshots WHERE id > ? AND id <= ?", (last, top)):
        book = codec.decode(value)
        for side in ("buyOrders", "sellOrders"):
            for o in book.get(side) or []:
                sid = o.get("steamId")
                if not sid or o.get("listingType") != "bot":
                    continue
                f = first.get(sid)
                if f is None:
                    first[sid] = [snap_id, scraped_at]
                else:
                    f[0], f[1] = min(f[0], snap_id), min(f[1], scraped_at)
    rows = [(sid, f[0], f[1]) for sid, f in first.items()]
    t = now()
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO bot_account VALUES (?, ?, ?, ?)",
                    [(sid, first_id, first_at, t) for sid, first_id, first_at in rows if sid])
    return con.total_changes - before, top


def record_members(con, grp, roster_ids):
    """Insert (account, grp) for registered accounts in the roster. Returns rows added."""
    registered = {r[0] for r in con.execute("SELECT steamId FROM bot_account")}
    t = now()
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO bot_group_member VALUES (?, ?, ?)",
                    [(sid, grp, t) for sid in set(roster_ids) & registered])
    return con.total_changes - before


def store_profile(con, sid, status, body):
    row = {"error": f"HTTP {status}"} if status != 200 else parse_profile(body)
    cols = ["error", "name", "privacy", "member_since", "trade_ban", "is_limited",
            "vac_banned", "groups_json", "summary"]
    con.execute(f"INSERT INTO steam_profile (steamId, fetched_at, status, {', '.join(cols)}, raw_xml) "
                f"VALUES (?, ?, ?, {', '.join('?' * len(cols))}, ?)",
                (sid, now(), status, *[row.get(c) for c in cols],
                 body if status == 200 else None))
    return row["error"] is None


def sync(db=DEFAULT_DB, refresh_profiles=False, out=print):
    con = connect(db)
    try:
        n_new, top = register_new(con)
        con.commit()
        out(f"accounts: {n_new} new badged account(s) registered "
            f"(total {con.execute('SELECT count(*) FROM bot_account').fetchone()[0]})")

        rosters = {}
        steam_limited = False
        for grp in GROUPS:
            if steam_limited and grp not in API_ROSTERS:
                # once Steam 429s one roster past the whole backoff, the rest of this sync
                # would too (2026-09-25: 5 in a row, ~4 min each) -- skip, retry next sync
                rosters[grp] = {"error": "skipped: Steam rate-limited"}
                out(f"roster {grp:16} skipped (Steam rate-limited) -- retried next sync")
                continue
            try:
                ids = fetch_roster(grp)
            except RuntimeError as e:              # partial roster: store nothing, retry next sync
                rosters[grp] = {"error": str(e)}
                out(f"roster {grp:16} FAILED ({e}) -- retried next sync")
                steam_limited = steam_limited or (grp not in API_ROSTERS and "HTTP 429" in str(e))
                continue
            added = record_members(con, grp, ids)
            con.commit()
            rosters[grp] = {"roster": len(ids), "added": added}
            out(f"roster {grp:16} {len(ids):6} members, {added} new registered member(s)")

        q = "SELECT steamId FROM bot_account"
        if not refresh_profiles:
            q += " WHERE steamId NOT IN (SELECT DISTINCT steamId FROM steam_profile WHERE error IS NULL)"
        todo = [r[0] for r in con.execute(q + " ORDER BY first_snapshot_id")]
        out(f"profiles: fetching {len(todo)} (~{len(todo) * PACE_S / 60:.0f} min at {PACE_S:.0f}/s)")
        ok = bad = 0
        for i, sid in enumerate(todo, 1):
            status, body = fetch(PROFILE_URL.format(sid=sid))
            try:
                good = store_profile(con, sid, status, body)
            except ET.ParseError:
                good = store_profile(con, sid, -1, "")
            ok, bad = ok + good, bad + (not good)
            con.commit()
            if status == 429:
                out("  Steam 429 after backoff -- stopping; the rest are fetched next sync")
                break
            if i % 50 == 0:
                out(f"  {i}/{len(todo)}")
            time.sleep(PACE_S)
        out(f"profiles: {ok} stored, {bad} error(s)")

        con.execute("INSERT INTO bot_sync (synced_at, max_snapshot_id, n_new_accounts, "
                    "rosters_json, n_profiles, n_profile_errors) VALUES (?, ?, ?, ?, ?, ?)",
                    (now(), top, n_new, json.dumps(rosters), ok, bad))
        con.commit()
    finally:
        con.close()


# -- manual marks -----------------------------------------------------------

def known_providers(con):
    return set(PROVIDERS) | {r[0] for r in con.execute("SELECT DISTINCT provider FROM bot_label")}


def mark(steam_id, provider, note=None, new=False, db=DEFAULT_DB):
    con = connect(db)
    try:
        if not con.execute("SELECT 1 FROM bot_account WHERE steamId = ?", (steam_id,)).fetchone():
            raise ValueError(f"{steam_id} is not a registered (bot-badged) account -- run sync?")
        if provider not in known_providers(con) and not new:
            raise ValueError(f"unknown provider {provider!r}; known: "
                             f"{', '.join(sorted(known_providers(con)))} (--new to add one)")
        con.execute("INSERT INTO bot_label (steamId, provider, set_at, note) VALUES (?, ?, ?, ?)",
                    (steam_id, provider, now(), note))
        con.commit()
    finally:
        con.close()


# -- CLI --------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="register new badged accounts, pull rosters, fetch profiles")
    s.add_argument("--refresh-profiles", action="store_true")
    m = sub.add_parser("mark", help="manually set an account's provider")
    m.add_argument("steam_id")
    m.add_argument("provider")
    m.add_argument("--note")
    m.add_argument("--new", action="store_true", help="allow a provider name not seen before")
    args = ap.parse_args(argv)

    if args.cmd == "sync":
        sync(args.db, args.refresh_profiles)
    elif args.cmd == "mark":
        try:
            mark(args.steam_id, args.provider, args.note, args.new, args.db)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"marked {args.steam_id} -> {args.provider}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

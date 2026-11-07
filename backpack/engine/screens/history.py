"""One item's book over time: the top buy order, the lowest sell, and the bid the agent can sell into.

Every stored book of one (item, quality): the hourly passes in trading_app.db (since
2026-10-02), the ~10-min watch books of what the agent holds or re-checks (`watch_snapshots`,
since 2026-10-18), and -- with --old -- the frozen passes in trading.db (to 2026-10-01). Each
book is read by the engine's own rules (engine.book: valid, fenced): `bid1` the top buy
order, `bid5` the top-5 mean, `ask1` the lowest sell; `tbid` is the best *tradable* bid
(engine.listings: bot + instant, trusted, trade URL known) -- what an exit sells into -- with
the account behind it.

    python -m engine history "Socked and Loaded"
    python -m engine history "Hot Dogger" --quality 11 --days 7 --json hist.json

Each point carries its stored book's (src, id); `book(src, id)` explains that one book -- every
listing kept or dropped by the engine's rules, and whether the agent could trade with it (the
panel's Books tab).
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from engine import book as eb
from engine import listings as el
from engine.panel import APP_DB, PLAIN_KIND, TRADING_DB, _columns, book_decoder, connect_ro, plain_sql

# One good's kind as the Books tab names it: 'craftable.elevated.australium.crate.target'
# (panel.PLAIN_KIND's order). '1.0.0.0.0' is the plain good -- every series the engine trades.
PLAIN_CODE = ".".join(str(v) for v in PLAIN_KIND.values())
_KIND_RE = re.compile(r"\d{1,6}(\.\d{1,6}){4}")


def kind_sql(con, table, code):
    """The WHERE tail pinning one good's kind in `table`: plain_sql for the plain good, else
    every kind column equal to `code`'s value -- and nothing at all from a table without the
    kind columns (it holds plain books only)."""
    if code in (None, "", PLAIN_CODE):
        return plain_sql(con, table)
    if not _KIND_RE.fullmatch(code):
        raise ValueError(f"bad kind {code!r}")
    if not set(PLAIN_KIND) <= _columns(con, table):
        return " AND 0"
    return "".join(f" AND {c} = {int(v)}" for c, v in zip(PLAIN_KIND, code.split(".")))
from engine.screens._common import parser


def _r4(x):
    return None if x is None or x != x else round(float(x), 4)


def _point(sid, t, src, book, urls):
    r = eb.read_book(book)
    fb = eb.fence_buys(eb.valid_buys(book)) or []
    tb = el.tradable_buys(book, urls)
    return {"t": t, "src": src, "id": sid, "bid1": _r4(r.bid1), "bid5": _r4(r.bid5), "ask1": _r4(r.ask1),
            "n_buy": r.n_buy, "n_sell": r.n_sell, "bid1_user": fb[0][1].get("user") if fb else None,
            "tbid": _r4(tb[0]["total_keys"]) if tb else None, "tbid_user": tb[0]["user"] if tb else None}


def _rows(db, sql, args, src, urls):
    con = connect_ro(db)
    try:
        decode = book_decoder(con)
        return [_point(sid, t, src, decode(bj), urls) for sid, t, bj in con.execute(sql, args)]
    finally:
        con.close()


def history(item, quality=6, since=None, old=False, app_db=APP_DB, old_db=TRADING_DB, kind=None):
    """[point] oldest first, for one good (variant 0): `kind` a 'c.e.a.crate.target' code
    (history.PLAIN_CODE, the default, is the plain good)."""
    since = since or "0000"
    con = connect_ro(app_db)
    try:
        urls = el.trade_urls(con)
        has_watch = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'watch_snapshots'").fetchone()
        plain = {t: kind_sql(con, t, kind) for t in ("snapshots", "watch_snapshots")}
    finally:
        con.close()
    q = ("SELECT rowid, scraped_at, book_json FROM {t} WHERE item = ? AND quality = ? AND variant = 0 "
         "AND scraped_at >= ?{p} ORDER BY scraped_at")
    pts = _rows(app_db, q.format(t="snapshots", p=plain["snapshots"]), (item, quality, since), "pass", urls)
    if has_watch:
        pts += _rows(app_db, q.format(t="watch_snapshots", p=plain["watch_snapshots"]),
                     (item, quality, since), "watch", urls)
    if old and kind in (None, "", PLAIN_CODE):
        pts += _rows(old_db, q.format(t="snapshots", p=""), (item, quality, since), "old", urls)
    return sorted(pts, key=lambda p: p["t"])


_TABLES = {"pass": "snapshots", "watch": "watch_snapshots"}


def book(src, sid, app_db=APP_DB):
    """One stored book ("pass" or "watch", its id) explained, or None: engine.book.explain_book
    (every listing with the rule that kept or dropped it), each listing also given `agent`, the
    agent's verdict (engine.listings): None = it could trade with this one, else why not."""
    con = connect_ro(app_db)
    try:
        row = con.execute(f"SELECT scraped_at, key_price_ref, book_json FROM {_TABLES[src]} WHERE rowid = ?",
                          (int(sid),)).fetchone()
        if row is None:
            return None
        b = book_decoder(con)(row[2])
        urls = el.trade_urls(con)
    finally:
        con.close()
    out = eb.explain_book(b)
    why = {(r["side"], r["steam_id"], r["total_keys"]): r["reason"] for r in el.explain(b, urls)}
    for side, rows in (("buy", out["buys"]), ("sell", out["sells"])):
        for r in rows:
            sid_ = None if r["steamId"] is None else str(r["steamId"])
            r["agent"] = why.get((side, sid_, r["tk"]), "not read")
    return {"src": src, "id": int(sid), "t": row[0], "key_ref": row[1], **out}


def items(app_db=APP_DB):
    """[[item, quality, kind code, Strangifier target or None, admitted]] of every good the
    collector watches -- its roster when there is one, else every series with a stored book --
    by name. kind = 'craftable.elevated.australium.crate.target' (PLAIN_CODE = the plain good)."""
    con = connect_ro(app_db)
    try:
        if "item" in _columns(con, "roster") and con.execute("SELECT 1 FROM roster LIMIT 1").fetchone():
            return [[i, q, f"{c}.{e}.{a}.{cr}.{t}", tn, adm] for i, q, c, e, a, cr, t, tn, adm in con.execute(
                "SELECT item, quality, craftable, elevated, australium, crate, target, target_name, admitted "
                "FROM roster ORDER BY item, quality, craftable DESC, elevated, australium, crate, target")]
        return [[i, q, PLAIN_CODE, None, 1] for i, q in
                con.execute("SELECT DISTINCT item, quality FROM snapshots ORDER BY item, quality")]
    finally:
        con.close()


def resolve(name, app_db=APP_DB):
    """(the stored item name, []) for a name typed loosely (any case, or a unique part of it),
    else (None, the closest names)."""
    con = connect_ro(app_db)
    try:
        names = [r[0] for r in con.execute("SELECT DISTINCT item FROM snapshots")]
    finally:
        con.close()
    low = name.strip().lower()
    exact = [n for n in names if n.lower() == low]
    if exact:
        return exact[0], []
    part = [n for n in names if low in n.lower()]
    if len(part) == 1:
        return part[0], []
    if part:
        return None, sorted(part)[:12]
    import difflib
    return None, difflib.get_close_matches(name, names, n=8, cutoff=0.5)


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("item")
    ap.add_argument("--quality", type=int, default=6)
    ap.add_argument("--days", type=float, help="only the last N days")
    ap.add_argument("--old", action="store_true", help="also the frozen passes in trading.db (to 2026-10-01)")
    ap.add_argument("--json", help="write {item, quality, points} here")
    args = ap.parse_args(argv)
    since = ((datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat() if args.days else None)
    sys.stdout.reconfigure(errors="replace")   # bot names with emoji on a cp1252 console
    item, close = resolve(args.item)
    pts = history(item, args.quality, since, args.old) if item else []
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"item": item, "asked": args.item, "matches": close, "quality": args.quality, "points": pts}, f)
    if not item:
        print(f"history: no item {args.item!r}" + (f"; did you mean: {', '.join(close)}" if close else ""))
        return 1
    if not pts:
        print(f"history: no books for {item!r} q{args.quality}")
        return 1
    print(f"{item} q{args.quality}: {len(pts)} books, {pts[0]['t'][:16]} -> {pts[-1]['t'][:16]}")
    for p in pts[-12:]:
        print(f"  {p['t'][:16]} {p['src']:5} bid1 {p['bid1']}  ask1 {p['ask1']}  tradable bid {p['tbid']} ({p['tbid_user']})")
    return 0

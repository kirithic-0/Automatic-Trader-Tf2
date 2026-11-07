"""Who each bot account is: the provider rule over the static registry (read side).

    reg = registry()          # one row per registered (bot-badged) steamId
    bots = reg[reg.is_bot]    # the set the dynamic bot features are computed on
    stats = account_stats()   # per steamId: listings, badge share, items, last name

    python -m engine bots [--unknown]     # the registry with its evidence

The facts live in trading.db, written by `bot_registry.py sync|mark` (collection
side): bot_account, bot_group_member, steam_profile, bot_label. The rule that turns
them into a provider is here and nowhere else:

    1. a manual mark (latest bot_label row) wins;
    2. a dedicated bot roster (gladiatortfbots or gladiator.tf/api/bots -> gladiator,
       merchanttfbots -> merchanttf, qsstore's moderators -> quicksell, the small
       fleet groups below); two different dedicated rosters -> unknown, conflict=True;
    3. the TF2Autobot community group -> tf2autobot (counts only with the bot badge,
       which every registered account has);
    4. otherwise unknown.

A `human` mark takes the account out of the bot set (is_bot=False). Provider is
treated as static identity: today's label applies to all history.
"""
import json
import os

import pandas as pd

from engine.panel import DEFAULT_DB, admitted_sql, book_decoder, cache_dir_for, connect_ro, write_cache

# Steam group -> (provider, roster_enough). roster_enough: membership alone names the
# provider (a dedicated bot roster). False: a community group, which counts only with
# the bot badge -- every registered account has it by construction. The groups pulled
# are bot_registry.GROUPS (collection side); keep the two in step.
GROUP_RULE = {
    "gladiatortfbots": ("gladiator", True),
    "api:gladiator.tf": ("gladiator", True),    # gladiator.tf/api/bots, its own list
    "merchanttfbots": ("merchanttf", True),
    "TF2Autobot": ("tf2autobot", False),
    "qsstore@moderators": ("quicksell", True),  # Quicksell.store's bots (owner excluded)
    # small fleet groups, 4-31 members each (2026-09-25)
    "itemfortbots": ("itemfort", True),         # ItemFort Bots
    "elsestore": ("trademann", True),           # TradeMann - Auto Trading
    "tf2autobot-upgrade": ("polishbot", True),  # PolishBot / Kuina Bot
    "TF2Syntax": ("tf2syntax", True),           # TF2Syntax
    "SafiStores": ("safi", True),               # Safi Trading Store
    "tweakmaxing": ("japantrip", True),         # Japan Trip (Let's Leave Norway)
    "sentrytf": ("sentrytf", True),             # Sentry.tf bot service
    "sawtradeshop": ("superanimalworld", True), # Super Animal World Trade Shop
    "bravobot": ("bravobot", True),             # bravobot.com
    "Gn1dor43_TF2": ("gn1dor43", True),         # Gn1dor43 TF2
}

REG_COLS = ["steamId", "provider", "provider_source", "conflict", "is_bot", "groups",
            "name", "first_seen_at", "added_at", "member_since", "trade_ban",
            "is_limited", "vac_banned", "summary", "label_note"]


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def provider_for(label, member_groups):
    """(provider, provider_source, conflict) for one account -- the rule above."""
    if label:
        return label, "manual", False
    dedicated = sorted({GROUP_RULE[g][0] for g in member_groups if g in GROUP_RULE and GROUP_RULE[g][1]})
    if len(dedicated) > 1:
        return "unknown", "conflict:" + "+".join(dedicated), True
    if dedicated:
        grp = next(g for g in sorted(member_groups) if GROUP_RULE.get(g, ("", False))[0] == dedicated[0])
        return dedicated[0], "roster:" + grp, False
    community = sorted(g for g in member_groups if g in GROUP_RULE and not GROUP_RULE[g][1])
    if community:
        return GROUP_RULE[community[0]][0], "group+badge:" + community[0], False
    return "unknown", "none", False


def registry(db=DEFAULT_DB):
    """One row per registered steamId with its provider (see module doc)."""
    con = connect_ro(db)
    try:
        if "bot_account" not in _tables(con):
            return pd.DataFrame(columns=REG_COLS)
        acc = pd.read_sql("SELECT steamId, first_seen_at, added_at FROM bot_account", con)
        mem = pd.read_sql("SELECT steamId, grp FROM bot_group_member", con)
        lab = pd.read_sql("SELECT steamId, provider, note FROM bot_label ORDER BY id", con)
        prof = pd.read_sql("SELECT steamId, name, member_since, trade_ban, is_limited, "
                           "vac_banned, groups_json, summary FROM steam_profile "
                           "WHERE error IS NULL ORDER BY id", con)
    finally:
        con.close()

    lab = lab.drop_duplicates("steamId", keep="last").set_index("steamId")
    prof = prof.drop_duplicates("steamId", keep="last").set_index("steamId")
    groups_of = mem.groupby("steamId").grp.apply(set).to_dict()

    rows = []
    for sid, first_at, added_at in acc.itertuples(index=False):
        label = lab.provider.get(sid)
        member = groups_of.get(sid, set())
        provider, source, conflict = provider_for(label, member)
        p = prof.loc[sid] if sid in prof.index else None
        profile_groups = ([g["url"] or g["id64"] for g in json.loads(p.groups_json or "[]")]
                          if p is not None else [])
        rows.append({
            "steamId": sid, "provider": provider, "provider_source": source,
            "conflict": conflict, "is_bot": provider != "human",
            "groups": sorted(member | set(profile_groups)),
            "name": p["name"] if p is not None else None,
            "first_seen_at": first_at, "added_at": added_at,
            "member_since": p["member_since"] if p is not None else None,
            "trade_ban": p["trade_ban"] if p is not None else None,
            "is_limited": p["is_limited"] if p is not None else None,
            "vac_banned": p["vac_banned"] if p is not None else None,
            "summary": p["summary"] if p is not None else None,
            "label_note": lab.note.get(sid),
        })
    return pd.DataFrame(rows, columns=REG_COLS)


_ITEM_COLS = ["steamId", "item", "quality", "n", "n_bot", "last_snapshot_id", "listing_name"]


def _scan_listings(con, after, top):
    """(steamId, item, quality) listing counts + each account's latest listing name over
    snapshots (after, top]. Books are decoded in Python (book_decoder): trading_app.db
    stores them zstd-compressed, which SQL's json_each cannot read (2026-10-17)."""
    decode = book_decoder(con)
    agg, names = {}, {}
    for sid, item, qual, bj in con.execute(
            f"SELECT id, item, quality, book_json FROM snapshots WHERE id > ? AND id <= ?{admitted_sql(con)} "
            "ORDER BY id", (after, top)):
        book = decode(bj)
        for side in ("buyOrders", "sellOrders"):
            for o in book.get(side) or []:
                acct = o.get("steamId")
                k = (acct, item, qual)
                a = agg.get(k)
                if a is None:
                    a = agg[k] = [0, 0, sid]
                a[0] += 1
                a[1] += o.get("listingType") == "bot"
                a[2] = sid
                names[acct] = o.get("user")
    rows = [(*k, n, nb, last, names.get(k[0])) for k, (n, nb, last) in agg.items()]
    return pd.DataFrame(rows, columns=_ITEM_COLS)


def _account_items(con, cache_dir, use_cache):
    """(steamId, item, quality) listing counts over every snapshot, cached incrementally
    in engine_cache/account_items.parquet (snapshots is append-only; disposable)."""
    path = os.path.join(cache_dir, "account_items.parquet")
    top = con.execute("SELECT COALESCE(MAX(id), 0) FROM snapshots").fetchone()[0]
    cached, after = None, 0
    if use_cache and os.path.exists(path):
        cached = pd.read_parquet(path)
        after = int(cached.watermark.iloc[0]) if len(cached) else 0
        if after > top:                               # db reset under the cache
            cached, after = None, 0
    new = _scan_listings(con, after, top)
    if cached is not None and len(new):
        out = pd.concat([cached.drop(columns="watermark"), new], ignore_index=True)
        out = (out.sort_values("last_snapshot_id")
                  .groupby(["steamId", "item", "quality"], as_index=False)
                  .agg(n=("n", "sum"), n_bot=("n_bot", "sum"),
                       last_snapshot_id=("last_snapshot_id", "max"),
                       listing_name=("listing_name", "last")))
    elif cached is not None:
        out = cached.drop(columns="watermark")
    else:
        out = new
    if use_cache and (cached is None or len(new)):
        write_cache(out.assign(watermark=top), path)
    return out


def account_stats(db=DEFAULT_DB, cache_dir=None, use_cache=True):
    """Per steamId: n_listings, badge_share, n_items, listing_name (latest bp.tf name)."""
    cache_dir = cache_dir or cache_dir_for(db)
    con = connect_ro(db)
    try:
        ai = _account_items(con, cache_dir, use_cache)
    finally:
        con.close()
    g = ai.sort_values("last_snapshot_id").groupby("steamId")
    out = g.agg(n_listings=("n", "sum"), n_bot=("n_bot", "sum"),
                n_items=("item", "size"), listing_name=("listing_name", "last")).reset_index()
    out["badge_share"] = out.n_bot / out.n_listings
    return out.drop(columns="n_bot")

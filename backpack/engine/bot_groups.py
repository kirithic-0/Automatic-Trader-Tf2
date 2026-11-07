"""Behaviour groups: which bots can be treated as one behaviour (read side).

    obs = load_bot_obs()                   # one row per (account, item, side, pass)
    modes = pricing_modes(obs)             # per account: static | repricing | thin
    groups = behaviour_groups()            # per registered bot: fleet, pricing_mode, group
    check = fleet_check(obs, groups)       # does each fleet still price as one?

    python -m engine bot-groups

**The group key** (user decision 2026-09-25, measurements in docs/data-model.md →
"Does a group behave as one?"):

- an **operator fleet** -- a provider run by one owner (quicksell, scrapyard,
  itemfort, ...) -- is one group: its bots quote the same price 93-100% of the time
  against a 58% cross-group baseline;
- everything else -- `SERVICE_PROVIDERS` (gladiator, tf2autobot: each owner
  configures their own bot), unknown, and fleets listed in `NOT_ONE_OPERATOR` -- is
  grouped by **provider x pricing mode**, e.g. `gladiator:static`.

**Pricing mode** (point-in-time): over the `window_h` hours up to `as_of`, take each
account's consecutive observations of the same (item, quality, side). A "book move"
is the side's median moving more than 0.5%; a "reprice" is the account's own price
moving more than 0.2%. `static` = fewer than `static_below` of its transitions
reprice, over at least `min_moves` book moves; `repricing` = at least that; `thin`
= fewer book moves than `min_moves` (too little data -- it inherits its group's
behaviour later). A gladiator Basic bot cannot autoprice, so it must be static; a
static bot is a Basic *candidate*, not a confirmed one (Premium allows manual
listings too). Under/overcutting is a continuous feature (`gap_ref` at rank 1), not
a mode -- it has no gap to split on.

`load_bot_obs` reads raw **valid** listings (read rules 1-3: flags out, null price
out, deduped by steamId -- before the fence, so the fence never shapes the metrics
that may later feed it), every account, Unique + Strange; `kept` says whether the
listing also got past the fence (rule 4). Cached incrementally in
`engine_cache/bot_obs_v<READ_KEY>.parquet` (disposable, like the panel). A level-2
untrusted account (engine.book.UNTRUSTED) is out of the book, so it has no rows.
"""
import json
import os

import numpy as np
import pandas as pd

from engine.book import (READ_KEY, fence_buys, fence_median, fence_sells, median, valid_buys,
                         valid_sells)
from engine.bots import registry
from engine.panel import (DEFAULT_DB, SCOPES, book_decoder, cache_dir_for, connect_ro, universe_sql,
                          write_cache)

# Providers that are a hosting service or open-source bot software, not one operator:
# every bot is configured by its own owner, so the provider is not a behaviour.
SERVICE_PROVIDERS = {"gladiator", "tf2autobot", "unknown", "other_bot"}
# Operator fleets whose bots measurably do NOT price as one (fleet_check); their
# members are grouped like service bots. tf2syntax: 14% same price, 2026-09-25.
NOT_ONE_OPERATOR = {"tf2syntax"}

BOOK_MOVE = 0.005        # the side's median moved more than this -> a book move
REPRICE = 0.002          # the account's own price moved more than this -> a reprice
SAME_PRICE = 0.002       # two co-listed prices within this -> "the same price"
MAX_PASS_GAP = 2         # consecutive observations at most this many passes apart

OBS_COLS = ["snapshot_id", "pass_id", "steamId", "item", "quality", "side", "price",
            "median", "rank", "n_valid", "gap_ref", "kept"]


def book_key(obs):
    """The columns naming one book: item, quality and -- when the observations carry it
    (load_bot_obs adds it) -- variant."""
    return ["item", "quality", "variant"] if "variant" in obs else ["item", "quality"]


# -- the observation table --------------------------------------------------

def _gap_ref(a, b, kref):
    """|a - b| in ref for two (price, listing) pairs: exact from the ref fields when both
    are quoted in the same number of keys, else via the snapshot's key rate."""
    pa, pb = a[1].get("price") or {}, b[1].get("price") or {}
    if pa.get("keys") == pb.get("keys") and pa.get("ref") is not None and pb.get("ref") is not None:
        return round(abs(pa["ref"] - pb["ref"]), 2)
    return round(abs(a[0] - b[0]) * kref, 2) if kref else np.nan


def _obs_rows(sid, pid, item, qual, kref, book_json, decode=json.loads):
    book = decode(book_json)
    for side, vals, fence in (("b", valid_buys(book), fence_buys), ("s", valid_sells(book), fence_sells)):
        if not vals:
            continue
        m = fence_median(vals)
        if m is None:                                        # < MIN_VALID: no median forms
            m = median([p for p, _ in vals])
        n = len(vals)
        survived = {id(o) for _, o in fence(vals) or []}     # got past the fence (rule 4)
        for i, (p, o) in enumerate(vals):
            gap = _gap_ref(vals[i], vals[i + 1], kref) if i + 1 < n else np.nan
            yield (sid, pid, o.get("steamId"), item, qual, side, p, m, i + 1, n, gap, id(o) in survived)


def _read_obs(con, after_id):
    """The observations of the snapshots after `after_id` that the engine reads (universe_sql)."""
    rows = []
    decode = book_decoder(con)
    for r in con.execute("SELECT id, pass_id, item, quality, key_price_ref, book_json "
                         f"FROM snapshots WHERE {universe_sql(con)} AND id > ? ORDER BY id",
                         (after_id,)):
        rows.extend(_obs_rows(*r, decode=decode))
    df = pd.DataFrame(rows, columns=OBS_COLS)
    return df.astype({"snapshot_id": "int64", "quality": "int8", "rank": "int8",
                      "n_valid": "int8", "price": "float64", "median": "float64",
                      "gap_ref": "float64", "kept": "bool"})


def _cached_obs(con, path, use_cache):
    """_read_obs over the db, appended to the parquet at `path`."""
    cached = pd.read_parquet(path) if use_cache and os.path.exists(path) else None
    if cached is not None and set(OBS_COLS) - set(cached.columns):   # a new column: re-read all
        cached = None
    after = int(cached.snapshot_id.max()) if cached is not None and len(cached) else 0
    top = con.execute("SELECT COALESCE(MAX(id), 0) FROM snapshots").fetchone()[0]
    if after > top:                                   # db reset under the cache
        cached, after = None, 0
    new = _read_obs(con, after)
    out = new if cached is None else (pd.concat([cached, new], ignore_index=True) if len(new) else cached)
    if use_cache and (cached is None or len(new)):
        write_cache(out, path)
    return out


def load_bot_obs(db=DEFAULT_DB, cache_dir=None, use_cache=True, scope="admitted"):
    """Every raw valid listing of every account, one row per (steamId, item, quality,
    variant, side, pass) -- see module doc. Adds `t` (pass start, UTC). scope="admitted"
    (the only one in this copy): the books the engine reads (Unique + Strange, admitted),
    variant 0."""
    if scope not in SCOPES:
        raise ValueError(f"scope: one of {SCOPES}, not {scope!r}")
    cache_dir = cache_dir or cache_dir_for(db)
    con = connect_ro(db)
    try:
        out = _cached_obs(con, os.path.join(cache_dir, f"bot_obs_v{READ_KEY}.parquet"), use_cache)
        out = out.assign(variant=np.zeros(len(out), dtype="int64"))
        passes = pd.read_sql("SELECT id AS pass_id, started_at FROM passes", con)
    finally:
        con.close()
    out = (out.dropna(subset=["pass_id", "steamId"])
              .sort_values("snapshot_id")
              .drop_duplicates(["steamId", "item", "quality", "variant", "side", "pass_id"], keep="last"))
    passes["t"] = pd.to_datetime(passes.started_at, utc=True, format="ISO8601")
    out = out.astype({"pass_id": "int64"}).merge(passes[["pass_id", "t"]], on="pass_id", how="inner")
    return out.reset_index(drop=True)


# -- pricing mode -----------------------------------------------------------

def transitions(obs):
    """Consecutive observations of the same (steamId, item, quality, side), at most
    MAX_PASS_GAP passes apart: book_move / reprice / track flags per transition."""
    k = ["steamId", *book_key(obs), "side"]
    o = obs.sort_values(k + ["pass_id"])
    g = o.groupby(k, sort=False)
    prev_p, prev_m, prev_pid = g.price.shift(), g["median"].shift(), g.pass_id.shift()
    ok = prev_pid.notna() & (o.pass_id - prev_pid <= MAX_PASS_GAP)
    t = o.loc[ok, k + ["pass_id", "t"]].copy()
    t["t_prev"] = g.t.shift()[ok]
    dp = o.price[ok] / prev_p[ok] - 1
    dm = o["median"][ok] / prev_m[ok] - 1
    t["book_move"] = dm.abs() > BOOK_MOVE
    t["reprice"] = dp.abs() > REPRICE
    t["track"] = t.book_move & t.reprice & (np.sign(dp) == np.sign(dm))
    return t


def pricing_modes(obs, as_of=None, window_h=168, min_moves=30, static_below=0.01, tr=None):
    """Per steamId, point-in-time: n_transitions, n_book_moves, reprice_rate,
    track_rate, pricing_mode (static | repricing | thin). `as_of` (a UTC Timestamp)
    defaults to the latest pass; `window_h=None` uses all history up to `as_of`.
    Default window 7 days: 72 h left 109 of 190 gladiator bots thin vs 95 at 7 days
    and 92 for all history (2026-09-25, 11 days of data). `tr` = transitions(obs)
    computed once, for callers asking at many as-of times (same result: a transition
    counts when both its ends fall inside the window)."""
    as_of = obs.t.max() if as_of is None else pd.Timestamp(as_of)
    if tr is None:
        w = obs[obs.t <= as_of]
        if window_h is not None:
            w = w[w.t > as_of - pd.Timedelta(hours=window_h)]
        tr = transitions(w)
    else:
        keep = tr.t <= as_of
        if window_h is not None:
            keep &= tr.t_prev > as_of - pd.Timedelta(hours=window_h)
        tr = tr[keep]
    per = tr.groupby("steamId").agg(n_transitions=("reprice", "size"),
                                    n_book_moves=("book_move", "sum"),
                                    reprice_rate=("reprice", "mean"),
                                    n_track=("track", "sum"))
    per["track_rate"] = per.n_track / per.n_book_moves.where(per.n_book_moves > 0)
    per["pricing_mode"] = np.where(per.n_book_moves < min_moves, "thin",
                                   np.where(per.reprice_rate < static_below, "static", "repricing"))
    return per.drop(columns="n_track").reset_index()


# -- the group key ----------------------------------------------------------

def fleet_of(provider):
    """The operator fleet a provider names, or None when it is not one operator."""
    if provider in SERVICE_PROVIDERS or provider in NOT_ONE_OPERATOR or provider == "human":
        return None
    return provider


def _group_rows(reg, modes):
    g = reg[["steamId", "name", "provider"]].merge(modes, on="steamId", how="left")
    g["pricing_mode"] = g.pricing_mode.fillna("thin")
    g["fleet"] = g.provider.map(fleet_of)
    g["group"] = g.fleet.where(g.fleet.notna(), g.provider + ":" + g.pricing_mode)
    return g


def behaviour_groups(db=DEFAULT_DB, obs=None, as_of=None, window_h=168, **mode_kw):
    """One row per registered bot (human marks excluded): steamId, name, provider, fleet,
    pricing_mode, reprice_rate, n_book_moves, group (see module doc)."""
    reg = registry(db)
    reg = reg[reg.is_bot.astype(bool)]   # astype: an empty registry has an object column
    obs = load_bot_obs(db) if obs is None else obs
    return _group_rows(reg, pricing_modes(obs, as_of=as_of, window_h=window_h, **mode_kw))


def groups_asof(times, db=DEFAULT_DB, obs=None, window_h=168, **mode_kw):
    """behaviour_groups at each of `times` (UTC Timestamps), each from data up to that
    time only -- the point-in-time group for backtests. One row per (as_of, bot)."""
    reg = registry(db)
    reg = reg[reg.is_bot.astype(bool)]   # astype: an empty registry has an object column
    obs = load_bot_obs(db) if obs is None else obs
    tr = transitions(obs)
    out = [_group_rows(reg, pricing_modes(obs, as_of=ts, window_h=window_h, tr=tr, **mode_kw))
           .assign(as_of=pd.Timestamp(ts)) for ts in times]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["steamId", "name", "provider", "pricing_mode", "fleet", "group", "as_of"])


def fleet_check(obs, groups, last_passes=48):
    """For each provider: how often two of its bots, co-listed on the same book side in
    the same pass, quote the same price (within SAME_PRICE), over the last `last_passes`
    passes -- plus the cross-provider baseline. An operator fleet that prices as one sits
    far above the baseline; a provider that does not is near it."""
    recent = sorted(obs.pass_id.unique())[-last_passes:]
    o = obs[obs.pass_id.isin(recent)].merge(groups[["steamId", "provider"]], on="steamId")
    key = [*book_key(obs), "side", "pass_id"]
    o = o[key + ["steamId", "provider", "price"]]
    pairs = o.merge(o, on=key, suffixes=("_a", "_b"))
    pairs = pairs[pairs.steamId_a < pairs.steamId_b]
    pairs["same"] = ((pairs.price_a - pairs.price_b).abs()
                     <= SAME_PRICE * pairs[["price_a", "price_b"]].max(axis=1))
    within = pairs[pairs.provider_a == pairs.provider_b]
    out = (within.groupby("provider_a")
                 .agg(pairs=("same", "size"), same_price=("same", "mean"),
                      bots=("steamId_a", lambda s: pd.concat([s, within.loc[s.index, "steamId_b"]]).nunique()))
                 .rename_axis("provider").reset_index())
    cross = pairs[pairs.provider_a != pairs.provider_b]
    out.attrs["baseline"] = float(cross.same.mean()) if len(cross) else float("nan")
    out.attrs["baseline_pairs"] = int(len(cross))
    out["fleet"] = out.provider.map(fleet_of)
    return out.sort_values("pairs", ascending=False).reset_index(drop=True)

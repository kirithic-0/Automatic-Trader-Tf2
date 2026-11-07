"""Per-bot behaviour features -- the dynamic part of the bot layer (read side).

    obs  = listing_moves(load_bot_obs(db))    # bot_obs + what each listing did since the book's last pass
    acct = account_features(obs, db=db)       # per (steamId, pass_id): decayed rates, shrunk to the group
    book = book_bot_features(obs, acct)       # per (item, quality, pass_id): who bids / asks, aggregated
    book = load_book_bot_features(db)         # the same, cached in engine_cache/

**Per account** (every account seen, registered bot or not), time-decayed with a
`HALF_LIFE_H` half-life and shrunk toward its behaviour group, `rate = (n_event +
K_SHRINK * group_rate) / (n_base + K_SHRINK)`, so an account with little data inherits
its group (36% of bots are `thin`):

- `prem_b` / `prem_s`  how far it quotes from the side's median (buy: price/median - 1;
                        sell: 1 - price/median) -- the overbidder / underseller;
- `top_b` / `top_s`    share of its listings at rank 1;
- `cut1_b` / `cut1_s`  of its rank-1 listings, the share exactly one scrap (0.11 ref) ahead
                        of #2 -- the overcut / undercut step;
- `raise_b`, `lower_b`, `raise_s`, `lower_s`  of its transitions (it quoted the same book
                        side at the book's previous pass), the share moving up / down > 0.2%;
- `new_b`              share of its buy listings that are new entries on that book;
- `lead_b`             of its buy raises made while the book median had not moved yet, the
                        share followed by a median rise >= 0.5% within `LEAD_H` hours
                        (does the book follow it?);
- `nobs`               decayed listing count -- how much evidence the rates stand on.

**Point-in-time**: an account's features at a pass use that pass and earlier ones only;
a lead counts only once its `LEAD_H` follow-up window has closed; the group it is shrunk
toward is its behaviour group as of the last UTC midnight before the pass
(`bot_groups.groups_asof`). Unregistered accounts (never carried the bot badge) form the
group `nonbot`.

**Per book** (`book_bot_features`): for each side (`bot_b_*` buys, `bot_s_*` sells) the
counts that moved this pass (`n_raise`, `n_lower`, `n_new`), the mean / max of the
listed accounts' rates, the provider mix (`sh_glad`, `sh_tf2a`, `sh_fleet`, `sh_human`),
and the rank-1 account's own rates (`top_*`, plus whether it entered or raised this pass).
"""
import os

import numpy as np
import pandas as pd

from engine.book import READ_KEY
from engine.bot_groups import book_key, groups_asof, load_bot_obs
from engine.panel import DEFAULT_DB, cache_dir_for, write_cache

HALF_LIFE_H = 48.0      # decay half-life of every per-account rate
K_SHRINK = 20.0         # pseudo-observations of the group rate mixed into each account's
REPRICE = 0.002         # own price moved more than this -> a raise / lower
LEAD_MOVE = 0.005       # the book median moved more than this -> "the book followed"
LEAD_H = 6.0            # how long a raise waits for the book to follow
ONE_SCRAP = (0.105, 0.125)   # gap_ref band of a one-scrap cut (0.11, or 0.12 from rounding)

# (feature, numerator, denominator) -- the decayed sums each rate divides
RATES = [("prem_b", "prem_b", "n_b"), ("prem_s", "prem_s", "n_s"),
         ("top_b", "top_b", "n_b"), ("top_s", "top_s", "n_s"),
         ("cut1_b", "cut1_b", "top_b"), ("cut1_s", "cut1_s", "top_s"),
         ("raise_b", "raise_b", "trans_b"), ("lower_b", "lower_b", "trans_b"),
         ("raise_s", "raise_s", "trans_s"), ("lower_s", "lower_s", "trans_s"),
         ("new_b", "new_b", "n_b"), ("lead_b", "lead_f", "lead")]
ACCOUNT_FEATURES = [r[0] for r in RATES] + ["nobs"]

_SUMS = ["n", "prem", "top", "cut1", "trans", "raise", "lower", "new"]


# -- what each listing did since the book's previous pass ---------------------

def listing_moves(obs):
    """bot_obs plus per-listing flags: trans (the account quoted this book side at the
    book's previous pass), raise / lower (and moved > REPRICE), new (it did not), top,
    cut1 (rank 1, one scrap ahead), prem (distance from the side median), and lead /
    lead_f (a buy raise ahead of the median, and whether the median followed within
    LEAD_H) with t_lead = when that is known."""
    bk = book_key(obs)
    sp = obs[[*bk, "pass_id", "t"]].drop_duplicates().sort_values([*bk, "t"])
    sp["prev_pass"] = sp.groupby(bk).pass_id.shift()
    o = obs.merge(sp[[*bk, "pass_id", "prev_pass"]], on=[*bk, "pass_id"], how="left")
    k = ["steamId", *bk, "side"]
    o = o.sort_values(k + ["t"]).reset_index(drop=True)
    g = o.groupby(k, sort=False)
    a_prev_pass, a_prev_price = g.pass_id.shift(), g.price.shift()
    cont = a_prev_pass.notna() & (a_prev_pass == o.prev_pass)
    o["trans"] = cont.astype(float)
    o["raise"] = (cont & (o.price > a_prev_price * (1 + REPRICE))).astype(float)
    o["lower"] = (cont & (o.price < a_prev_price * (1 - REPRICE))).astype(float)
    o["new"] = (o.prev_pass.notna() & ~cont).astype(float)
    o["top"] = (o["rank"] == 1).astype(float)
    o["cut1"] = ((o["rank"] == 1) & o.gap_ref.between(*ONE_SCRAP)).astype(float)
    prem = np.where(o.side == "b", o.price / o["median"] - 1, 1 - o.price / o["median"])
    o["prem"] = np.clip(prem, -0.5, 0.5)

    # the side median's path: did it already move at this pass, does it rise within LEAD_H
    # scope "all" (an `extra` column): the admitted books first, so they keep the codes -- and
    # the float axis, whose rounding decides a book exactly LEAD_H later -- the agent's run gives
    # them (panel.load_panel has the measurement)
    ex = ["extra"] if "extra" in o else []
    med = (o[[*ex, *bk, "side", "pass_id", "t", "median"]]
           .drop_duplicates([*bk, "side", "pass_id"])
           .sort_values([*ex, *bk, "side", "t"]).reset_index(drop=True))
    code = med.groupby([*ex, *bk, "side"], sort=False).ngroup().to_numpy()
    ax = code * 1e7 + (med.t - med.t.min()).dt.total_seconds().to_numpy() / 3600
    mv = med["median"].to_numpy(float)
    idx = np.arange(len(med))
    hi = np.searchsorted(ax, ax + LEAD_H, side="right")
    fmax = np.full(len(med), np.nan)
    for j in range(1, int((hi - idx - 1).max(initial=0)) + 1):
        jj = idx + j
        ok = jj < hi
        fmax[ok] = np.fmax(fmax[ok], mv[jj[ok]])
    med["med_up"] = (fmax >= mv * (1 + LEAD_MOVE)).astype(float)
    med["med_prev"] = pd.Series(mv).groupby(code).shift().to_numpy()
    o = o.merge(med[[*bk, "side", "pass_id", "med_up", "med_prev"]],
                on=[*bk, "side", "pass_id"], how="left")
    already = o["median"] >= o.med_prev * (1 + LEAD_MOVE)
    o["lead"] = ((o["raise"] == 1) & (o.side == "b") & ~already).astype(float)
    o["lead_f"] = o.lead * o.med_up.fillna(0.0)
    o["t_lead"] = o.t + pd.Timedelta(hours=LEAD_H)
    return o.drop(columns=["med_up", "med_prev"])


# -- decayed sums ------------------------------------------------------------

def decayed_sums(events, queries, key, cols, half_life_h, t0):
    """For each query row (key, t): sum over the key's events with t_ev <= t of
    value * 0.5 ** ((t - t_ev) / half_life_h). `events` has key, t, cols; returns a
    frame aligned to `queries` (0 where the key has no event yet)."""
    ev = events.sort_values("t")
    tev = (ev.t - t0).dt.total_seconds().to_numpy() / 3600
    w = np.exp2(tev / half_life_h)
    cum = pd.DataFrame({key: ev[key].to_numpy(), "_h": tev})
    for c in cols:
        cum[c] = pd.Series(ev[c].to_numpy(float) * w).groupby(cum[key].to_numpy()).cumsum().to_numpy()
    tq = (queries.t - t0).dt.total_seconds().to_numpy() / 3600
    qq = pd.DataFrame({key: queries[key].to_numpy(), "_h": tq, "_i": np.arange(len(queries))})
    m = pd.merge_asof(qq.sort_values("_h"), cum, on="_h", by=key, direction="backward")
    dec = np.exp2(-m["_h"].to_numpy() / half_life_h)
    out = pd.DataFrame({c: m[c].to_numpy(float) * dec for c in cols})
    out["_i"] = m["_i"].to_numpy()
    out = out.sort_values("_i").drop(columns="_i").fillna(0.0)
    out.index = queries.index
    return out


def _group_at(pairs, groups):
    """The behaviour group of each (steamId, t) as of the last midnight strictly before t;
    accounts without a registered-bot row are 'nonbot'."""
    if groups is None or groups.empty:
        return pd.Series("nonbot", index=pairs.index)
    g = groups[["steamId", "as_of", "group"]].sort_values("as_of")
    q = pd.DataFrame({"steamId": pairs.steamId.to_numpy(), "as_of": pairs.t.to_numpy(),
                      "_i": np.arange(len(pairs))}).sort_values("as_of")
    q["as_of"] = pd.to_datetime(q.as_of, utc=True) - pd.Timedelta(microseconds=1)
    m = pd.merge_asof(q, g, on="as_of", by="steamId", direction="backward")
    return pd.Series(m.sort_values("_i").group.fillna("nonbot").to_numpy(), index=pairs.index)


def account_features(obs, groups=None, db=DEFAULT_DB, half_life_h=HALF_LIFE_H, k_shrink=K_SHRINK):
    """One row per (steamId, pass_id) an account is seen: `group` + ACCOUNT_FEATURES.
    `obs` is listing_moves() output; `groups` a groups_asof() frame (computed at every
    UTC midnight of the data when None)."""
    if groups is None:
        days = pd.date_range(obs.t.min().floor("D") + pd.Timedelta(days=1), obs.t.max().ceil("D"), freq="D")
        groups = groups_asof(days, db=db, obs=obs)
    per = []
    for side in ("b", "s"):
        a = (obs[obs.side == side].groupby(["steamId", "pass_id"])
             .agg(t=("t", "first"), n=("price", "size"), prem=("prem", "sum"), top=("top", "sum"),
                  cut1=("cut1", "sum"), trans=("trans", "sum"), raise_=("raise", "sum"),
                  lower=("lower", "sum"), new=("new", "sum")).reset_index())
        a.columns = ["steamId", "pass_id", "t"] + [f"{c}_{side}" for c in _SUMS]
        per.append(a)
    ev = per[0].merge(per[1], on=["steamId", "pass_id", "t"], how="outer").fillna(0.0)
    lead = (obs[obs.lead == 1].groupby(["steamId", "pass_id"])
            .agg(t=("t_lead", "first"), lead=("lead", "sum"), lead_f=("lead_f", "sum")).reset_index())
    t0 = obs.t.min()
    q = obs[["steamId", "pass_id", "t"]].drop_duplicates(["steamId", "pass_id"]).reset_index(drop=True)
    cols = [c for c in ev.columns if c not in ("steamId", "pass_id", "t")]
    A = pd.concat([q, decayed_sums(ev, q, "steamId", cols, half_life_h, t0),
                   decayed_sums(lead, q, "steamId", ["lead", "lead_f"], half_life_h, t0)], axis=1)

    # the group prior: the same decayed sums, keyed by each event's group at event time
    A["group"] = _group_at(A, groups)
    ev["group"] = _group_at(ev, groups)
    lead["group"] = _group_at(lead, groups)
    G = pd.concat([decayed_sums(ev, A, "group", cols, half_life_h, t0),
                   decayed_sums(lead, A, "group", ["lead", "lead_f"], half_life_h, t0)], axis=1)

    F = A[["steamId", "pass_id", "group"]].copy()
    for name, num, den in RATES:
        prior = (G[num] / G[den].where(G[den] > 0)).fillna(0.0).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):   # k=0 and no data: NaN
            F[name] = (A[num].to_numpy() + k_shrink * prior) / (A[den].to_numpy() + k_shrink)
    F["nobs"] = A.n_b + A.n_s
    return F


# -- per book ----------------------------------------------------------------

def _gtype(group):
    return np.select([group.str.startswith("gladiator"), group.str.startswith("tf2autobot"),
                      group.str.startswith("unknown"), group.str.startswith("other_bot"),
                      group == "nonbot"],
                     ["glad", "tf2a", "unknown", "other", "human"], "fleet")


def book_bot_features(obs, acct):
    """One row per book and pass -- (item, quality[, variant], pass_id): bot_b_* (buy side)
    and bot_s_* (sell side)."""
    bk = book_key(obs)
    ob = obs[[*bk, "side", "pass_id", "steamId", "rank", "raise", "lower", "new",
              "price"]].merge(acct, on=["steamId", "pass_id"], how="left")
    gt = _gtype(ob.group.fillna("nonbot"))
    for g in ("glad", "tf2a", "fleet", "human"):
        ob[f"is_{g}"] = (gt == g).astype(float)
    key = [*bk, "pass_id"]
    out = []
    for s in ("b", "s"):
        o = ob[ob.side == s]
        agg = o.groupby(key).agg(
            n_raise=("raise", "sum"), n_lower=("lower", "sum"), n_new=("new", "sum"),
            m_raise=(f"raise_{s}", "mean"), x_raise=(f"raise_{s}", "max"),
            m_lower=(f"lower_{s}", "mean"),
            m_cut1=(f"cut1_{s}", "mean"), x_cut1=(f"cut1_{s}", "max"),
            m_prem=(f"prem_{s}", "mean"), x_prem=(f"prem_{s}", "max"),
            m_lead=("lead_b", "mean"), x_lead=("lead_b", "max"),
            sh_glad=("is_glad", "mean"), sh_tf2a=("is_tf2a", "mean"),
            sh_fleet=("is_fleet", "mean"), sh_human=("is_human", "mean"))
        top = o[o["rank"] == 1].drop_duplicates(key).set_index(key)
        for name, col in (("top_raise", f"raise_{s}"), ("top_lower", f"lower_{s}"),
                          ("top_cut1", f"cut1_{s}"), ("top_prem", f"prem_{s}"),
                          ("top_lead", "lead_b"), ("top_new", "new"), ("top_raised", "raise"),
                          ("top_glad", "is_glad"), ("top_human", "is_human"), ("top_nobs", "nobs")):
            agg[name] = top[col]
        agg.columns = [f"bot_{s}_{c}" for c in agg.columns]
        out.append(agg)
    return out[0].join(out[1], how="outer").reset_index()


BOOK_BOT_FEATURES = [f"bot_{s}_{c}" for s in ("b", "s") for c in
                     ("n_raise", "n_lower", "n_new", "m_raise", "x_raise", "m_lower", "m_cut1",
                      "x_cut1", "m_prem", "x_prem", "m_lead", "x_lead", "sh_glad", "sh_tf2a",
                      "sh_fleet", "sh_human", "top_raise", "top_lower", "top_cut1", "top_prem",
                      "top_lead", "top_new", "top_raised", "top_glad", "top_human", "top_nobs")]


def load_book_bot_features(db=DEFAULT_DB, cache_dir=None, use_cache=True,
                           half_life_h=HALF_LIFE_H, k_shrink=K_SHRINK, obs=None, scope="admitted"):
    """book_bot_features over all of the db, cached in its cache folder and recomputed
    whole when new snapshots arrive (the decay makes every later value depend on them).
    The admitted books keep the agent's own features (the accounts' rates from the admitted
    books alone: measured 2026-11-01, every value equal); the other books' accounts are rated
    on every book they quote, as if all were admitted."""
    cache_dir = cache_dir or cache_dir_for(db)
    obs = load_bot_obs(db, cache_dir=cache_dir, use_cache=use_cache, scope=scope) if obs is None else obs
    mark = int(obs.snapshot_id.max()) if len(obs) else 0
    path = os.path.join(cache_dir, f"bot_book_v{READ_KEY}_h{half_life_h:g}_k{k_shrink:g}"
                                   f"{'_all' if scope == 'all' else ''}.parquet")
    if use_cache and os.path.exists(path):
        cached = pd.read_parquet(path)
        # (a cache from before the variant column: recompute, the next pass does anyway)
        if len(cached) and int(cached.watermark.iloc[0]) == mark and "variant" in cached:
            return cached.drop(columns="watermark")
    moves = listing_moves(obs)
    rates = lambda m: account_features(m, db=db, half_life_h=half_life_h, k_shrink=k_shrink)
    if "extra" in moves and moves.extra.any():
        own = moves[~moves.extra]
        book = pd.concat([book_bot_features(own, rates(own)),
                          book_bot_features(moves[moves.extra], rates(moves))], ignore_index=True)
    else:
        book = book_bot_features(moves, rates(moves))
    if use_cache:
        write_cache(book.assign(watermark=mark), path)
    return book

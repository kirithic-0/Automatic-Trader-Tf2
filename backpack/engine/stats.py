"""Per-pass statistics of the hourly scrape and of the model's features, cached, and the
drift read off them -- what `engine panel` (the live control panel) shows.

    st = load(db)                              # what is cached (None: nothing yet)
    st, panel = refresh_scrape(db)             # summarise new passes' books, save
    st = refresh_features(db, panel)           # ... the model's features (builds forecast_frame)
    table = drift_table(st, "scrape", now, ref)        # PSI per column, now vs a reference
    heat = daily_psi(st, "features")                   # PSI per column per day vs the 7 before

**Why a cache of answers, not of inputs** (measured 2026-10-30, 1.27M books, 445 passes):
the book reads load from the panel cache in ~8 s and the model's features (forecast_frame)
take ~72 s. A page cannot wait for that, so each pass is summarised once -- per column its
row count, missing count, mean, p10..p90 and its counts in fixed bins -- and only those
small rows are kept. A pass already summarised is never summarised again unless its row
count changed (it was still being written when it was read).

**Drift** is the PSI (population stability index) of a column's binned values, a "now"
window against a reference window: sum((p - q) * ln(p / q)) over the bins, missing values a
bin of their own (a column going missing is drift too). The bin edges are the column's
deciles over every row when the cache was first built, fixed for the life of the cache, so
any two windows compare by adding up their passes' counts. Read: < 0.1 stable, 0.1-0.25
moving, > 0.25 drifted (the usual convention, PSI_WATCH / PSI_DRIFT).

Two groups of columns:
- `scrape`   -- what the collector delivers: the BookRead fields of every Unique/Strange book
  (engine/book.py), its pricedb pairing, and the raw row's listing counts and key rate.
  Plus a per-pass table (`st.passes`): books, pass minutes, new / gone series, listings on
  the books, capped books, and the market's top-buy-order moves (the economy tab).
- `features` -- the model's inputs (forecast.FORECAST_FEATURES) on the rows it learns from
  (forecast.universe at TRAIN_MAX_SPREAD).

Files, in cache_dir_for(db) and disposable: `stats_<key>_cols.parquet`,
`stats_<key>_passes.parquet`, `stats_<key>_meta.json` (bin edges, watermarks, the live
model's training end). The key hashes STATS_VERSION, READ_KEY and the column lists: change
any and the cache rebuilds. The db is only read.
"""
import hashlib
import json
import os
import pickle
from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.book import READ_KEY
from engine.events import STEP_PCT, label_events
from engine.panel import (DEFAULT_DB, QUALITIES, SERIES, cache_dir_for, connect_ro, load_panel,
                          write_cache)

STATS_VERSION = 1
BINS = 10              # at most this many value bins a column (its deciles; fewer when tied)
QS = (0.1, 0.25, 0.5, 0.75, 0.9)
QCOLS = ["p10", "p25", "p50", "p75", "p90"]
HCOLS = [f"h{i}" for i in range(BINS)]
PSI_WATCH = 0.1        # PSI at or above: moving
PSI_DRIFT = 0.25       # PSI at or above: drifted
FLAT = 0.005           # a top-buy-order move under this (either way) counts as flat
IDX_CLIP = 0.25        # the buy-order index clips each item's log move to +-this per pass
CAP = 30               # the app's listing call returns at most this many a side (data-model.md)
REF_DAYS = 7           # the rolling reference: the 7 days before the window

SCRAPE_BOOK = ["bid1", "bid5", "ask1", "ask5", "bid_med", "ask_med", "bid1_fill", "ask1_bot",
               "n_buy", "n_sell", "n_buy_fenced", "n_sell_fenced", "gap2", "n_near_top",
               "bid_disp", "buy_bot_share", "buy_premium_share", "buy_instant_share", "top_bot",
               "sell_bot_share", "pdb_buy", "pdb_sell"]
SCRAPE_RAW = ["n_buy_total", "n_sell_total", "n_buy_page", "n_sell_page", "key_price_ref"]
SCRAPE_COLS = SCRAPE_BOOK + SCRAPE_RAW

SCRAPE_DOCS = {
    "bid1": "best fenced bid, keys",
    "bid5": "mean of the 5 best fenced bids, keys",
    "ask1": "best (lowest) fenced ask, keys",
    "ask5": "mean of the 5 lowest fenced asks, keys",
    "bid_med": "median of the fenced bids in the top 7: the buy-order level",
    "ask_med": "median of the fenced asks in the top 7",
    "bid1_fill": "best fenced bid from a trusted account: what a sale gets",
    "ask1_bot": "lowest fenced ask from a bp.tf bot: an instant buy",
    "n_buy": "valid buy orders (before the fence)",
    "n_sell": "valid sell orders (before the fence)",
    "n_buy_fenced": "buy orders the fence dropped",
    "n_sell_fenced": "sell orders the fence dropped",
    "gap2": "(best bid - 2nd best) / best bid",
    "n_near_top": "fenced bids within 1% of the best",
    "bid_disp": "spread of the top-5 fenced bids / their median",
    "buy_bot_share": "share of valid buys from bots",
    "buy_premium_share": "share of valid buys from premium accounts",
    "buy_instant_share": "share of valid buys marked instant",
    "top_bot": "1 if the best fenced bid is a bot",
    "sell_bot_share": "share of valid sells from bots",
    "pdb_buy": "pricedb suggested buy paired to the book, keys",
    "pdb_sell": "pricedb suggested sell paired to the book, keys",
    "n_buy_total": "buy listings the site reports for the item",
    "n_sell_total": "sell listings the site reports for the item",
    "n_buy_page": f"buy listings returned (at most {CAP})",
    "n_sell_page": f"sell listings returned (at most {CAP})",
    "key_price_ref": "the key rate the book was priced at, ref",
}


def feature_cols():
    """The model's inputs, imported lazily (forecast pulls in the bot layer)."""
    from engine.forecast import FORECAST_FEATURES
    return list(FORECAST_FEATURES)


def feature_groups():
    """feature -> the list it comes from (book, who, ..., churn), for the page's filter."""
    from engine import features as F
    from engine import forecast as FC
    from engine.bot_features import BOOK_BOT_FEATURES
    out = {}
    for name, cols in (("book", F.BOOK), ("who", F.WHO), ("anchor", F.ANCHOR),
                       ("momentum", F.MOMENTUM), ("market", F.MARKET), ("level", FC.LEVEL_FEATURES),
                       ("reach", FC.REACH_FEATURES), ("bot", BOOK_BOT_FEATURES),
                       ("churn", FC.CHURN_FEATURES)):
        for c in cols:
            out.setdefault(c, name)
    return out


_BASE = {"lvl": "the buy-order level", "b1": "the best bid", "ask1": "the best ask", "askmed": "the median ask",
         "pdb": "the pricedb buy price", "bid": "the best bid", "ask": "the best ask"}
_BOT_STAT = {"n": "how many listed accounts", "m": "mean over the listed accounts of their", "x": "max over the listed accounts of their",
             "sh": "share of listings from", "top": "the rank-1 account's"}
_BOT_RATE = {"raise": "raise rate", "lower": "lower rate", "new": "new-listing rate", "cut1": "one-scrap-cut rate",
             "prem": "premium over the median", "lead": "lead rate (raises the book follows)", "nobs": "evidence (decayed listings)",
             "glad": "gladiator.tf bots", "tf2a": "tf2autobot bots", "fleet": "operator fleets", "human": "non-bots",
             "raised": "raise this pass (1/0)"}


def _auto_doc(c):
    """A one-line meaning for a feature the engine has no doc for, from its name."""
    parts = c.split("_")
    if parts[0] == "bot" and len(parts) >= 4:
        side = "buys" if parts[1] == "b" else "sells"
        stat, rate = parts[2], "_".join(parts[3:])
        win = ""
        if rate.endswith(("_6h", "_24h")):
            rate, win = rate.rsplit("_", 1)
            win = f", summed over {win}"
        if stat == "n":
            did = "entered" if rate == "new" else rate + "d"
            return f"{side}: accounts that {did}, summed over the last {win.split()[-1]}" if win else f"{side}: accounts that {did} this pass"
        return f"{side}: {_BOT_STAT.get(stat, stat)} {_BOT_RATE.get(rate, rate)}{win}"
    if "_chg_" in c:
        base, h = c.split("_chg_")
        return f"change of {_BASE.get(base, base)} over {'one pass' if h == '1p' else h}"
    for k, word in (("_vs_max", "vs its high over "), ("_vs_min", "vs its low over ")):
        if k in c:
            base, h = c.split(k)
            return f"{_BASE.get(base, base)} {word}{h} h"
    return ""


# the level features engine.forecast documents only in its code (add_level_features / add_reach_features)
_MORE_DOCS = {
    "b5_over_med": "mean of the 5 best bids over the level",
    "ask1_is_bot": "1 if the best ask is a bot's",
    "lvl_nups_24h": "passes in the last 24 h where the level rose > 0.5%",
    "lvl_nups_72h": "passes in the last 72 h where the level rose > 0.5%",
    "lvl_nmoves_72h": "passes in the last 72 h where the level moved > 0.5%",
    "lvl_vol_24h": "std of per-pass log changes of the level, 24 h",
    "ask_nups_24h": "passes in the last 24 h where the best ask rose > 0.5%",
    "ask_nups_72h": "passes in the last 72 h where the best ask rose > 0.5%",
    "ask_ndowns_24h": "passes in the last 24 h where the best ask fell > 0.5%",
    "ask_ndowns_72h": "passes in the last 72 h where the best ask fell > 0.5%",
    "ask_vs_pdb_sell": "best ask / pricedb suggested sell",
    "is_strange": "1 for a Strange, 0 for a Unique",
    "n_fb": "bids that survive the fence",
    "n_fs": "asks that survive the fence",
    "b1max24_over_ask": "the best bid's high over the last 24 h vs today's ask",
    "b1max168_over_ask": "the best bid's high over the last 7 days vs today's ask",
}


def feature_docs():
    from engine.features import FEATURE_DOCS as A
    from engine.forecast import FEATURE_DOCS as B
    known = {**A, **B, **_MORE_DOCS}
    return {c: known.get(c) or _auto_doc(c) for c in feature_cols()}


def cache_key():
    raw = json.dumps([STATS_VERSION, READ_KEY, SCRAPE_COLS, feature_cols(), BINS], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


@dataclass
class Stats:
    cols: pd.DataFrame      # one row per (group, col, pass_id): n, nulls, mean, p10..p90, h0..h9
    passes: pd.DataFrame    # one row per pass: t, minutes, books, moves, listings, ...
    meta: dict              # key, edges {group: {col: [..]}}, watermark {group: id}, built, train_end


def _paths(db, cache_dir=None):
    base = os.path.join(cache_dir or cache_dir_for(db), f"stats_{cache_key()}")
    return base + "_cols.parquet", base + "_passes.parquet", base + "_meta.json"


def load(db=DEFAULT_DB, cache_dir=None):
    """The cached Stats, or None when there is none (or it will not read)."""
    pc, pp, pm = _paths(db, cache_dir)
    if not all(os.path.exists(p) for p in (pc, pp, pm)):
        return None
    try:
        with open(pm, encoding="utf-8") as f:
            meta = json.load(f)
        return Stats(pd.read_parquet(pc), pd.read_parquet(pp), meta)
    except (OSError, ValueError):
        return None


def _save(st, db, cache_dir=None):
    pc, pp, pm = _paths(db, cache_dir)
    write_cache(st.cols, pc)
    write_cache(st.passes, pp)
    tmp = f"{pm}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st.meta, f)
        os.replace(tmp, pm)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)


def watermark(db=DEFAULT_DB):
    """The newest snapshot id in the db: new books arrived when it moves."""
    con = connect_ro(db)
    try:
        return int(con.execute("SELECT coalesce(max(id), 0) FROM snapshots").fetchone()[0])
    finally:
        con.close()


def freshness(db=DEFAULT_DB):
    """When the newest pass book and the newest watch book were scraped (ISO, or None)."""
    con = connect_ro(db)
    out = {}
    try:
        for name, table in (("book", "snapshots"), ("watch", "watch_snapshots")):
            try:
                row = con.execute(f"SELECT scraped_at FROM {table} ORDER BY rowid DESC LIMIT 1").fetchone()
                out[name] = row[0] if row else None
            except Exception:
                out[name] = None
    finally:
        con.close()
    return out


# -- summarising -------------------------------------------------------------------

def edges_of(values):
    """A column's fixed bin edges: its deciles (ties merged), [] when it has no values."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if not len(v):
        return []
    return [float(x) for x in np.unique(np.quantile(v, np.linspace(0.1, 0.9, BINS - 1)))]


def summarise(df, cols, edges, group):
    """One row per (col, pass_id) of `df`: n, nulls, mean, p10..p90 and the counts in the
    column's bins (h0..h9; a value lands in bin = how many edges it is >= to)."""
    if df.empty:
        return pd.DataFrame(columns=["group", "col", "pass_id", "n", "nulls", "mean"] + QCOLS + HCOLS)
    pid = df.pass_id.to_numpy()
    passes, inv = np.unique(pid, return_inverse=True)
    n = np.bincount(inv, minlength=len(passes))
    out = []
    for c in cols:
        v = df[c].to_numpy(dtype=float) if c in df else np.full(len(df), np.nan)
        v = np.where(np.isfinite(v), v, np.nan)
        ok = ~np.isnan(v)
        s = pd.Series(v).groupby(inv)
        q = s.quantile(list(QS)).unstack().reindex(range(len(passes)))
        b = np.searchsorted(np.asarray(edges.get(c, []), float), v[ok], side="right")
        h = np.bincount(inv[ok] * BINS + b, minlength=len(passes) * BINS).reshape(len(passes), BINS)
        part = pd.DataFrame(h, columns=HCOLS)
        part.insert(0, "group", group)
        part.insert(1, "col", c)
        part.insert(2, "pass_id", passes.astype("int64"))
        part.insert(3, "n", n)
        part.insert(4, "nulls", n - np.bincount(inv[ok], minlength=len(passes)))
        part.insert(5, "mean", s.mean().reindex(range(len(passes))).to_numpy())
        for i, qc in enumerate(QCOLS):
            part.insert(6 + i, qc, q.iloc[:, i].to_numpy())
        out.append(part)
    return pd.concat(out, ignore_index=True)


def _todo(cached_cols, group, counts):
    """The passes to (re)summarise: not cached, or cached with another row count."""
    have = cached_cols[(cached_cols.group == group)].drop_duplicates("pass_id").set_index("pass_id").n
    now = counts.reindex(have.index)
    stale = set(have.index[now.to_numpy() != have.to_numpy()])
    return (set(counts.index) - set(have.index)) | stale


def pass_table(panel, raw, passes_meta):
    """One row per pass: collection health, listings and the market's top-buy-order moves.
    `panel` = label_events(drop_misreads(load_panel)) (r_bid1, step, new_high); `raw` = the
    raw rows' counts by snapshot_id; `passes_meta` = the passes table."""
    df = panel.merge(raw, on="snapshot_id", how="left")
    g = df.groupby("pass_id")
    r = df.r_bid1.astype(float)
    lg = np.log1p(r.where(r > -1)).clip(-IDX_CLIP, IDX_CLIP)
    moved = r.notna()
    out = pd.DataFrame({
        "t": g.t.first(),
        "books": g.size(),
        "moved": moved.groupby(df.pass_id).sum(),
        "idx_log": lg.groupby(df.pass_id).mean(),
        "chg_med": r.groupby(df.pass_id).median(),
        "up": (r >= FLAT).where(moved).groupby(df.pass_id).mean(),
        "down": (r <= -FLAT).where(moved).groupby(df.pass_id).mean(),
        "steps": df.step.groupby(df.pass_id).sum(),
        "new_highs": df.new_high.groupby(df.pass_id).sum(),
        "spread_med": ((df.ask1 - df.bid1) / df.bid1).groupby(df.pass_id).median(),
        "top_bot": df.top_bot.astype(float).groupby(df.pass_id).mean(),
        "buy_total": df.n_buy_total.groupby(df.pass_id).sum(min_count=1),
        "sell_total": df.n_sell_total.groupby(df.pass_id).sum(min_count=1),
        "capped_buy": (df.n_buy_page >= CAP).where(df.n_buy_page.notna()).groupby(df.pass_id).mean(),
        "capped_sell": (df.n_sell_page >= CAP).where(df.n_sell_page.notna()).groupby(df.pass_id).mean(),
        "key_ref": df.key_price_ref.groupby(df.pass_id).median(),
    }).sort_values("t")
    out["flat"] = 1 - out.up - out.down
    # series that appeared / went missing against the pass before
    keys = pd.Series(df.groupby(SERIES, sort=False, dropna=False).ngroup().to_numpy(),
                     index=df.index).groupby(df.pass_id).agg(set)
    keys = keys.reindex(out.index)
    prev = keys.shift(1)
    out["new"] = [len(k - p) if isinstance(p, set) else np.nan for k, p in zip(keys, prev)]
    out["gone"] = [len(p - k) if isinstance(p, set) else np.nan for k, p in zip(keys, prev)]
    pm = passes_meta.set_index("pass_id")
    st = pd.to_datetime(pm.started_at, utc=True, format="ISO8601", errors="coerce")
    en = pd.to_datetime(pm.ended_at, utc=True, format="ISO8601", errors="coerce")
    out["minutes"] = ((en - st).dt.total_seconds() / 60).reindex(out.index)
    out["idx"] = 100 * np.exp(out.idx_log.fillna(0).cumsum())
    return out.reset_index().rename(columns={"index": "pass_id"})


def _raw(db, ids):
    con = connect_ro(db)
    try:
        q = ",".join(str(x) for x in QUALITIES)
        raw = pd.read_sql(f"SELECT id AS snapshot_id, {', '.join(SCRAPE_RAW)} FROM snapshots "
                          f"WHERE quality IN ({q}) AND id >= ?", con, params=(int(min(ids)),))
        pm = pd.read_sql("SELECT id AS pass_id, started_at, ended_at FROM passes", con)
    finally:
        con.close()
    # the panel's own books only (its ids already passed the universe gate); filtering here
    # costs nothing, where the gate in SQL scans every row's kind columns (~7 s)
    return raw[raw.snapshot_id.isin(set(ids))], pm


def refresh_scrape(db=DEFAULT_DB, cache_dir=None, panel=None):
    """Summarise the scrape of every pass not summarised yet (or still being written), and
    rebuild the per-pass table. Returns (Stats, panel) -- the panel for refresh_features."""
    from engine.forecast import drop_misreads
    mark = watermark(db)
    panel = load_panel(db, cache_dir=cache_dir) if panel is None else panel
    st = load(db, cache_dir) or Stats(pd.DataFrame(), pd.DataFrame(), {})
    meta = st.meta or {"key": cache_key(), "edges": {}, "watermark": {}, "built": {}}
    raw, pm = _raw(db, panel.snapshot_id)
    df = panel.merge(raw, on="snapshot_id", how="left")
    if "scrape" not in meta["edges"]:
        meta["edges"]["scrape"] = {c: edges_of(df[c]) for c in SCRAPE_COLS}
    cached = st.cols if len(st.cols) else pd.DataFrame(columns=["group", "pass_id", "n"])
    todo = _todo(cached, "scrape", df.groupby("pass_id").size())
    new = summarise(df[df.pass_id.isin(todo)], SCRAPE_COLS, meta["edges"]["scrape"], "scrape")
    keep = cached[~((cached.group == "scrape") & cached.pass_id.isin(todo))] if len(cached) else cached
    cols = pd.concat([keep, new], ignore_index=True) if len(keep) else new
    labelled = label_events(drop_misreads(panel), pct=STEP_PCT, horizons=(24,))
    passes = pass_table(labelled, raw, pm)
    meta["watermark"]["scrape"] = mark
    meta["built"]["scrape"] = pd.Timestamp.now(tz="UTC").isoformat()
    st = Stats(cols, passes, meta)
    _save(st, db, cache_dir)
    return st, panel


def train_end(cache_dir):
    """The live model's last training label (its saved meta), or None."""
    try:
        from engine.forecast import MODEL_FILE
        with open(os.path.join(cache_dir, MODEL_FILE), "rb") as f:
            return pickle.load(f)["meta"].get("label_end")
    except Exception:
        return None


def refresh_features(db=DEFAULT_DB, panel=None, cache_dir=None):
    """Summarise the model's features on the rows it learns from, for every pass not
    summarised yet. Builds the forecast frame (~72 s on 1.27M books)."""
    from engine.forecast import TRAIN_MAX_SPREAD, forecast_frame, universe
    mark = watermark(db)
    cache_dir = cache_dir or cache_dir_for(db)
    st = load(db, cache_dir)
    if st is None:
        st, panel = refresh_scrape(db, cache_dir, panel)
    frame = forecast_frame(db, cache_dir=cache_dir, panel=panel)
    rows = frame[universe(frame, TRAIN_MAX_SPREAD)]
    del frame
    feats = feature_cols()
    meta = st.meta
    if "features" not in meta["edges"]:
        meta["edges"]["features"] = {c: edges_of(rows[c]) for c in feats}
    todo = _todo(st.cols, "features", rows.groupby("pass_id").size())
    new = summarise(rows[rows.pass_id.isin(todo)], feats, meta["edges"]["features"], "features")
    keep = st.cols[~((st.cols.group == "features") & st.cols.pass_id.isin(todo))]
    meta["watermark"]["features"] = mark
    meta["built"]["features"] = pd.Timestamp.now(tz="UTC").isoformat()
    meta["train_end"] = train_end(cache_dir)
    st = Stats(pd.concat([keep, new], ignore_index=True), st.passes, meta)
    _save(st, db, cache_dir)
    return st


# -- drift ---------------------------------------------------------------------------

def psi(p, q, eps=1e-4):
    """Population stability index of two count vectors (same bins)."""
    p = np.asarray(p, float)
    q = np.asarray(q, float)
    if p.sum() <= 0 or q.sum() <= 0:
        return float("nan")
    p = np.maximum(p / p.sum(), eps)
    q = np.maximum(q / q.sum(), eps)
    return float(np.sum((p - q) * np.log(p / q)))


def status(v):
    if v is None or not np.isfinite(v):
        return "none"
    return "drift" if v >= PSI_DRIFT else "watch" if v >= PSI_WATCH else "ok"


def _counts(rows):
    """Summed bin counts per col over `rows`, the missing ones as a last bin."""
    g = rows.groupby("col")
    c = g[HCOLS].sum()
    c["nulls"] = g.nulls.sum()
    return c


def drift_table(st, group, now, ref):
    """One dict per column of `group`: PSI of the passes in `now` (a (from, to) pair of
    UTC timestamps, to exclusive) against those in `ref`, plus the median of the per-pass
    medians and the missing share in each window. Worst first."""
    cols = st.cols[st.cols.group == group]
    if cols.empty:
        return []
    t = st.passes.set_index("pass_id").t
    when = cols.pass_id.map(t)
    sel = lambda w: cols[(when >= w[0]) & (when < w[1])]
    a, b = sel(now), sel(ref)
    ca, cb = _counts(a), _counts(b)
    med_a, med_b = a.groupby("col").p50.median(), b.groupby("col").p50.median()
    out = []
    for c in cols.col.unique():
        if c not in ca.index or c not in cb.index:
            v = float("nan")
        else:
            v = psi(ca.loc[c].to_numpy(), cb.loc[c].to_numpy())
        na, nb = a[a.col == c], b[b.col == c]
        out.append({"col": c, "psi": v, "status": status(v),
                    "p50_now": _f(med_a.get(c)), "p50_ref": _f(med_b.get(c)),
                    "miss_now": _f(na.nulls.sum() / na.n.sum()) if na.n.sum() else None,
                    "miss_ref": _f(nb.nulls.sum() / nb.n.sum()) if nb.n.sum() else None,
                    "n_now": int(na.n.sum()), "n_ref": int(nb.n.sum())})
    return sorted(out, key=lambda r: -(r["psi"] if np.isfinite(r["psi"]) else -1))


def daily_psi(st, group, ref_days=REF_DAYS):
    """PSI per column per UTC day against the `ref_days` days before it: {days, cols, z}
    (z[col][day], NaN where the day or its reference has no rows)."""
    cols = st.cols[st.cols.group == group]
    if cols.empty:
        return {"days": [], "cols": [], "z": []}
    day = cols.pass_id.map(st.passes.set_index("pass_id").t).dt.floor("D")
    k = cols.assign(day=day).groupby(["col", "day"])[HCOLS + ["nulls"]].sum()
    days = sorted(day.dropna().unique())
    names = list(cols.col.unique())
    z = np.full((len(names), len(days)), np.nan)
    for i, c in enumerate(names):
        m = k.loc[c].reindex(days).fillna(0).to_numpy()
        for j in range(len(days)):
            ref = m[max(0, j - ref_days):j].sum(axis=0)
            if j and ref.sum() > 0 and m[j].sum() > 0:
                z[i, j] = psi(m[j], ref)
    return {"days": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in days], "cols": names, "z": z}


def series(st, group, col):
    """One column's per-pass band: pass_id, t, p10..p90, mean, missing share."""
    r = st.cols[(st.cols.group == group) & (st.cols.col == col)]
    r = r.merge(st.passes[["pass_id", "t"]], on="pass_id").sort_values("t")
    return r.assign(miss=r.nulls / r.n.where(r.n > 0))[["pass_id", "t", "mean", "miss"] + QCOLS]


def _f(x):
    return None if x is None or not np.isfinite(x) else float(x)

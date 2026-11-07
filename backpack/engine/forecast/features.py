"""What the model sees: the level, reach and bot features. Each is point-in-time, reading only rows at or
before t. Rows are sorted by series then t, as load_panel returns them."""
import numpy as np
import pandas as pd

from engine.bot_features import BOOK_BOT_FEATURES, load_book_bot_features
from engine.events import series_axis
from engine.features import FEATURES
from engine.forecast.config import EXIT_ON, HOLD_GAP_H, LEVEL, TAKE_PROFIT
from engine.panel import DEFAULT_DB, SERIES

LEVEL_FEATURES = [
    "log_lvl", "spr_med", "b1_over_med", "b5_over_med", "askmed_over_ask1", "askbot_over_ask1",
    "ask1_is_bot", "lvl_chg_1p", "lvl_chg_6h", "lvl_chg_24h", "lvl_chg_72h", "ask1_chg_1p",
    "ask1_chg_72h", "askmed_chg_1p", "askmed_chg_6h", "askmed_chg_24h", "askmed_chg_72h",
    "b1_chg_72h", "lvl_vs_max24", "lvl_vs_min24", "lvl_vs_max72", "lvl_vs_min72",
    "ask_vs_max24", "ask_vs_min24", "ask_vs_max72", "ask_vs_min72", "lvl_nmoves_24h",
    "lvl_nups_24h", "lvl_nmoves_72h", "lvl_nups_72h", "lvl_vol_24h", "lvl_vol_72h",
    "ask_nups_24h", "ask_ndowns_24h", "ask_nups_72h", "ask_ndowns_72h", "lvl_vs_pdb",
    "ask_vs_pdb_sell", "pdb_chg_24h", "is_strange", "mkt_lvl_up_share", "n_fb", "n_fs"]
# how high the best bid has been against today's ask -- has the take-profit been there
# lately? (added 2026-09-26: +0.13-0.38% per trade at matched volume, data-model.md)
REACH_FEATURES = ["b1max24_over_ask", "b1max72_over_ask", "b1max168_over_ask",
                  "medmax168_over_ask", "b1_tp_share72", "h_since_b1_tp"]
CHURN_FEATURES = [f"bot_{s}_n_{c}_{h}h" for s in ("b", "s") for c in ("raise", "lower", "new")
                  for h in (6, 24)]
FORECAST_FEATURES = FEATURES + LEVEL_FEATURES + REACH_FEATURES + BOOK_BOT_FEATURES + CHURN_FEATURES

FEATURE_DOCS = {
    "log_lvl": "log of the buy-order level (median fenced bid, keys)",
    "spr_med": "(ask1 - level) / level: the ask over the buy-order median",
    "b1_over_med": "best bid over the level: how far the top bidder is ahead",
    "askmed_over_ask1": "median ask over the best ask: one cheap seller under the rest?",
    "askbot_over_ask1": "cheapest bot ask over the best ask (0 = the best ask is a bot)",
    "lvl_vs_max72": "level vs its prior-72 h high (<0 = below a recent high)",
    "ask_vs_max24": "ask vs its prior-24 h high (<0 = the ask just dropped)",
    "lvl_nmoves_24h": "passes in the last 24 h where the level moved > 0.5%",
    "lvl_vol_72h": "std of per-pass log changes of the level, 72 h",
    "lvl_vs_pdb": "level / pricedb suggested buy",
    "mkt_lvl_up_share": "share of all items whose level rose this pass",
    "b1max72_over_ask": "the best bid's high over the last 72 h vs today's ask",
    "medmax168_over_ask": "the level's high over the last 7 days vs today's ask",
    "b1_tp_share72": "share of the last 72 h's passes whose best bid was >= today's ask + take-profit",
    "h_since_b1_tp": "hours since the best bid last was >= today's ask + take-profit (72 h window)",
}


def _lag_change(cur, code, axis, hours):
    """cur / (the series' latest value at or before t-hours) - 1, ignoring a lag value
    older than t - 2*hours (a scrape gap)."""
    ok = ~np.isnan(cur)
    if not ok.any():
        return np.full(len(cur), np.nan)
    vals, ax, cd = cur[ok], axis[ok], code[ok]
    j = np.searchsorted(ax, axis - hours, side="right") - 1
    jc = np.clip(j, 0, len(ax) - 1)
    good = (j >= 0) & (cd[jc] == code) & (ax[jc] >= axis - 2 * hours)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = cur / np.where(good, vals[jc], np.nan) - 1
    return np.where(np.isfinite(out), out, np.nan)


def _prev_change(v, code, axis, max_gap=HOLD_GAP_H):
    """v / (the series' previous row's v) - 1, when that row is <= max_gap h earlier."""
    ok = np.append(False, (axis[1:] - axis[:-1] <= max_gap) & (code[1:] == code[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        out = v / np.append(np.nan, v[:-1]) - 1
    return np.where(ok & np.isfinite(out), out, np.nan)


def _window_reduce(v, axis, hours, fn, empty):
    """fn-reduce v over the series' rows in [t-hours, t] (inclusive of t)."""
    x = np.where(np.isnan(v), empty, v)
    idx = np.arange(len(v))
    lo = np.searchsorted(axis, axis - hours, side="left")
    out = x.copy()
    for k in range(1, int((idx - lo).max(initial=0)) + 1):
        j = idx - k
        ok = j >= lo
        out[ok] = fn(out[ok], x[j[ok]])
    return np.where(out == empty, np.nan, out)


def _window_sum(v, axis, hours):
    """sum of v over the series' rows in [t-hours, t] (inclusive of t), NaN as 0."""
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(v))])
    lo = np.searchsorted(axis, axis - hours, side="left")
    return c[np.arange(len(v)) + 1] - c[lo]


def add_level_features(frame, level=LEVEL):
    """LEVEL_FEATURES: the buy-order level and the ask, their momentum, range position,
    volatility, pricedb anchor and the market-wide share of rising levels. Point-in-time."""
    df = frame.copy()
    code, axis = series_axis(df)
    L = df[level].to_numpy(float)
    ask = df.ask1.to_numpy(float)
    askmed = df.ask_med.to_numpy(float)
    b1 = df.bid1.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["log_lvl"] = np.log(L)
        df["spr_med"] = ask / L - 1
        df["b1_over_med"] = b1 / L - 1
        df["b5_over_med"] = df.bid5.to_numpy(float) / L - 1
        df["askmed_over_ask1"] = askmed / ask - 1
        df["askbot_over_ask1"] = df.ask1_bot.to_numpy(float) / ask - 1
    df["ask1_is_bot"] = np.where(np.isnan(ask), np.nan,
                                 np.isclose(df.ask1_bot.to_numpy(float), ask).astype(float))
    for name, v in (("lvl", L), ("ask1", ask), ("askmed", askmed), ("b1", b1)):
        df[f"{name}_chg_1p"] = _prev_change(v, code, axis)
        for h in (6, 24, 72):
            df[f"{name}_chg_{h}h"] = _lag_change(v, code, axis, h)
    for h in (24, 72):
        df[f"lvl_vs_max{h}"] = L / _window_reduce(L, axis, h, np.fmax, -np.inf) - 1
        df[f"lvl_vs_min{h}"] = L / _window_reduce(L, axis, h, np.fmin, np.inf) - 1
        df[f"ask_vs_max{h}"] = ask / _window_reduce(ask, axis, h, np.fmax, -np.inf) - 1
        df[f"ask_vs_min{h}"] = ask / _window_reduce(ask, axis, h, np.fmin, np.inf) - 1
    prev_ok = np.append(False, (axis[1:] - axis[:-1] <= HOLD_GAP_H) & (code[1:] == code[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        dl = np.where(prev_ok, np.log(L / np.append(np.nan, L[:-1])), np.nan)
        da = np.where(prev_ok, np.log(ask / np.append(np.nan, ask[:-1])), np.nan)
    for h in (24, 72):
        df[f"lvl_nmoves_{h}h"] = _window_sum((np.abs(dl) > 0.005).astype(float), axis, h)
        df[f"lvl_nups_{h}h"] = _window_sum((dl > 0.005).astype(float), axis, h)
        n = np.maximum(_window_sum((~np.isnan(dl)).astype(float), axis, h), 1)
        s1, s2 = _window_sum(dl, axis, h), _window_sum(dl ** 2, axis, h)
        df[f"lvl_vol_{h}h"] = np.sqrt(np.maximum(s2 / n - (s1 / n) ** 2, 0))
        df[f"ask_nups_{h}h"] = _window_sum((da > 0.005).astype(float), axis, h)
        df[f"ask_ndowns_{h}h"] = _window_sum((da < -0.005).astype(float), axis, h)
    df["lvl_vs_pdb"] = (df[level] / df.pdb_buy).replace([np.inf, -np.inf], np.nan)
    df["ask_vs_pdb_sell"] = (df.ask1 / df.pdb_sell).replace([np.inf, -np.inf], np.nan)
    df["pdb_chg_24h"] = _lag_change(df.pdb_buy.to_numpy(float), code, axis, 24)
    df["is_strange"] = (df.quality == 11).astype(float)
    up = pd.Series(np.where(np.isnan(dl), np.nan, (dl > 0.005).astype(float)), index=df.index)
    if "extra" in df:               # scope "all": the market is the admitted goods, as the agent sees it
        up = up.where(~df.extra)
    df["mkt_lvl_up_share"] = up.groupby(df.pass_id).transform("mean")
    # fenced depth, for the model: how many bids / asks survive the fence
    df["n_fb"] = df.n_buy - df.n_buy_fenced
    df["n_fs"] = df.n_sell - df.n_sell_fenced
    return df


def add_reach_features(frame, take_profit=TAKE_PROFIT, level=LEVEL, exit_on=EXIT_ON):
    """REACH_FEATURES: how high the best bid that fills (exit_on) and the level have been
    against today's ask -- its 24 / 72 / 168 h highs, the share of the last 72 h's passes
    whose best bid was already at today's ask + take_profit, and the hours since it last
    was. Point-in-time."""
    df = frame.copy()
    code, axis = series_axis(df)
    b1, ask = df[exit_on].to_numpy(float), df.ask1.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        for h in (24, 72, 168):
            df[f"b1max{h}_over_ask"] = _window_reduce(b1, axis, h, np.fmax, -np.inf) / ask - 1
        df["medmax168_over_ask"] = (_window_reduce(df[level].to_numpy(float), axis, 168, np.fmax, -np.inf)
                                    / ask - 1)
    thr = ask * (1 + take_profit)
    idx = np.arange(len(b1))
    lo = np.searchsorted(axis, axis - 72, side="left")
    hits, seen, since = np.zeros(len(b1)), np.zeros(len(b1)), np.full(len(b1), np.nan)
    for k in range(1, int((idx - lo).max(initial=0)) + 1):     # earlier passes, newest first
        j = idx - k
        ok = j >= lo
        jj = j[ok]
        hit = b1[jj] >= thr[ok]
        hits[ok] += hit
        seen[ok] += ~np.isnan(b1[jj])
        sel = np.flatnonzero(ok)[hit]
        new = sel[np.isnan(since[sel])]
        since[new] = axis[new] - axis[new - k]
    df["b1_tp_share72"] = np.where(seen > 0, hits / np.maximum(seen, 1), np.nan)
    df["h_since_b1_tp"] = since
    return df


def add_bot_features(frame, db=DEFAULT_DB, cache_dir=None, use_cache=True, scope="admitted"):
    """Merge the per-book bot aggregates (engine.bot_features) and their 6 h / 24 h churn
    sums (how many bidders / sellers raised, lowered, arrived lately)."""
    book = load_book_bot_features(db, cache_dir=cache_dir, use_cache=use_cache, scope=scope)
    df = frame.merge(book, on=SERIES + ["pass_id"], how="left")
    code, axis = series_axis(df)
    for s in ("b", "s"):
        for c in ("raise", "lower", "new"):
            v = df[f"bot_{s}_n_{c}"].to_numpy(float)
            for h in (6, 24):
                df[f"bot_{s}_n_{c}_{h}h"] = _window_sum(v, axis, h)
    return df

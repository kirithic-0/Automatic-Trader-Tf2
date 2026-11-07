"""Per-snapshot features for the step-up question, using only data up to that pass.

    frame = build_features(label_events(load_panel()))

Adds every column in FEATURES (grouped below). Nothing looks ahead: momentum
reads earlier passes of the same series, and a step at an earlier row is known
by the current row (its hold check is the pass after it, at the latest this one).
Only `r_bid1 >= pct` (not the confirmed step) feeds the market-wide share, since
the current pass's own steps are not confirmed yet.
"""
import numpy as np
import pandas as pd

from engine.events import STEP_PCT, series_axis

BOOK = ["bid1", "n_buy", "n_sell", "spread", "gap2", "n_near_top", "bid_disp",
        "n_buy_fenced", "n_sell_fenced"]
WHO = ["buy_bot_share", "buy_premium_share", "buy_instant_share", "top_bot",
       "sell_bot_share"]
ANCHOR = ["bid_vs_pdb", "ask_vs_pdb"]
MOMENTUM = ["bid_chg_1p", "bid_chg_6h", "bid_chg_24h", "ask_chg_6h", "ask_chg_24h",
            "bid_vs_24h_high", "hours_since_step", "steps_72h"]
MARKET = ["market_up_share"]
FEATURES = BOOK + WHO + ANCHOR + MOMENTUM + MARKET

# one line per feature, for the screens and the dashboard
FEATURE_DOCS = {
    "bid1": "best fenced bid, keys (price level)",
    "n_buy": "valid buy orders (after flag filter + dedupe, before the fence)",
    "n_sell": "valid sell orders",
    "spread": "(best ask - best bid) / best bid",
    "gap2": "(best bid - 2nd best bid) / best bid: how exposed the top bid is",
    "n_near_top": "fenced bids within 1% of the best bid: a crowded top",
    "bid_disp": "(max-min)/median of the top-5 fenced bids: do they agree",
    "n_buy_fenced": "buy orders the fence dropped (contamination)",
    "n_sell_fenced": "sell orders the fence dropped",
    "buy_bot_share": "share of valid buys from bp.tf-badged bots",
    "buy_premium_share": "share of valid buys from premium accounts",
    "buy_instant_share": "share of valid buys marked instant-deal",
    "top_bot": "1 if the best fenced bid is a bot, else 0",
    "sell_bot_share": "share of valid sells from bots",
    "bid_vs_pdb": "best bid / pricedb suggested buy",
    "ask_vs_pdb": "best ask / pricedb suggested sell",
    "bid_chg_1p": "best-bid change since the previous pass",
    "bid_chg_6h": "best-bid change over ~6 h",
    "bid_chg_24h": "best-bid change over ~24 h",
    "ask_chg_6h": "best-ask change over ~6 h",
    "ask_chg_24h": "best-ask change over ~24 h",
    "bid_vs_24h_high": "best bid / highest best bid of the prior 24 h (<1 = below recent high)",
    "hours_since_step": "hours since this item's previous step (any kind)",
    "steps_72h": "steps this item made in the prior 72 h",
    "market_up_share": "share of all items whose bid rose >= the step bar this pass",
}


def _lag_change(df, code, axis, col, hours):
    """Relative change of `col` vs the series' latest value at or before t-hours,
    ignoring a lag observation older than t - 2*hours (a scrape gap)."""
    cur = df[col].to_numpy(dtype=float)
    ok = ~np.isnan(cur)
    if not ok.any():
        return np.full(len(df), np.nan)
    vals, ax, cd = cur[ok], axis[ok], code[ok]
    j = np.searchsorted(ax, axis - hours, side="right") - 1
    jc = np.clip(j, 0, len(ax) - 1)
    good = (j >= 0) & (cd[jc] == code) & (ax[jc] >= axis - 2 * hours)
    base = np.where(good, vals[jc], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = cur / base - 1
    return np.where(np.isfinite(out), out, np.nan)


def build_features(frame, pct=STEP_PCT):
    """Return a copy of a label_events() frame with FEATURES added."""
    df = frame.copy()
    code, axis = series_axis(df)

    df["spread"] = (df.ask1 - df.bid1) / df.bid1
    df["bid_vs_pdb"] = df.bid1 / df.pdb_buy
    df["ask_vs_pdb"] = df.ask1 / df.pdb_sell
    for col in ("bid_vs_pdb", "ask_vs_pdb"):
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    df["bid_chg_1p"] = df.r_bid1
    df["bid_vs_24h_high"] = df.bid1 / df.bid1_max24
    for h in (6, 24):
        df[f"bid_chg_{h}h"] = _lag_change(df, code, axis, "bid1", h)
        df[f"ask_chg_{h}h"] = _lag_change(df, code, axis, "ask1", h)

    # hours since this series' previous step (strictly earlier row)
    step = df.step.to_numpy()
    step_t = df.t.where(df.step)
    prev = step_t.groupby(code).shift(1).groupby(code).ffill()
    df["hours_since_step"] = (df.t - prev).dt.total_seconds() / 3600.0

    # steps in [t-72h, t), strictly earlier rows of the same series
    c = np.concatenate([[0], np.cumsum(step)])
    lo = np.searchsorted(axis, axis - 72, side="left")
    df["steps_72h"] = (c[np.arange(len(df))] - c[lo]).astype(float)

    up = (df.r_bid1 >= pct).where(df.r_bid1.notna())
    if "extra" in df:               # scope "all": the market is the admitted goods, as the agent sees it
        up = up.where(~df.extra)
    df["market_up_share"] = up.groupby(df.pass_id).transform("mean")
    return df

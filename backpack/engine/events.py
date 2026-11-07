"""Bid step-up events and the forward labels hung off them.

A **step** (the bid-ratchet event, docs/data-model.md) is a snapshot where the
series' best fenced bid (bid1):
  * rose by >= `pct` over the series' previous pass with a bid, that pass being
    <= `max_gap_h` hours earlier (a long gap is not "the previous pass"), and
  * still holds on the next pass: next bid1 >= `hold` * this bid1, that pass
    also <= `max_gap_h` hours later. A step on a series' last pass is not yet
    confirmable, so it is False.

The 1-scrap undercut race (the median tick, +0.22%) is noise the pct bar filters
out; measured 2026-09-23 a 2% bar marks ~1.7% of passes.

A **new high** is a step whose bid1 also exceeds every bid1 the series showed in
the prior 24 h (strictly earlier passes). Measured 2026-09-24, 63% of steps are
recoveries back to a recent level (a flickering book), so the new-high subset is
the ratchet proper. `bid1_max24` (that prior-24h max) is kept for features.

The label `ev_<H>h` for a snapshot at t: 1.0 if the same series steps at some
t' with t < t' <= t + H, else 0.0 -- and NaN when the series has no observation
at or after t + H (the window is not fully observed yet, or the item stopped
being scraped). `nh_<H>h` is the same for new highs. Censored rows must stay
NaN: scoring them as "no event" biases every lift down.

**Collection gaps (2026-10-17).** When no pass ran for more than SEGMENT_GAP_H hours (the
collector was down -- trading_app.db has a 6-day gap before pass 133, when the Azure VM
started), a window that reaches into the gap is censored too: `observed_until` caps each
row's "last seen" at the end of its run of passes, so the gap never reads as "no event".
"""
import numpy as np
import pandas as pd

from engine.panel import SERIES

STEP_PCT = 0.02
HOLD = 0.99
MAX_GAP_H = 3.0
HORIZONS = (6, 24)
SEGMENT_GAP_H = 6.0   # no pass for longer than this = the collector was down (passes are hourly)

_SPAN = 1e7   # hours; separates series on one global time axis (see series_axis)


def label_col(h, new_high=False):
    return f"{'nh' if new_high else 'ev'}_{h:g}h"


def event_col(new_high=False):
    return "new_high" if new_high else "step"


def series_axis(panel):
    """A strictly increasing float per row -- series code * _SPAN + hours --
    so one global searchsorted answers per-series time-window queries.
    Requires the panel sorted by series then t (load_panel's order)."""
    code = panel.groupby(SERIES, sort=False, dropna=False).ngroup().to_numpy()
    hours = (panel.t - panel.t.min()).dt.total_seconds().to_numpy() / 3600.0
    return code, code * _SPAN + hours


def observed_until(df, gap_h=SEGMENT_GAP_H):
    """Per row: how far forward the row's series is observed -- its last t, capped at the
    last pass of the row's run (runs of passes break where none ran for > gap_h hours,
    over every pass in the frame). A label window past this is censored."""
    last_t = df.groupby(series_axis(df)[0], sort=False).t.transform("max")
    if not len(df):
        return last_t
    ut = pd.DatetimeIndex(df.t.unique()).sort_values()
    uh = (ut - ut[0]).total_seconds().to_numpy() / 3600.0
    end_at = np.append(np.flatnonzero(np.diff(uh) > gap_h), len(ut) - 1)   # each run's last pass
    run = np.searchsorted(end_at, ut.get_indexer(df.t), side="left")
    run_end = pd.Series(ut[end_at[run]], index=df.index)
    return last_t.where(last_t <= run_end, run_end)


def _prior_max(vals, axis, hours):
    """Per row, the max of `vals` over the same series' rows in [t-hours, t),
    NaN-skipping; NaN when there are none. Windows hold ~1 row per pass, so a
    loop over window offsets (not rows) stays vectorised."""
    idx = np.arange(len(vals))
    lo = np.searchsorted(axis, axis - hours, side="left")   # never crosses a series
    out = np.full(len(vals), np.nan)
    for k in range(1, int((idx - lo).max(initial=0)) + 1):
        j = idx - k
        ok = j >= lo
        out[ok] = np.fmax(out[ok], vals[j[ok]])
    return out


def label_events(panel, pct=STEP_PCT, hold=HOLD, max_gap_h=MAX_GAP_H,
                 horizons=HORIZONS):
    """Return a copy of `panel` with r_bid1, bid1_max24, step, new_high, and an
    ev_<H>h + nh_<H>h label pair per horizon."""
    df = panel.copy()
    code, axis = series_axis(df)
    df["_code"] = code

    has = df[df.bid1.notna()]
    g = has.groupby("_code", sort=False)
    prev_bid, prev_t = g.bid1.shift(1), g.t.shift(1)
    next_bid, next_t = g.bid1.shift(-1), g.t.shift(-1)
    gap = pd.Timedelta(hours=max_gap_h)
    r = (has.bid1 / prev_bid - 1).where(has.t - prev_t <= gap)
    step = (r >= pct) & (next_t - has.t <= gap) & (next_bid >= hold * has.bid1)

    df["r_bid1"] = r.reindex(df.index)
    df["bid1_max24"] = _prior_max(df.bid1.to_numpy(dtype=float), axis, 24.0)
    df["step"] = step.reindex(df.index, fill_value=False).astype(bool)
    df["new_high"] = df.step & (df.bid1 > df.bid1_max24)

    # events strictly after row i and within +H, via cumulative counts on the axis
    idx = np.arange(len(df))
    last_t = observed_until(df)
    censored = {h: (last_t < df.t + pd.Timedelta(hours=h)).to_numpy() for h in horizons}
    for nh in (False, True):
        c = np.concatenate([[0], np.cumsum(df[event_col(nh)].to_numpy())])
        for h in horizons:
            hi = np.searchsorted(axis, axis + h, side="right")      # first row past t+H
            lab = ((c[hi] - c[idx + 1]) > 0).astype(float)
            lab[censored[h]] = np.nan
            df[label_col(h, nh)] = lab
    return df.drop(columns="_code")

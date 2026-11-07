"""What the model learns: up_<H>h and ret_<H>h, read forward from each row (see the package doc)."""
import numpy as np
import pandas as pd

from engine.events import observed_until, series_axis
from engine.forecast.config import (EXIT_ON, EXIT_WAIT_H, HOLD_GAP_H, HORIZON, LEVEL, MAX_JUMP, TAKE_PROFIT,
                                    TARGET)


def up_col(h):
    return f"up_{h:g}h"


def ret_col(h):
    return f"ret_{h:g}h"


def exit_col(h):
    return f"exit_h_{h:g}h"


def close_col(h):
    return f"close_h_{h:g}h"


def _held(v, axis, max_gap=HOLD_GAP_H):
    """min(v at a row, v at the series' next row) when that row is <= max_gap h later."""
    nxt = np.append(v[1:], np.nan)
    nax = np.append(axis[1:], np.inf)
    ok = (nax > axis) & (nax - axis <= max_gap)
    return np.where(ok, np.fmin(v, nxt), np.nan)


def _first_reach(v, axis, h, lo, hi=None):
    """Per row: (hours from t to the first later row within (t, t+h] of the same series
    whose lo <= v <= hi, that row's index) -- (NaN, -1) when there is none."""
    idx = np.arange(len(v))
    end = np.searchsorted(axis, axis + h, side="right")
    hi = np.full(len(v), np.inf) if hi is None else hi
    out, at = np.full(len(v), np.nan), np.full(len(v), -1)
    for k in range(1, int((end - idx - 1).max(initial=0)) + 1):
        j = idx + k
        ok = (j < end) & np.isnan(out)
        jj = j[ok]
        hit = (v[jj] >= lo[ok]) & (v[jj] <= hi[ok])
        sel = np.flatnonzero(ok)[hit]
        out[sel] = axis[j[sel]] - axis[sel]
        at[sel] = j[sel]
    return out, at


def _end_exit(v, cap, axis, h, wait=None):
    """The horizon exit, per row: sell into v (a bid <= cap) at the series' last row <= t+h
    when that row is past t + 3h/4 and has one; else into the first row in (t+h, t+h+wait]
    that has one (the position is held until a buyer appears); else it is marked at the
    last bid seen in (t, t+h] (no buyer came), at t+h+wait. Returns (value, hours to the
    exit or mark, found) -- found False where no buyer is seen yet (value NaN)."""
    wait = EXIT_WAIT_H if wait is None else wait
    n = len(v)
    idx = np.arange(n)
    val, hrs = np.full(n, np.nan), np.full(n, np.nan)

    def take(rows, j, hours):              # rows whose v[j] is a bid within the cap
        hit = ~np.isnan(v[j]) & (v[j] <= cap[rows])
        sel = rows[hit]
        val[sel], hrs[sel] = v[j[hit]], hours(sel, j[hit])
        return sel

    at_h = lambda sel, j: axis[j] - axis[sel]
    j_end = np.searchsorted(axis, axis + h, side="right") - 1      # >= idx
    todo = np.ones(n, bool)
    rows = np.flatnonzero((j_end > idx) & (axis[j_end] >= axis + 0.75 * h))
    todo[take(rows, j_end[rows], at_h)] = False
    stop = np.searchsorted(axis, axis + h + wait, side="right")     # hold on for a buyer
    for k in range(1, int((stop - j_end - 1).max(initial=0)) + 1):
        rows = np.flatnonzero(todo & (j_end + k < stop))
        todo[take(rows, j_end[rows] + k, at_h)] = False
    found = ~todo
    for k in range(0, int((j_end - idx).max(initial=0))):          # mark at the last bid seen
        rows = np.flatnonzero(todo & (j_end - k > idx))
        todo[take(rows, j_end[rows] - k, lambda sel, j: np.full(len(sel), h + wait))] = False
    return val, hrs, found


def label_forecast(frame, horizons=(HORIZON,), target=TARGET, take_profit=TAKE_PROFIT,
                   exit_on=EXIT_ON, level=LEVEL, max_jump=MAX_JUMP, wait_h=EXIT_WAIT_H):
    """Add up_<H>h, ret_<H>h, exit_h_<H>h and close_h_<H>h (hours after t until the row's
    labels are known -- the backtest's purge) for each horizon; see the module doc. A
    level or bid above max_jump x the entry ask never counts. No bid at the horizon: the
    position is held up to wait_h more for a buyer (_end_exit)."""
    df = frame.copy()
    code, axis = series_axis(df)
    ask = df.ask1.to_numpy(float)
    cap = ask * max_jump
    lvl_held = _held(df[level].to_numpy(float), axis)
    sell_on = df[exit_on].to_numpy(float)
    last_t = observed_until(df)          # a collection gap censors like the end of the data
    for h in horizons:
        censored = (last_t < df.t + pd.Timedelta(hours=h)).to_numpy() | np.isnan(ask)
        up = (~np.isnan(_first_reach(lvl_held, axis, h, ask * (1 + target), cap)[0])).astype(float)
        tp_h, j = _first_reach(sell_on, axis, h, ask * (1 + take_profit), cap)
        end_bid, end_h, found = _end_exit(sell_on, cap, axis, h, wait_h)
        # no buyer yet and the data stops before the wait is over: not known yet
        pending = np.isnan(tp_h) & ~found & (last_t < df.t + pd.Timedelta(hours=h + wait_h)).to_numpy()
        with np.errstate(invalid="ignore"):
            ret = np.where(np.isnan(tp_h), end_bid / ask - 1, sell_on[np.maximum(j, 0)] / ask - 1)
        ext = np.where(np.isnan(tp_h), end_h, tp_h)
        close = np.fmax(h + HOLD_GAP_H, np.where(np.isnan(tp_h), end_h, 0.0))
        up[censored] = np.nan
        ret[censored | pending] = np.nan
        ext[censored | pending] = np.nan
        df[up_col(h)], df[ret_col(h)], df[exit_col(h)], df[close_col(h)] = up, ret, ext, close
    df.attrs.update(target=target, take_profit=take_profit, exit_on=exit_on)
    return df

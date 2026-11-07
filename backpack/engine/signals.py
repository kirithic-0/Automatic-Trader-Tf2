"""Which features precede a step, a score built from them, and a backtest of it.

Not a trained model: a feature earns a place by **lift** -- the step rate in its
top (or bottom) `frac` of rows over the base rate -- and the score is the plain
average of the chosen features' within-pass percentile ranks. The backtest picks
features on the early history only and scores the later history, reporting
precision@k against the base rate: the benchmark a Stage-5 model has to beat.

Only rows whose label is known (not NaN) are ever used to pick or judge.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.features import FEATURES

FRAC = 0.10
MIN_LIFT = 1.5
MIN_ROWS = 200
MAX_BUCKET = 0.5   # a tail holding > half the rows (a mostly-constant feature) is no tail


@dataclass(frozen=True)
class Signal:
    feature: str
    side: str        # "high" or "low": which tail precedes steps
    cut: float       # the tail boundary, from the rows it was picked on
    strict: bool     # tail is x > cut (x < cut) rather than >= (<=)
    lift: float
    rate: float
    n: int


def _tail(x, side, frac):
    """The `frac` tail of x. When the cut value itself fills most of the rows (a
    binary or mostly-constant feature) the inclusive tail is everything, so fall
    back to the strict one: e.g. top_bot's low tail becomes 'top bidder is not a bot'."""
    cut = x.quantile(1 - frac if side == "high" else frac)
    strict = False
    mask = x >= cut if side == "high" else x <= cut
    if mask.sum() > MAX_BUCKET * len(x):
        strict = True
        mask = x > cut if side == "high" else x < cut
    return cut, mask, strict


def feature_lifts(frame, label, frac=FRAC):
    """One row per feature: coverage and both tails' step rate + lift."""
    lab = frame[label]
    known = lab.notna()
    base = lab[known].mean()
    out = []
    for f in FEATURES:
        x = frame.loc[known, f]
        y = lab[known]
        ok = x.notna()
        x, y = x[ok], y[ok]
        row = {"feature": f, "coverage": ok.mean() if len(ok) else 0.0}
        for side in ("high", "low"):
            if len(x) == 0:
                row.update({f"{side}_cut": np.nan, f"{side}_strict": False,
                            f"{side}_rate": np.nan,
                            f"{side}_lift": np.nan, f"{side}_n": 0})
                continue
            cut, mask, strict = _tail(x, side, frac)
            n = int(mask.sum())
            valid = n <= MAX_BUCKET * len(x)
            rate = y[mask].mean() if n and valid else np.nan
            row.update({f"{side}_cut": cut, f"{side}_strict": strict,
                        f"{side}_rate": rate,
                        f"{side}_lift": rate / base if base else np.nan,
                        f"{side}_n": n if valid else 0})
        out.append(row)
    return pd.DataFrame(out), base


def select_signals(frame, label, frac=FRAC, min_lift=MIN_LIFT, min_rows=MIN_ROWS):
    """Features whose better tail clears `min_lift`, strongest first."""
    lifts, _ = feature_lifts(frame, label, frac)
    picked = []
    for r in lifts.itertuples():
        best = max(("high", "low"),
                   key=lambda s: -1 if pd.isna(getattr(r, f"{s}_lift"))
                   else getattr(r, f"{s}_lift"))
        lift = getattr(r, f"{best}_lift")
        n = getattr(r, f"{best}_n")
        if pd.notna(lift) and lift >= min_lift and n >= min_rows:
            picked.append(Signal(r.feature, best, getattr(r, f"{best}_cut"),
                                 getattr(r, f"{best}_strict"), lift,
                                 getattr(r, f"{best}_rate"), n))
    return sorted(picked, key=lambda s: s.lift, reverse=True)


def score(frame, signals):
    """Average within-pass percentile rank of the signals (low side flipped);
    a missing feature counts as neutral 0.5. NaN if there are no signals."""
    if not signals:
        return pd.Series(np.nan, index=frame.index)
    parts = []
    for s in signals:
        r = frame.groupby("pass_id")[s.feature].rank(pct=True)
        parts.append((r if s.side == "high" else 1 - r).fillna(0.5))
    return sum(parts) / len(parts)


def fired(row, signals):
    """Names of the signals whose tail this row sits in (the score's drivers)."""
    out = []
    for s in signals:
        v = row[s.feature]
        if pd.isna(v):
            continue
        if s.side == "high":
            hit = v > s.cut if s.strict else v >= s.cut
        else:
            hit = v < s.cut if s.strict else v <= s.cut
        if hit:
            out.append(s.feature)
    return out


def backtest(frame, label, horizon_h, train_frac=0.7, ks=(10, 25, 50),
             frac=FRAC, min_lift=MIN_LIFT, min_rows=MIN_ROWS):
    """Pick signals on the first `train_frac` of passes, score the rest.

    Train rows are cut `horizon_h` before the split so no train label peeks
    into test time. Returns a dict with the split, the signals, base rate, and
    per-k mean precision over test passes (and its lift over the base rate).
    """
    known = frame[frame[label].notna()]
    times = np.sort(known.t.unique())
    if len(times) < 4:
        return None
    split = times[int(len(times) * train_frac)]
    train = known[known.t <= split - pd.Timedelta(hours=horizon_h)]
    test = known[known.t >= split].copy()
    if train.empty or test.empty:
        return None
    sigs = select_signals(train, label, frac, min_lift, min_rows)
    base = test[label].mean()
    res = {"split": pd.Timestamp(split), "signals": sigs, "base": base,
           "n_test_passes": test.pass_id.nunique(), "precision": {}}
    if not sigs:
        return res
    test["_score"] = score(test, sigs)
    for k in ks:
        top = (test.sort_values("_score", ascending=False)
                   .groupby("pass_id").head(k))
        p = top.groupby("pass_id")[label].mean().mean()
        res["precision"][k] = (p, p / base if base else np.nan)
    return res

"""The honest test: purged walk-forward days, and a portfolio over their predictions."""
import numpy as np
import pandas as pd

from engine.forecast.config import (HOLD_GAP_H, HORIZON, MAX_NEW_BUYS, MAX_SPREAD, MIN_EXP_RET, MIN_PRICE, SEEDS,
                                    TRAIN_MAX_SPREAD)
from engine.forecast.frame import feature_list, universe
from engine.forecast.labels import close_col, exit_col, ret_col, up_col
from engine.forecast.live import pick
from engine.forecast.models import fit_models, predict


def _auc(y, s):
    from sklearn.metrics import roc_auc_score
    return roc_auc_score(y, s) if 0 < y.sum() < len(y) else np.nan


def _ap(y, s):
    from sklearn.metrics import average_precision_score
    return average_precision_score(y, s) if y.sum() > 0 else np.nan


def _topk_mean(d, score, col, k):
    top = d.assign(_s=score).sort_values("_s", ascending=False).groupby("pass_id").head(k)
    return top.groupby("pass_id")[col].mean().mean()


def test_blocks(t_end, n_folds=4, fold_h=24.0, since=None):
    """The backtest's test windows (start, end], oldest first: the last `n_folds` blocks of
    `fold_h` hours ending at t_end -- or, given `since`, consecutive blocks from `since` up
    to t_end (the last one may be short): a forward test of a recipe frozen at `since`."""
    step = pd.Timedelta(hours=fold_h)
    if since is None:
        return [(t_end - step * i, t_end - step * (i - 1)) for i in range(n_folds, 0, -1)]
    since = pd.Timestamp(since)
    since = since.tz_localize("UTC") if since.tzinfo is None else since
    n = int(np.ceil((t_end - since) / step)) if t_end > since else 0
    return [(since + step * i, min(since + step * (i + 1), t_end)) for i in range(n)]


def walk_forward(frame, horizon=HORIZON, n_folds=4, fold_h=24.0, max_spread=MAX_SPREAD,
                 train_max_spread=TRAIN_MAX_SPREAD, feats=None, ks=(5, 10, 20), seeds=SEEDS,
                 since=None, train_mask=None):
    """Train on rows whose label was fully known before each test day, test on that day.
    A label closes at t + horizon plus the one pass (<= HOLD_GAP_H) its held check reads,
    or later when a position waited past the horizon for a buyer (close_h_<H>h) -- that
    is the purge. Test days are the last `n_folds` blocks of `fold_h` hours with
    known labels, or every block since `since` (test_blocks). `train_mask` (a boolean
    Series on frame): only those rows train (e.g. only the admitted goods), every
    row is still scored. Returns {"folds": one row per test day, "preds": test rows with
    p_up / exp_ret}."""
    feats = feats or feature_list(frame)
    up, ret = up_col(horizon), ret_col(horizon)
    labelled = frame[up].notna()
    U, Utr = universe(frame, max_spread), universe(frame, train_max_spread)
    if train_mask is not None:
        Utr = Utr & train_mask
    t_end = frame.loc[labelled, "t"].max()
    close_h = (frame[close_col(horizon)] if close_col(horizon) in frame
               else pd.Series(horizon + HOLD_GAP_H, index=frame.index))
    closes = frame.t + pd.to_timedelta(close_h.fillna(horizon + HOLD_GAP_H), unit="h")
    rows, preds = [], []
    rng = np.random.RandomState(0)
    for te_start, te_end in test_blocks(t_end, n_folds, fold_h, since):
        tr = frame[Utr & labelled & (closes <= te_start)]
        te = frame[U & labelled & (frame.t > te_start) & (frame.t <= te_end)].copy()
        if tr[up].nunique() < 2 or te.empty:
            continue
        models = fit_models(tr, feats, horizon, seeds)
        te["p_up"], te["exp_ret"] = predict(models, te, feats)
        noise = rng.rand(len(te)) * 1e-9
        r = {"test_start": te_start, "test_end": te_end, "n_train": len(tr), "n_test": len(te),
             "passes": te.pass_id.nunique(), "base": te[up].mean(),
             "auc": _auc(te[up], te.p_up), "ap": _ap(te[up], te.p_up),
             "ret_all": te[ret].mean()}
        for k in ks:
            r[f"prec@{k}"] = _topk_mean(te, te.p_up + noise, up, k)
            r[f"ret@{k}"] = _topk_mean(te, te.exp_ret + noise, ret, k)
            r[f"ret_spread@{k}"] = _topk_mean(te, -te.spread + noise, ret, k)   # naive: tightest first
        rows.append(r)
        preds.append(te)
    return {"folds": pd.DataFrame(rows), "preds": pd.concat(preds) if preds else pd.DataFrame(),
            "horizon": horizon, "feats": feats}


def simulate(preds, horizon=HORIZON, k=MAX_NEW_BUYS, min_exp_ret=MIN_EXP_RET, min_price=MIN_PRICE):
    """A non-overlapping portfolio over backtest predictions: at each pass buy what `pick`
    picks, holding = the series whose earlier position has not exited yet;
    each position exits per ret_<H>h after exit_h_<H>h hours. One unit per trade.
    Returns one row per trade (entry time, item, entry ask, ret, profit in keys)."""
    ret, ext = ret_col(horizon), exit_col(horizon)
    held_until = {}
    trades = []
    for pid, d in preds.sort_values("t").groupby("pass_id", sort=False):
        t = d.t.iloc[0]
        held = {key for key, until in held_until.items() if until > t}
        for r in pick(d, held, k, min_exp_ret, min_price).itertuples():
            exit_h = getattr(r, ext)
            held_until[(r.item, r.quality, r.variant)] = (
                t + pd.Timedelta(hours=float(exit_h) if exit_h == exit_h else horizon))
            trades.append({"t": t, "pass_id": pid, "label": r.label, "ask1": r.ask1,
                           "p_up": r.p_up, "exp_ret": r.exp_ret, "ret": getattr(r, ret),
                           "profit_keys": getattr(r, ret) * r.ask1, "exit_h": exit_h})
    return pd.DataFrame(trades)

"""Today's decision: pick, the one buy rule every path shares, and scoring the latest pass."""
import numpy as np

from engine.forecast.config import (HORIZON, LIVE_SEEDS, MAX_NEW_BUYS, MAX_SPREAD, MIN_EXP_RET, MIN_PRICE,
                                    MODEL_MAX_AGE_H, TAKE_PROFIT, TARGET, TRAIN_MAX_SPREAD)
from engine.forecast.frame import feature_list, universe
from engine.forecast.labels import up_col
from engine.forecast.models import drivers, fit_models, live_models, model_key, predict
from engine.panel import SERIES


def pick(scored, held=(), k=MAX_NEW_BUYS, min_exp_ret=MIN_EXP_RET, min_price=MIN_PRICE,
         min_p_up=None, max_price=None, rank="exp_ret"):
    """The buys at one pass: of `scored` (one pass's rows with exp_ret / p_up), the k with
    the highest `rank` whose exp_ret > min_exp_ret (None: no exp_ret bar), p_up >= min_p_up
    (None: none), ask at least min_price and under max_price keys (None: no cap), and whose
    (item, quality, variant) is not in `held` -- never two of one series at once. The one
    rule the backtest (simulate), the replay and the live agent buy by; the agent's own
    settings are AGENT_RULE."""
    held = set(held)
    ok = scored.ask1 >= min_price
    if min_exp_ret is not None:
        ok &= scored.exp_ret > min_exp_ret
    if min_p_up is not None:
        ok &= scored.p_up >= min_p_up
    if max_price is not None:
        ok &= scored.ask1 < max_price
    pos = np.flatnonzero(ok.to_numpy())
    pos = pos[np.argsort(-scored[rank].to_numpy()[pos], kind="stable")]
    keys = list(scored[SERIES].itertuples(index=False, name=None))
    keep = []   # positions, not index labels: a concatenated frame can repeat a label
    for i in pos:
        if len(keep) >= k:
            break
        if keys[i] in held:
            continue
        held.add(keys[i])
        keep.append(i)
    return scored.iloc[keep]


def whole_pass(frame, share=0.9, lookback=10):
    """The newest pass with at least `share` of the rows a pass usually has (the median of
    the last `lookback` passes) -- a pass the scraper is still writing (a pass takes 5-15
    min) is passed over for the one before it."""
    n = frame.groupby("pass_id").size().sort_index()
    ok = n[n >= share * n.tail(lookback).median()]
    return int(ok.index[-1] if len(ok) else n.index[-1])


def predict_latest(frame, horizon=HORIZON, max_spread=MAX_SPREAD,
                   train_max_spread=TRAIN_MAX_SPREAD, target=TARGET, take_profit=TAKE_PROFIT,
                   min_exp_ret=MIN_EXP_RET, min_price=MIN_PRICE, feats=None, seeds=LIVE_SEEDS,
                   n_drivers=3, model_dir=None, max_age_h=MODEL_MAX_AGE_H, retrain=False, at=None):
    """Train on every labelled row (cheap items too: more to learn from), score the latest
    whole pass's universe. One row per item, best exp_ret first: p_up, exp_ret, buy
    (exp_ret > min_exp_ret and ask1 >= min_price), up_at (the ask +
    target the buy-order median has to reach), sell_at (the take-profit price) and the
    drivers of exp_ret. Given `model_dir`, the model is the day's saved one (live_models:
    rebuilt from scratch once `max_age_h` old, or always with `retrain`) instead of a new
    fit every call. attrs: base_rate, n_train, trained_at (None when fit for this call)."""
    feats = feats or feature_list(frame)
    up = up_col(horizon)
    train = frame[universe(frame, train_max_spread) & frame[up].notna()]
    last = whole_pass(frame)
    now = frame[(frame.pass_id == last) & universe(frame, max_spread)].copy()
    if now.empty or train[up].nunique() < 2:
        return now
    if model_dir is None:
        models = fit_models(train, feats, horizon, seeds)
        meta = {"trained_at": None, "n_train": len(train), "base_rate": float(train[up].mean())}
    else:
        key = model_key(feats, horizon, train_max_spread, target, take_profit, seeds,
                        frame.quality.unique())
        models, meta = live_models(train, feats, horizon, seeds, model_dir, key, max_age_h,
                                   retrain, at)
    now["p_up"], now["exp_ret"] = predict(models, now, feats)
    now["buy"] = (now.exp_ret > min_exp_ret) & (now.ask1 >= min_price)
    now["up_at"] = now.ask1 * (1 + target)
    now["sell_at"] = now.ask1 * (1 + take_profit)
    if n_drivers:
        now["drivers"] = drivers(models, now, feats, n_drivers)
    now.attrs.update(base_rate=meta["base_rate"], n_train=meta["n_train"],
                     trained_at=meta["trained_at"])
    return now.sort_values("exp_ret", ascending=False)

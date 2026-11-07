"""The two xgboost models: fit, predict, explain, and the day's saved copy."""
import json
import os
import pickle

import numpy as np
import pandas as pd

from engine.forecast.config import (CLF_PARAMS, HORIZON, MODEL_FILE, MODEL_MAX_AGE_H, REG_PARAMS, RET_FLOOR,
                                    SEEDS)
from engine.forecast.labels import ret_col, up_col


def _xgb():
    import xgboost as xgb   # imported lazily: only the forecast needs it
    return xgb


_DEVICE = []


def device():
    """Where xgboost trains: $FORECAST_DEVICE ("cpu" / "cuda"), else "cuda" when a GPU
    answers a one-tree fit, else "cpu". The GPU is ~3x faster here and leaves the CPU to
    a scraper running beside it; predictions always run on the CPU."""
    if not _DEVICE:
        import os
        import warnings
        dev = os.environ.get("FORECAST_DEVICE", "auto")
        if dev == "auto":
            xgb, dev = _xgb(), "cpu"
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error")   # "no visible GPU" is a warning
                    xgb.XGBRegressor(n_estimators=1, device="cuda").fit(np.zeros((4, 1)), np.arange(4.0))
                dev = "cuda"
            except Exception:
                pass
        _DEVICE.append(dev)
    return _DEVICE[0]


def _fit(model, x, y):
    """Fit on device(); hand the fitted booster to the CPU for prediction (a CPU frame
    fed to a GPU booster only warns and copies)."""
    model.fit(x, y)
    model.get_booster().set_param({"device": "cpu"})
    return model


def _x(rows, feats):
    """The model matrix: every feature as float (a bool-with-NaN column reads as object)."""
    return rows[feats].astype(float)


def fit_models(train, feats, horizon=HORIZON, seeds=SEEDS):
    """(classifiers, regressors): one per seed, on the labelled rows of `train` (the
    regressor on those whose trade return is known -- a missing bid at the horizon has
    none)."""
    xgb = _xgb()
    up, ret = train[up_col(horizon)], train[ret_col(horizon)]
    has_up, has_ret = up.notna(), np.isfinite(ret)
    x_up, x_ret = _x(train[has_up], feats), _x(train[has_ret], feats)
    y_up, y_ret = up[has_up], ret[has_ret].clip(lower=RET_FLOOR)
    dev = device()
    clfs, regs = [], []
    for s in seeds:
        clfs.append(_fit(xgb.XGBClassifier(**CLF_PARAMS, device=dev, random_state=s), x_up, y_up))
        regs.append(_fit(xgb.XGBRegressor(**REG_PARAMS, device=dev, random_state=s), x_ret, y_ret))
    return clfs, regs


def predict(models, rows, feats):
    """(p_up, exp_ret) arrays for `rows`, averaged over the seeds."""
    clfs, regs = models
    x = _x(rows, feats)
    p = np.mean([m.predict_proba(x)[:, 1] for m in clfs], axis=0)
    r = np.mean([m.predict(x) for m in regs], axis=0)
    return p, r


def drivers(models, rows, feats, n=3, model="exp_ret"):
    """Per row, the n features pushing `model` (exp_ret or p_up) up the most -- xgboost's
    SHAP contributions from the first seed's model: the 'why' beside each pick."""
    xgb = _xgb()
    booster = (models[1][0] if model == "exp_ret" else models[0][0]).get_booster()
    contrib = booster.predict(xgb.DMatrix(_x(rows, feats)), pred_contribs=True)[:, :-1]
    names = np.array(feats)
    top = np.argsort(-contrib, axis=1)[:, :n]
    return [", ".join(names[j] for j in r if contrib[i, j] > 0) for i, r in enumerate(top)]


def model_key(feats, horizon, train_max_spread, target, take_profit, seeds, qualities):
    """What a saved live model was trained for; one saved for anything else is rebuilt."""
    return json.dumps({"feats": list(feats), "horizon": horizon, "train_max_spread": train_max_spread,
                       "target": target, "take_profit": take_profit, "seeds": list(seeds),
                       "qualities": sorted(int(q) for q in qualities), "clf": CLF_PARAMS,
                       "reg": REG_PARAMS, "ret_floor": RET_FLOOR, "xgboost": _xgb().__version__},
                      sort_keys=True)


def live_models(train, feats, horizon, seeds, model_dir, key, max_age_h=MODEL_MAX_AGE_H,
                retrain=False, at=None):
    """(models, meta): the model saved in `model_dir` when it was trained for `key` less than
    `max_age_h` hours before `at` (default now), else a new one fit from scratch on `train`
    and saved. meta = {key, trained_at, n_train, base_rate, label_end}. A file that will not
    load (a torn write, another xgboost) is replaced; one that cannot be written (another
    process has it open) only costs the next run a refit."""
    at = at or pd.Timestamp.now(tz="UTC")
    path = os.path.join(model_dir, MODEL_FILE)
    if not retrain and os.path.exists(path):
        try:
            with open(path, "rb") as f:
                saved = pickle.load(f)
            age_h = (at - pd.Timestamp(saved["meta"]["trained_at"])).total_seconds() / 3600
            if saved["meta"]["key"] == key and 0 <= age_h < max_age_h:
                return saved["models"], saved["meta"]
        except Exception:
            pass
    models = fit_models(train, feats, horizon, seeds)
    meta = {"key": key, "trained_at": at.isoformat(), "n_train": len(train),
            "base_rate": float(train[up_col(horizon)].mean()), "label_end": train.t.max().isoformat()}
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(model_dir, exist_ok=True)
        with open(tmp, "wb") as f:
            pickle.dump({"models": models, "meta": meta}, f)
        os.replace(tmp, path)   # a reader sees the old file or the new one, never half of one
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
    return models, meta

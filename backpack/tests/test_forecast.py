# -*- coding: utf-8 -*-
"""Offline test for engine.forecast (Stage 5): the up / ret labels (held level, take-profit
sold into the first best bid past it, the misread guard, censoring), no look-ahead in the
level features, the purged
walk-forward on a planted signal, the non-overlapping portfolio, predict_latest, and the
whole forecast_frame over a throwaway trading.db (cache kept in the temp dir).

    python test_forecast.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import json
import math
import os
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

os.environ.setdefault("FORECAST_DEVICE", "cpu")   # the same trees on every machine
from engine import forecast as fc   # noqa: E402
from engine.bot_features import BOOK_BOT_FEATURES   # noqa: E402

fails = []
T0 = pd.Timestamp("2026-09-20", tz="UTC")


def check(name, got, want, tol=1e-9):
    if isinstance(want, float) and isinstance(got, (float, np.floating)):
        ok = (math.isnan(want) and math.isnan(got)) or abs(got - want) <= tol
    else:
        ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def series(item, bid_med, bid1=None, ask1=None, start=0, bid1_fill=None):
    n = len(bid_med)
    bid1 = bid_med if bid1 is None else bid1
    bid1_fill = bid1 if bid1_fill is None else bid1_fill
    ask1 = [1.0] * n if ask1 is None else ask1
    return pd.DataFrame({"item": item, "quality": 6, "variant": 0, "pass_id": range(start, start + n),
                         "t": [T0 + pd.Timedelta(hours=h) for h in range(start, start + n)],
                         "bid_med": bid_med, "bid1": bid1, "ask1": ask1, "bid1_fill": bid1_fill})


def test_labels():
    print("label_forecast")
    #         t: 0     1     2     3     4     5     6     7     8     9
    med = [1.00, 1.00, 1.03, 1.00, 1.03, 1.03, 1.03, 1.03, 1.03, 1.03]
    b1 = [1.00, 1.00, 1.03, 1.06, 1.06, 1.03, 1.03, 1.03, 1.03, 1.03]
    f = fc.label_forecast(series("A", med, b1), horizons=(6,), target=0.02, take_profit=0.05)
    r = f.set_index("pass_id")
    check("a one-pass spike of the median is not held; t=4..5 is -> up", r.loc[0, "up_6h"], 1.0)
    check("t=1: the level first holds >= ask+2% at t=4", r.loc[1, "up_6h"], 1.0)
    check("take-profit: sold into the first best bid >= ask+5% (t=3, 1.06) at its price",
          r.loc[0, "ret_6h"], 0.06, tol=1e-12)
    check("exit after 3 h", r.loc[0, "exit_h_6h"], 3.0)
    check("t=3: the next pass's 1.06 is enough -- one pass, sold into", r.loc[3, "ret_6h"], 0.06,
          tol=1e-12)
    check("t=3: exit after 1 h", r.loc[3, "exit_h_6h"], 1.0)
    slow = fc.label_forecast(series("H", [1.0] * 10, [1.0, 1.02] + [1.03] * 8), horizons=(6,))
    check("no +5% bid ahead -> sell to the best bid at t+6 (1.03)", slow.ret_6h.iloc[0], 0.03,
          tol=1e-12)
    check("... exit at the horizon", slow.exit_h_6h.iloc[0], 6.0)
    check("t=4 (window runs past the data): censored, NaN", r.loc[4, "up_6h"], float("nan"))
    flat = fc.label_forecast(series("B", [1.0] * 10), horizons=(6,))
    check("a flat book never goes up", flat.up_6h.iloc[0], 0.0)
    check("... and the trade loses nothing at a zero spread", flat.ret_6h.iloc[0], 0.0)
    gap = series("C", [1.0, 1.0, 1.05, 1.0, 1.0])
    gap.loc[3:, "t"] = gap.loc[3:, "t"] + pd.Timedelta(hours=9)      # a 10 h gap in C after t=2
    other = series("Z", [1.0] * 16)                                   # passes kept running
    g = fc.label_forecast(pd.concat([gap, other], ignore_index=True), horizons=(12,))
    check("a level whose next pass is > 3 h later is not held", g.up_12h.iloc[0], 0.0)
    g = fc.label_forecast(gap, horizons=(12,))
    check("no pass at all for 10 h (collector down): the window is censored, NaN",
          g.up_12h.iloc[0], float("nan"))
    check("... the trade too", g.ret_12h.iloc[0], float("nan"))
    spike = fc.label_forecast(series("D", [1.0] * 10, [1.0, 1.0, 1.08] + [1.0] * 7), horizons=(6,))
    check("a one-pass best-bid spike is sold into (the fill rule)", spike.ret_6h.iloc[0], 0.08,
          tol=1e-12)
    check("... after 2 h", spike.exit_h_6h.iloc[0], 2.0)
    mis = fc.label_forecast(series("E", [1.0] * 10, [1.0, 9.0, 9.0] + [1.0] * 7), horizons=(6,))
    check(f"a bid over {fc.MAX_JUMP:g}x the ask is a misread, never a sale", mis.ret_6h.iloc[0], 0.0)
    mend = fc.label_forecast(series("F", [1.0] * 10, [1.0] * 6 + [9.0] * 4), horizons=(6,))
    check("... and a horizon exit into one, no buyer yet and no data left: unknown",
          mend.ret_6h.iloc[0], float("nan"))
    mlv = fc.label_forecast(series("G", [1.0, 9.0, 9.0] + [1.0] * 7), horizons=(6,))
    check("... nor does a misread level count as up", mlv.up_6h.iloc[0], 0.0)
    #   an unstable account (engine.book.UNSTABLE_FILLS) spikes bid1 at t=2; the bid that fills does not
    uns = fc.label_forecast(series("U", [1.0] * 10, [1.0, 1.0, 1.08] + [1.0] * 7,
                                   bid1_fill=[1.0, 1.0, 1.01] + [1.0] * 7), horizons=(6,))
    check("a spike from an account whose trades do not go through is never sold into",
          uns.ret_6h.iloc[0], 0.0)
    uhe = fc.label_forecast(series("V", [1.0] * 10, [1.0] * 6 + [1.03] * 4, bid1_fill=[1.0] * 6 + [1.01] * 4),
                            horizons=(6,))
    check("... and the horizon exit sells to the bid that fills (1.01, not 1.03)", uhe.ret_6h.iloc[0], 0.01,
          tol=1e-12)

    # no bid at the horizon: hold on for a buyer (up to EXIT_WAIT_H), else mark at the last bid
    nan = float("nan")
    #            t: 0    1    2    3    4    5    6    7    8    9   10   11 ... 40
    wb = [1.0, 1.0, 1.0, 1.0, 1.0, 0.99, nan, nan, nan, 0.97] + [0.97] * 31
    w = fc.label_forecast(series("W", [1.0] * 41, wb), horizons=(6,)).set_index("pass_id")
    check("no bid at t+6: held until the first buyer (t=9, 0.97)", w.loc[0, "ret_6h"], -0.03, tol=1e-12)
    check("... exit 9 h after entry", w.loc[0, "exit_h_6h"], 9.0)
    check("... and the label is known only then (the purge)", w.loc[0, "close_h_6h"], 9.0)
    check("a bid at the horizon: known at t+6 plus the held pass", w.loc[1, "close_h_6h"], 6.0 + fc.HOLD_GAP_H)
    mb = [1.0, 1.0, 0.98] + [nan] * 38
    m = fc.label_forecast(series("M", [1.0] * 41, mb), horizons=(6,)).set_index("pass_id")
    check("no buyer within the wait: marked at the last bid seen (t=2, 0.98)", m.loc[0, "ret_6h"], -0.02,
          tol=1e-12)
    check("... at t+6+wait", m.loc[0, "exit_h_6h"], 6.0 + fc.EXIT_WAIT_H)
    pb = [1.0, 1.0, 0.98] + [nan] * 10
    pnd = fc.label_forecast(series("P", [1.0] * 13, pb), horizons=(6,))
    check("no buyer yet but the data stops inside the wait: unknown", pnd.ret_6h.iloc[0], nan)
    gp = series("Q", [1.0] * 40, [1.0, 1.0, 1.0, 0.95] + [0.96] * 36)
    gp.loc[4:, "t"] = gp.loc[4:, "t"] + pd.Timedelta(hours=5)   # no pass between t=3 and t=9
    g = fc.label_forecast(gp, horizons=(6,)).set_index("pass_id")
    check("a scrape gap over the horizon: not sold at t=3 (before t+4.5), but at the next pass",
          g.loc[0, "ret_6h"], -0.04, tol=1e-12)
    check("... 9 h after entry", g.loc[0, "exit_h_6h"], 9.0)


def test_drop_misreads():
    print("drop_misreads")
    s = series("X", [1.0, 1.0, 9.0, 9.0, 1.0], [1.0, 1.0, 9.0, 9.0, 1.0], [1.02, 1.02, 1.02, float("nan"), 1.02])
    s["bid5"], s["gap2"] = s.bid1, 0.0
    d = fc.drop_misreads(s)
    check("a median over 3x the same pass's ask: every bid read blanked",
          bool(d.loc[2, ["bid1", "bid_med", "bid5", "gap2", "bid1_fill"]].isna().all()), True)
    check("... over 3x the last ask when this pass has none", bool(np.isnan(d.loc[3, "bid_med"])), True)
    check("... sane passes untouched", d.loc[[0, 1, 4], "bid_med"].tolist(), [1.0, 1.0, 1.0])
    check("... and counted", d.attrs["n_misread"], 2)


def test_no_lookahead():
    print("add_level_features: no look-ahead")
    rng = np.random.RandomState(0)
    parts = []
    for i in range(4):
        n = 120
        lvl = np.exp(np.cumsum(rng.randn(n) * 0.01))
        s = series(f"I{i}", lvl, lvl * 1.01, lvl * 1.02)
        s["ask_med"], s["ask1_bot"], s["bid5"] = lvl * 1.03, lvl * 1.02, lvl
        s["pdb_buy"], s["pdb_sell"] = 1.0, 1.1
        s["n_buy"], s["n_buy_fenced"], s["n_sell"], s["n_sell_fenced"] = 7, 0, 7, 1
        parts.append(s)
    base = pd.concat(parts).sort_values(["item", "quality", "variant", "t"]).reset_index(drop=True)
    a = fc.add_reach_features(fc.add_level_features(base))
    cut = T0 + pd.Timedelta(hours=80)
    later = base.t > cut
    moved = base.copy()
    for c in ("bid_med", "bid1", "bid1_fill", "ask1", "ask_med", "ask1_bot", "bid5"):
        moved.loc[later, c] = moved.loc[later, c] * 1.5
    b = fc.add_reach_features(fc.add_level_features(moved))
    cols = [c for c in fc.LEVEL_FEATURES + fc.REACH_FEATURES
            if c in a.columns and c != "mkt_lvl_up_share"]
    early = ~later
    same = np.allclose(a.loc[early, cols].to_numpy(float), b.loc[early, cols].to_numpy(float),
                       equal_nan=True)
    check("changing every price after t never changes a feature at or before t", same, True)
    check("the 72 h features exist and are not all empty",
          bool(a["lvl_vs_max72"].notna().any() and a["lvl_vol_72h"].notna().any()), True)
    #          t: 0    1    2     3    4    5
    r = fc.add_reach_features(series("R", [1.0] * 6, [1.0, 1.06, 1.0, 1.0, 1.0, 1.0]))
    r = r.set_index("pass_id")
    u = fc.add_reach_features(series("S", [1.0] * 6, [1.0, 1.06, 1.0, 1.0, 1.0, 1.0], bid1_fill=[1.0] * 6))
    check("reach reads the bid that fills, not an unstable account's bid1",
          u.set_index("pass_id").loc[4, "b1max24_over_ask"], 0.0, tol=1e-12)
    check("reach: the best bid's 24 h high vs today's ask", r.loc[4, "b1max24_over_ask"], 0.06, tol=1e-12)
    check("... 1 of the 4 earlier passes had the bid at ask +5%", r.loc[4, "b1_tp_share72"], 0.25)
    check("... 3 h ago", r.loc[4, "h_since_b1_tp"], 3.0)
    check("... and at t=1 itself only earlier passes count", r.loc[1, "h_since_b1_tp"], float("nan"))


def test_blocks():
    print("test_blocks")
    end = T0 + pd.Timedelta(hours=100)
    b = fc.test_blocks(end, n_folds=2, fold_h=24.0)
    check("the last n_folds days, oldest first, ending at the last label",
          [(s.hour, e.hour) for s, e in b] == [(4, 4), (4, 4)] and b[-1][1] == end, True)
    f = fc.test_blocks(end, fold_h=24.0, since=(T0 + pd.Timedelta(hours=50)).tz_localize(None))
    check("since: consecutive days from the freeze (naive time read as UTC)",
          [(e - s) / pd.Timedelta(hours=1) for s, e in f], [24.0, 24.0, 2.0])
    check("... the first starts at the freeze", f[0][0], T0 + pd.Timedelta(hours=50))
    check("... the last stops at the last label", f[-1][1], end)
    check("since after the last label: nothing to test", fc.test_blocks(end, since=end), [])


def planted(n_items=40, n_pass=200, h=48.0, seed=1):
    """A frame with a planted signal: up (and a +5% trade) is likely when `sig` > 0.8."""
    rng = np.random.RandomState(seed)
    rows = []
    for i in range(n_items):
        for p in range(n_pass):
            sig, spread = rng.rand(), rng.rand() * 0.05
            up = rng.rand() < (0.6 if sig > 0.8 else 0.03)
            rows.append({"item": f"I{i}", "quality": 6, "variant": 0, "pass_id": p,
                         "t": T0 + pd.Timedelta(hours=p), "label": f"I{i} (Unique)",
                         "sig": sig, "noise": rng.randn(), "spread": spread, "bid1": 1.0,
                         "bid_med": 1.0, "ask1": 1 + spread, "up": float(up),
                         "ret": 0.05 if up else -0.02, "exit": 10.0 if up else h})
    f = pd.DataFrame(rows)
    done = f.t <= f.t.max() - pd.Timedelta(hours=h)
    f[fc.up_col(h)] = f.up.where(done)
    f[fc.ret_col(h)] = f.ret.where(done)
    f[fc.exit_col(h)] = f.exit.where(done)
    return f.sort_values(["item", "quality", "variant", "t"]).reset_index(drop=True)


def test_walk_forward():
    print("walk_forward / simulate / predict_latest (planted signal)")
    f = planted()
    rep = fc.walk_forward(f, horizon=48.0, n_folds=2, feats=["sig", "noise"], seeds=(0,))
    folds = rep["folds"]
    check("two test days", len(folds), 2)
    # a perfect model scores AUC ~0.87 here (the label is noisy by design); random is 0.5
    check("the planted signal is found out of sample (AUC > 0.75 each day)",
          bool((folds.auc > 0.75).all()), True)
    check("precision@10 beats twice the base rate", bool((folds["prec@10"] > 2 * folds.base).all()), True)
    check("the return model's top 10 beat the whole universe", bool((folds["ret@10"] > folds.ret_all).all()),
          True)
    labelled = f[fc.up_col(48.0)].notna()
    for r in folds.itertuples():
        n = int((fc.universe(f, fc.TRAIN_MAX_SPREAD) & labelled
                 & (f.t + pd.Timedelta(hours=48 + fc.HOLD_GAP_H) <= r.test_start)).sum())
        check(f"purged (H + the held pass): train rows before {r.test_start:%m-%d %H:%M}", r.n_train, n)
    slow = f.assign(**{fc.close_col(48.0): np.where(f.index % 2 == 0, 48.0 + fc.HOLD_GAP_H, 70.0)})
    rs = fc.walk_forward(slow, horizon=48.0, n_folds=1, feats=["sig", "noise"], seeds=(0,))["folds"].iloc[0]
    n = int((fc.universe(f, fc.TRAIN_MAX_SPREAD) & labelled
             & (slow.t + pd.to_timedelta(slow[fc.close_col(48.0)], unit="h") <= rs.test_start)).sum())
    check("... and a row that waited past the horizon for a buyer closes later (close_h)", rs.n_train, n)
    tr = fc.simulate(rep["preds"], horizon=48.0, k=10, min_exp_ret=0.0)
    overlap = 0
    for item, g in tr.groupby("label"):
        g = g.sort_values("t")
        ends = g.t + pd.to_timedelta(g.exit_h, unit="h")
        overlap += int((g.t.iloc[1:].to_numpy() < ends.iloc[:-1].to_numpy()).sum())
    check("the portfolio never holds two of one item at once", overlap, 0)
    check("at most 10 buys per pass", int(tr.groupby("pass_id").size().max()) <= 10, True)
    now = fc.predict_latest(f.assign(**{fc.LEVEL: 1.0}), horizon=48.0, feats=["sig", "noise"],
                            seeds=(0,), n_drivers=2)
    check("predict_latest scores only the latest pass", set(now.pass_id), {f.pass_id.max()})
    part = f[(f.pass_id < f.pass_id.max()) | (f.item.isin(["I0", "I1", "I2"]))]
    check("... but passes over one the scraper is still writing (3 of 40 items)",
          fc.whole_pass(part), int(f.pass_id.max()) - 1)
    check("... best exp_ret first", bool(now.exp_ret.is_monotonic_decreasing), True)
    check("... p_up is a probability", bool(now.p_up.between(0, 1).all()), True)
    check("... BUY = exp_ret above the bar and the ask at the minimum price",
          bool((now.buy == ((now.exp_ret > fc.MIN_EXP_RET) & (now.ask1 >= fc.MIN_PRICE))).all()), True)
    cheap = fc.predict_latest(f.assign(**{fc.LEVEL: 1.0}), horizon=48.0, feats=["sig", "noise"],
                              seeds=(0,), n_drivers=0, min_price=5.0)
    check("... an ask under min_price is never a BUY", bool(cheap.buy.any()), False)
    check("the portfolio skips asks under min_price",
          len(fc.simulate(rep["preds"], horizon=48.0, k=10, min_exp_ret=0.0, min_price=5.0)), 0)
    hi = now[now.sig > 0.8].p_up.mean() if (now.sig > 0.8).any() else 1.0
    check("... high-signal items get the higher p_up", bool(hi > now[now.sig <= 0.8].p_up.mean()), True)


def test_saved_model():
    print("predict_latest with a model_dir: the day's saved model")
    f = planted().assign(**{fc.LEVEL: 1.0})
    kw = dict(horizon=48.0, feats=["sig", "noise"], seeds=(0,), n_drivers=0)
    fits = []
    real_fit = fc.fit_models
    fc.models.fit_models = fc.live.fit_models = lambda *a, **k: fits.append(1) or real_fit(*a, **k)
    try:
        with tempfile.TemporaryDirectory() as d:
            t = pd.Timestamp("2026-10-26 12:00", tz="UTC")
            a = fc.predict_latest(f, model_dir=d, at=t, **kw)
            check("first call fits and saves", (len(fits), os.path.exists(os.path.join(d, fc.MODEL_FILE))),
                  (1, True))
            check("... trained_at is the call's time", a.attrs["trained_at"], t.isoformat())
            b = fc.predict_latest(f, model_dir=d, at=t + pd.Timedelta(hours=23), **kw)
            check("23 h later: the saved model, no fit", len(fits), 1)
            check("... the same scores", bool(np.allclose(a.exp_ret, b.exp_ret)), True)
            check("... attrs describe the saved model", (b.attrs["trained_at"], b.attrs["n_train"]),
                  (a.attrs["trained_at"], a.attrs["n_train"]))
            c = fc.predict_latest(f, model_dir=d, at=t + pd.Timedelta(hours=24), **kw)
            check("24 h later: a new model from scratch", (len(fits), c.attrs["trained_at"]),
                  (2, (t + pd.Timedelta(hours=24)).isoformat()))
            t2 = t + pd.Timedelta(hours=25)
            fc.predict_latest(f, model_dir=d, at=t2, retrain=True, **kw)
            check("--retrain: a new model now", len(fits), 3)
            fc.predict_latest(f, model_dir=d, at=t2, **dict(kw, seeds=(1,)))
            check("other seeds (any key change): a new model", len(fits), 4)
            fc.predict_latest(f.assign(quality=11), model_dir=d, at=t2, **dict(kw, seeds=(1,)))
            check("other qualities: a new model", len(fits), 5)
            with open(os.path.join(d, fc.MODEL_FILE), "wb") as fh:
                fh.write(b"torn")
            e = fc.predict_latest(f, model_dir=d, at=t2, **kw)
            check("an unreadable file is replaced", (len(fits), "p_up" in e), (6, True))
            fc.predict_latest(f, model_dir=d, at=t2, **kw)
            check("... and the replacement loads", len(fits), 6)
            fc.predict_latest(f, at=t2, **kw)
            check("no model_dir: fit every call, as before", len(fits), 7)
    finally:
        fc.models.fit_models = fc.live.fit_models = real_fit


# -- end to end over a throwaway trading.db ------------------------------------

def L(sid, keys, bot=True):
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": 0, "ref": round(keys * 60, 2), "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False}


def book(level, spread):
    buys = [L(f"b{i}", round(level * (1 - 0.01 * i), 6)) for i in range(5)]
    sells = [L(f"s{i}", round(level * (1 + spread + 0.01 * i), 6)) for i in range(5)]
    return {"buyOrders": buys, "sellOrders": sells}


def test_end_to_end():
    print("forecast_frame over a temp trading.db")
    tmp = tempfile.mkdtemp()
    db, cache = os.path.join(tmp, "trading.db"), os.path.join(tmp, "cache")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE passes (id INTEGER PRIMARY KEY, started_at TEXT)")
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, pass_id INTEGER, item TEXT, "
                "quality INTEGER, variant INTEGER, effect_name TEXT, key_price_ref REAL, "
                "scraped_at TEXT, n_buy_page INTEGER, n_sell_page INTEGER, book_json TEXT)")
    con.execute("CREATE TABLE snapshot_baseline (snapshot_id INTEGER, buy_total_keys REAL, "
                "sell_total_keys REAL)")
    for p in range(60):
        t = (T0 + pd.Timedelta(hours=p)).isoformat()
        con.execute("INSERT INTO passes (id, started_at) VALUES (?, ?)", (p + 1, t))
        for item, lvl in (("Riser", 1.0 if p < 30 else 1.10), ("Flat", 2.0), ("Faller", 1.0 - 0.002 * p)):
            con.execute("INSERT INTO snapshots (pass_id, item, quality, variant, key_price_ref, "
                        "scraped_at, book_json) VALUES (?, ?, 6, 0, 60.0, ?, ?)",
                        (p + 1, item, t, json.dumps(book(lvl, 0.01))))
    con.commit()
    con.close()
    f = fc.forecast_frame(db, horizons=(12,), cache_dir=cache)
    check("one row per item per pass", len(f), 180)
    check("every forecast feature is a column",
          [c for c in fc.FORECAST_FEATURES if c not in f.columns], [])
    r = f[(f.item == "Flat")].iloc[0]
    check("bid_med = the median fenced bid (2.0, 1.98, ..., 1.92 -> 1.96)", r.bid_med, 1.96, tol=1e-9)
    check("ask1_bot = the cheapest bot ask", r.ask1_bot, 2.02, tol=1e-9)
    ri = f[f.item == "Riser"].set_index("pass_id")
    check("Riser 6 h before its +10% step: up within 12 h", ri.loc[25, "up_12h"], 1.0)
    check("Riser early on: no rise within 12 h", ri.loc[5, "up_12h"], 0.0)
    check("Riser 6 h before: sold into the new 1.10 best bid (bought at the 1.01 ask)",
          ri.loc[25, "ret_12h"], 1.10 / 1.01 - 1, tol=1e-9)
    check("last 12 h are censored", bool(f[f.pass_id > 49].up_12h.isna().all()), True)
    check("bot features are joined (5 bidders, unregistered)", ri.loc[10, "bot_b_sh_human"], 1.0)
    check("the caches went to the temp dir",
          sorted(x.split("_v")[0] for x in os.listdir(cache) if not x.endswith(".stamp.json")),
          ["bot_book", "bot_obs", "panel"])
    check("BOOK_BOT_FEATURES all present", all(c in f.columns for c in BOOK_BOT_FEATURES), True)

    from engine.screens import dashboard
    data = dashboard.build(db, horizon=2, use_cache=False, forecast=True)
    fcp = data["forecast"]
    check("dashboard --forecast: a pick row per latest-pass universe item", len(fcp["picks"]), 3)
    check("... each pick points at a dashboard series",
          all(0 <= r[0] < len(data["series"]) for r in fcp["picks"]), True)
    check("... and carries p_up, exp_ret, BUY and its drivers",
          all(len(r) == 10 and isinstance(r[3], bool) and isinstance(r[9], list) for r in fcp["picks"]),
          True)
    check("... and no forward test before the recipe's freeze date has a labelled pass",
          fcp["forward"], None)
    check("the plain dashboard has no forecast payload",
          dashboard.build(db, horizon=2, use_cache=False)["forecast"], None)


def test_pick():
    print("pick (the buy rule simulate and the live picker share) / the picks screen")
    from engine.screens import picks
    t = T0 + pd.Timedelta(hours=5)
    d = pd.DataFrame({"item": ["A", "B", "C", "D", "E", "F", "A"],
                      "quality": [6, 6, 6, 6, 11, 6, 11], "variant": 0, "pass_id": 5, "t": t,
                      "label": list("ABCDEFa"),
                      "exp_ret": [0.05, 0.04, 0.03, 0.005, 0.02, 0.06, 0.01 + 1e-9],
                      "ask1": [1.0, 1.0, 1.0, 1.0, 1.0, 0.05, 1.0], "p_up": 0.5,
                      "bid1": 0.98, "bid_med": 0.97, "spread": 0.02, "sell_at": 1.04, "drivers": "x"})
    d["buy"] = (d.exp_ret > fc.MIN_EXP_RET) & (d.ask1 >= fc.MIN_PRICE)   # as predict_latest flags
    got = lambda **kw: list(fc.pick(d, **kw).label)
    check("best exp_ret first; under the bar (D) and under MIN_PRICE (F) out", got(), list("ABCEa"))
    check("at most k", got(k=2), list("AB"))
    check("a held series is skipped, the next one takes its place",
          got(held={("A", 6, 0)}, k=2), list("BC"))
    check("quality is part of the series (A Strange is not A Unique)", got(held={("A", 6, 0)})[-1], "a")
    dup = pd.concat([d, d.iloc[[0]].assign(exp_ret=0.045)])
    check("never two of one series in one pass", list(fc.pick(dup).label).count("A"), 1)

    model = dict(min_exp_ret=fc.MIN_EXP_RET, min_price=fc.MIN_PRICE)
    out = picks.build(d, held=[("B", 6, 0)], k=3, rule=model, now=t + pd.Timedelta(minutes=20))
    check("picks.build: not stale 20 min after the pass", out["stale"], False)
    check("... the agent's list", [p["item"] for p in out["picks"]], ["A", "C", "E"])
    check("... over the bar counted before k and held", out["n_over_bar"], 5)
    check("... JSON a browser/Node can parse (no NaN, plain types)",
          json.loads(json.dumps(picks.build(d.assign(bid_med=np.nan), rule=model, now=t), allow_nan=False))
          ["picks"][0]["bid_med"], None)
    old = picks.build(d, now=t + pd.Timedelta(hours=3))
    check("... a 3 h old pass is stale and picks nothing", (old["stale"], old["picks"]), (True, []))

    # the agent's rule (AGENT_RULE): p_up >= 0.65 alone, 0.1 <= ask < 2 keys, best p_up first
    a = d.assign(p_up=[0.71, 0.9, 0.64, 0.8, 0.75, 0.95, 0.66],
                 ask1=[0.5, 2.0, 0.5, 0.3, 0.2, 0.05, 1.99], exp_ret=-0.01)
    check("agent rule: best p_up first, < 0.65 (C), >= 2 keys (B) and < 0.1 key (F) out, exp_ret ignored",
          list(fc.pick(a, **fc.AGENT_RULE).label), list("DEAa"))
    out = picks.build(a, held=[("D", 6, 0)], k=2, now=t + pd.Timedelta(minutes=20))
    check("picks.build defaults to the agent's rule", ([p["item"] for p in out["picks"]], out["n_over_bar"]),
          (["E", "A"], 4))


def main():
    test_labels()
    test_drop_misreads()
    test_no_lookahead()
    test_blocks()
    test_walk_forward()
    test_saved_model()
    test_pick()
    test_end_to_end()
    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

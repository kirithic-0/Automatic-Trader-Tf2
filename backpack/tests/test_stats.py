# -*- coding: utf-8 -*-
"""Offline test for engine.stats (the panel's per-pass stats cache and drift) and the panel
screen's payload, over a small temp db.

    python test_stats.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import os as _os
import json
import math
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

from engine import stats as S
from engine.panel import write_cache

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True):
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": 0, "ref": 0, "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False, "id": f"440_{sid}"}


def book(top):
    buys = [L(str(i), round(top - 0.001 * i, 4)) for i in range(1, 8)]
    sells = [L(str(i + 10), round(top * 1.03 + 0.001 * i, 4)) for i in range(6)]
    return json.dumps({"buyOrders": buys, "sellOrders": sells})


def make(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, pass_id INT, item TEXT, quality INT, "
                "variant INT, effect_name TEXT, scraped_at TEXT, book_json TEXT, n_buy_total INT, "
                "n_sell_total INT, n_buy_page INT, n_sell_page INT, key_price_ref REAL)")
    con.execute("CREATE TABLE passes (id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT)")
    con.execute("CREATE TABLE snapshot_baseline (snapshot_id INT, buy_total_keys REAL, sell_total_keys REAL)")
    con.commit()
    con.close()


def add_pass(path, pid, bids, minutes=5):
    """bids: {item: top bid}; one Unique book each, the pass at 2026-10-01 + pid hours."""
    t = pd.Timestamp("2026-10-01T00:00Z") + pd.Timedelta(hours=pid)
    con = sqlite3.connect(path)
    con.execute("INSERT OR REPLACE INTO passes VALUES (?, ?, ?)",
                (pid, t.isoformat(), (t + pd.Timedelta(minutes=minutes)).isoformat()))
    for item, b in bids.items():
        con.execute("INSERT INTO snapshots (pass_id, item, quality, variant, effect_name, scraped_at, book_json, "
                    "n_buy_total, n_sell_total, n_buy_page, n_sell_page, key_price_ref) "
                    "VALUES (?, ?, 6, NULL, NULL, ?, ?, ?, 6, ?, 6, 60.0)",
                    (pid, item, t.isoformat(), book(b), 40 if item == "A" else 7, 30 if item == "A" else 7))
    con.commit()
    con.close()


def test_pure():
    print("bins, PSI, the atomic cache write")
    check("edges: deciles, ties merged", [round(e, 6) for e in S.edges_of([1, 1, 1, 1, 1, 1, 1, 1, 2, 3])], [1.0, 1.2, 2.1])
    check("edges: no values", S.edges_of([np.nan, np.inf]), [])
    check("psi: same distribution = 0", S.psi([10, 20, 30], [1, 2, 3]), 0.0)
    check("psi: a moved distribution is drift", S.status(S.psi([0, 0, 100], [100, 0, 0])), "drift")
    check("psi: empty side = no reading", math.isnan(S.psi([0, 0], [1, 1])), True)
    check("status bands", [S.status(v) for v in (0.05, 0.1, 0.3, float("nan"))], ["ok", "watch", "drift", "none"])
    df = pd.DataFrame({"pass_id": [1, 1, 1, 2, 2], "x": [0.0, 1.0, np.nan, 5.0, 5.0]})
    s = S.summarise(df, ["x"], {"x": [0.5, 2.0]}, "g")
    check("summarise: rows, missing", s[["pass_id", "n", "nulls"]].values.tolist(), [[1, 3, 1], [2, 2, 0]])
    check("summarise: bins (below / between / above the edges)", s[["h0", "h1", "h2"]].values.tolist(), [[1, 1, 0], [0, 0, 2]])
    check("summarise: median", s.p50.tolist(), [0.5, 5.0])
    d = tempfile.mkdtemp()
    p = _os.path.join(d, "c.parquet")
    write_cache(df, p)
    check("write_cache: written whole, no temp file left", (len(pd.read_parquet(p)), _os.listdir(d)), (5, ["c.parquet"]))


def fake_stats(days=10):
    """A Stats with a stable column `x` and a column `y` that moves on the last day."""
    rows, passes = [], []
    for d in range(days):
        for h in (0, 12):
            pid = d * 2 + h // 12 + 1
            passes.append({"pass_id": pid, "t": pd.Timestamp("2026-10-01T00:00Z") + pd.Timedelta(days=d, hours=h)})
            for col, hist in (("x", [10, 10, 10]), ("y", [10, 10, 10] if d < days - 1 else [0, 0, 30])):
                r = {"group": "g", "col": col, "pass_id": pid, "n": 30, "nulls": 0, "mean": 1.0,
                     **{q: float(d if col == "y" else 1) for q in S.QCOLS}, **{c: 0 for c in S.HCOLS}}
                r.update({"h0": hist[0], "h1": hist[1], "h2": hist[2]})
                rows.append(r)
    return S.Stats(pd.DataFrame(rows), pd.DataFrame(passes), {"edges": {}, "watermark": {}, "built": {}})


def test_drift():
    print("drift: now vs reference, per day")
    st = fake_stats()
    last = pd.Timestamp("2026-10-10T00:00Z")
    tab = {r["col"]: r for r in S.drift_table(st, "g", (last, last + pd.Timedelta(days=1)),
                                              (last - pd.Timedelta(days=7), last))}
    check("the moved column drifted", tab["y"]["status"], "drift")
    check("the stable column did not", (tab["x"]["status"], tab["x"]["psi"]), ("ok", 0.0))
    check("worst first", S.drift_table(st, "g", (last, last + pd.Timedelta(days=1)), (last - pd.Timedelta(days=7), last))[0]["col"], "y")
    check("medians now / ref", (tab["y"]["p50_now"], tab["y"]["p50_ref"]), (9.0, 5.0))
    h = S.daily_psi(st, "g")
    z = dict(zip(h["cols"], np.asarray(h["z"])))
    check("heatmap: one column a day", len(h["days"]), 10)
    check("heatmap: the first day has no reference", math.isnan(z["x"][0]), True)
    check("heatmap: y drifts only on its last day", [S.status(v) for v in z["y"][1:]], ["ok"] * 8 + ["drift"])


def test_refresh():
    print("refresh over a temp db: per-pass table, incremental summaries, the page payload")
    d = tempfile.mkdtemp()
    db, cache = _os.path.join(d, "t.db"), _os.path.join(d, "cache")
    make(db)
    add_pass(db, 1, {"A": 0.10, "B": 0.20, "C": 0.30}, minutes=4)
    add_pass(db, 2, {"A": 0.11, "B": 0.20, "C": 0.30})
    add_pass(db, 3, {"A": 0.11, "B": 0.20})
    add_pass(db, 4, {"A": 0.11, "B": 0.198, "C": 0.30})
    st, panel = S.refresh_scrape(db, cache_dir=cache)
    p = st.passes.set_index("pass_id")
    check("books per pass", p.books.tolist(), [3, 3, 2, 3])
    check("pass minutes", p.minutes.tolist()[0], 4.0)
    check("items gone / back", (p.gone.tolist()[1:], p.new.tolist()[1:]), ([0.0, 1.0, 0.0], [0.0, 0.0, 1.0]))
    check("breadth, pass 2: A up 10%, B and C flat", (round(p.up[2], 4), p.down[2]), (round(1 / 3, 4), 0.0))
    check("breadth, pass 4: B down 1%", round(p.down[4], 4), round(1 / 3, 4))
    check("index: chained average log move (A: 0.099 -> 0.109)", round(p.idx[2], 4),
          round(100 * math.exp(math.log(0.109 / 0.099) / 3), 4))
    check("capped books (A's side came back full)", round(p.capped_buy[1], 4), round(1 / 3, 4))
    check("listings summed", p.buy_total[1], 54.0)
    check("summaries: every pass of every scrape column",
          len(st.cols), 4 * len(S.SCRAPE_COLS))
    check("cached: load reads it back", len(S.load(db, cache).cols), len(st.cols))

    add_pass(db, 5, {"A": 0.12, "B": 0.20})                        # a pass still being written
    st, _ = S.refresh_scrape(db, cache_dir=cache)
    n5 = lambda st: int(st.cols[(st.cols.col == "bid1") & (st.cols.pass_id == 5)].n.iloc[0])
    check("a new pass is summarised", n5(st), 2)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO snapshots (pass_id, item, quality, variant, scraped_at, book_json, n_buy_total, "
                "n_sell_total, n_buy_page, n_sell_page, key_price_ref) VALUES (5, 'C', 6, NULL, '', ?, 7, 6, 7, 6, 60)",
                (book(0.30),))
    con.commit()
    con.close()
    st, _ = S.refresh_scrape(db, cache_dir=cache)
    check("... and again once its last book arrives", n5(st), 3)
    check("... with no duplicate rows", int(st.cols.duplicated(["group", "col", "pass_id"]).sum()), 0)
    check("watermark = the newest snapshot id", st.meta["watermark"]["scrape"], S.watermark(db))

    from engine.screens import panel as screen
    body = json.dumps(screen.payload(st, db), allow_nan=False)
    out = json.loads(body)
    check("payload: strict JSON, every pass", len(out["passes"]["t"]), 5)
    check("payload: no features yet", out["features"], None)
    check("payload: a drift row per scrape column", len(out["scrape"]["table"]), len(S.SCRAPE_COLS))


if __name__ == "__main__":
    test_pure()
    test_drift()
    test_refresh()
    print("\nFAILED: " + ", ".join(fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)

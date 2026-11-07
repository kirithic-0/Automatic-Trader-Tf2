# -*- coding: utf-8 -*-
"""Offline test for the engine pipeline over a REAL temp trading.db.

Rows are written through snapshot_store (the same writer the scraper uses), then
read back through the engine's interface: load_panel (scope, pricedb pairing,
the incremental cache and its rebuild on a reset db), label_events (the step
rule, the gap rule, censored labels), build_features (no look-ahead), and the
signals tail/backtest edges, the dashboard payload, and the --serve book API.

    python test_engine_pipeline.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
_paths.use_collector()
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import snapshot_store as ss
from engine import FEATURES, build_features, label_events, load_panel
from engine.book import READ_KEY
from engine.screens import dashboard
from engine.signals import backtest, feature_lifts, select_signals

fails = []
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def check(name, got, want, tol=1e-9):
    if isinstance(want, float) and math.isnan(want):
        ok = got is not None and isinstance(got, float) and math.isnan(got)
    elif isinstance(want, float):
        ok = got is not None and not (isinstance(got, float) and math.isnan(got)) \
            and abs(got - want) <= tol
    else:
        ok = got == want
    print("  %-4s %-58s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def row(bid, quality=6, variant=0, at=None):
    """A classifieds row whose best fenced bid is `bid` (3 agreeing bots)."""
    return {"variant": variant, "quality": quality, "scrapedAt": at.isoformat(),
            "url": "https://backpack.tf/classifieds?x",
            "buyOrders": [{"steamId": f"b{i}", "price": {"keys": bid * f},
                           "listingType": "bot"} for i, f in enumerate((1.0, 0.99, 0.98))],
            "sellOrders": [{"steamId": f"s{i}", "price": {"keys": bid * f},
                            "listingType": "bot"} for i, f in enumerate((1.1, 1.12, 1.15))]}


def write(db, series, hours):
    """series: {item: [bid per pass or None]}; one pass per entry of `hours`."""
    ids = {}
    for k, h in enumerate(hours):
        at = T0 + timedelta(hours=h)
        pid = ss.start_pass(db, started_at=at.isoformat())
        for item, bids in series.items():
            if k < len(bids) and bids[k] is not None:
                ids[(item, k)] = ss.insert_snapshot(db, item, row(bids[k], at=at), pass_id=pid)
    return ids


def at(frame, item, k):
    s = frame[frame.item == item].reset_index(drop=True)
    return s.iloc[k]


def main():
    tmp = tempfile.mkdtemp()
    try:
        dbp, cache = os.path.join(tmp, "t.db"), os.path.join(tmp, "cache")
        db = ss.connect(dbp)
        hours = list(range(10))
        #          pass:  0     1     2     3     4     5     6     7     8     9
        ramp = [1.00, 1.00, 1.05, 1.05, 1.10, 1.00, 1.00, 1.00, 1.00, 1.00]
        # p2: +5% and holds -> step.  p4: +4.8% but p5 drops -> no step.
        flat = [1.00] * 10
        ids = write(db, {"Ramp": ramp, "Flat": flat}, hours)
        ss.insert_snapshot(db, "Unusual Hat", row(5.0, quality=5, variant=14, at=T0),
                           pass_id=1)
        ss.insert_baseline(db, {"snapshot_id": ids[("Ramp", 0)], "fetched_at": "x",
                                "buy_total_keys": 0.8, "sell_total_keys": 1.2})

        print("panel")
        p = load_panel(dbp, cache_dir=cache)
        check("Unusual (quality 5) out of scope", int((p.quality == 5).sum()), 0)
        check("one row per series per pass", len(p), 20)
        check("bid1 read through the rules", float(at(p, "Ramp", 2).bid1), 1.05)
        check("pricedb paired by snapshot id", float(at(p, "Ramp", 0).pdb_buy), 0.8)
        check("unpaired snapshot -> NaN pdb", float(at(p, "Ramp", 1).pdb_buy), float("nan"))
        check("label", at(p, "Ramp", 0).label, "Ramp (Unique)")
        check("cache file written", os.path.exists(os.path.join(cache, f"panel_v{READ_KEY}.parquet")), True)

        print("cache: incremental append, rebuild on a reset db")
        pid = ss.start_pass(db, started_at=(T0 + timedelta(hours=10)).isoformat())
        ss.insert_snapshot(db, "Flat", row(1.0, at=T0 + timedelta(hours=10)), pass_id=pid)
        check("new snapshot appended to the cached panel",
              len(load_panel(dbp, cache_dir=cache)), 21)
        db2p = os.path.join(tmp, "reset.db")
        db2 = ss.connect(db2p)
        write(db2, {"Other": [2.0, 2.0]}, [0, 1])
        p2 = load_panel(db2p, cache_dir=cache)
        check("reset db under the cache -> rebuilt, no stale rows",
              sorted(p2.item.unique().tolist()), ["Other"])

        print("events")
        f = label_events(load_panel(dbp, use_cache=False), horizons=(2,))
        check("+5% that holds -> step", bool(at(f, "Ramp", 2).step), True)
        check("+4.8% that falls back next pass -> no step", bool(at(f, "Ramp", 4).step), False)
        check("r_bid1 at p4", float(at(f, "Ramp", 4).r_bid1), 1.10 / 1.05 - 1, tol=1e-6)
        check("flat series never steps", int(f[f.item == "Flat"].step.sum()), 0)
        check("ev_2h at p0 (step at p2, t+2h)", float(at(f, "Ramp", 0).ev_2h), 1.0)
        check("ev_2h at p2 excludes its own step", float(at(f, "Ramp", 2).ev_2h), 0.0)
        check("ev_2h censored when t+2h is unobserved", float(at(f, "Ramp", 8).ev_2h),
              float("nan"))
        check("first step above everything before it -> new high",
              bool(at(f, "Ramp", 2).new_high), True)
        check("nh_2h at p0 sees that new high", float(at(f, "Ramp", 0).nh_2h), 1.0)
        check("bid1_max24 excludes the current pass", float(at(f, "Ramp", 2).bid1_max24), 1.0)

        bdb = os.path.join(tmp, "bounce.db")
        bd = ss.connect(bdb)
        write(bd, {"Bounce": [1.10, 1.00, 1.05, 1.05, 1.05]}, [0, 1, 2, 3, 4])
        b = label_events(load_panel(bdb, use_cache=False), horizons=(1,))
        check("a recovery is a step...", bool(at(b, "Bounce", 2).step), True)
        check("...but not a new high (1.10 seen 2h earlier)",
              bool(at(b, "Bounce", 2).new_high), False)
        check("ev_1h at p1 = 1, nh_1h at p1 = 0",
              (float(at(b, "Bounce", 1).ev_1h), float(at(b, "Bounce", 1).nh_1h)), (1.0, 0.0))

        gdb = os.path.join(tmp, "gap.db")
        gd = ss.connect(gdb)
        write(gd, {"Gappy": [1.00, 1.10, 1.10]}, [0, 10, 11])
        g = label_events(load_panel(gdb, use_cache=False))
        check("a rise across a 10h gap is not a step", bool(at(g, "Gappy", 1).step), False)
        check("...and has no r_bid1", float(at(g, "Gappy", 1).r_bid1), float("nan"))

        print("features (no look-ahead)")
        x = build_features(label_events(load_panel(dbp, use_cache=False), horizons=(2,)))
        check("hours_since_step before any step", float(at(x, "Ramp", 2).hours_since_step),
              float("nan"))
        check("hours_since_step one pass after", float(at(x, "Ramp", 3).hours_since_step), 1.0)
        check("steps_72h excludes the current row", float(at(x, "Ramp", 2).steps_72h), 0.0)
        check("steps_72h counts earlier rows", float(at(x, "Ramp", 5).steps_72h), 1.0)
        check("bid_chg_6h vs 6h back", float(at(x, "Ramp", 6).bid_chg_6h), 0.0)
        check("bid_vs_24h_high at p5 = 1.00/1.10", float(at(x, "Ramp", 5).bid_vs_24h_high),
              1.0 / 1.1, tol=1e-9)
        check("bid_chg_6h with no pass 6h back -> NaN",
              float(at(x, "Ramp", 4).bid_chg_6h), float("nan"))
        check("spread = (ask1-bid1)/bid1", float(at(x, "Flat", 0).spread), 0.1, tol=1e-3)
        check("bid_vs_pdb", float(at(x, "Ramp", 0).bid_vs_pdb), 1.0 / 0.8, tol=1e-9)
        check("market_up_share at p2 (Ramp up, Flat not)",
              float(at(x, "Ramp", 2).market_up_share), 0.5)

        print("signals")
        rng = np.random.default_rng(0)
        n = 20000              # 2,000-row tails: enough that 23 noise features stay < 1.5x
        s = pd.DataFrame({"pass_id": np.repeat(np.arange(200), 100),
                          "t": pd.Timestamp(T0) + pd.to_timedelta(np.repeat(np.arange(200), 100), "h")})
        for col in FEATURES:
            s[col] = rng.random(n)
        s["top_bot"] = (rng.random(n) < 0.95).astype(float)          # 95% ones
        s["ev_24h"] = ((s.gap2 > 0.9) | ((s.top_bot == 0) & (rng.random(n) < 0.8))).astype(float)
        lifts, base = feature_lifts(s, "ev_24h")
        tb = lifts.set_index("feature").loc["top_bot"]
        check("binary feature: low tail falls back to strict (not a bot)",
              bool(tb.low_strict) and tb.low_n > 0, True)
        picked = {x.feature: x.side for x in select_signals(s, "ev_24h")}
        check("planted gap2 signal picked, high side", picked.get("gap2"), "high")
        check("planted top_bot signal picked, low side", picked.get("top_bot"), "low")
        check("noise features not picked", set(picked) <= {"gap2", "top_bot"}, True)
        check("backtest on 3 passes -> None", backtest(s[s.pass_id < 3], "ev_24h", 24), None)
        bt = backtest(s, "ev_24h", 1)
        check("backtest precision@10 beats base on planted signal",
              bt["precision"][10][0] > bt["base"], True)

        print("dashboard payload")
        data = dashboard.build(dbp, horizon=2, use_cache=False)
        check("one series entry per item", [x["l"] for x in data["series"]],
              ["Flat (Unique)", "Ramp (Unique)"])
        ramp_s = data["series"][1]
        check("step markers: Ramp's p2 is a new high (code 2)", ramp_s["e"], [[2, 2]])
        check("latest-pass features carried for every feature",
              len(ramp_s["fv"]) == len(ramp_s["fp"]) == len(FEATURES), True)
        check("both event definitions present", sorted(data["defs"]), ["any", "new"])
        check("payload is JSON-serialisable", bool(json.dumps(data)), True)
        check("audit covers the latest pass's books", len(data["audit"]["books"]), 1)
        check("pass lookup: one (pass_id, hours) per pass", len(data["meta"]["passes"]), 11)

        print("--serve: /api/book over a real local server")
        httpd = dashboard.make_server("<html></html>", dbp, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            pid2 = int(at(label_events(load_panel(dbp, use_cache=False)), "Ramp", 2).pass_id)
            q = urllib.parse.urlencode({"item": "Ramp", "q": 6, "v": 0, "pass": pid2})
            bk = json.load(urllib.request.urlopen(f"{base}/api/book?{q}"))
            check("book at Ramp p2: bid1 1.05", bk["prices"]["bid1"], 1.05)
            check("...every buy explained", [r["reason"] for r in bk["buys"]], ["kept"] * 3)
            check("...with snapshot provenance", bk["snapshot"]["source"], "classifieds")

            def status(url):
                try:
                    return urllib.request.urlopen(url).status
                except urllib.error.HTTPError as err:
                    return err.code
            check("unknown snapshot -> 404", status(f"{base}/api/book?item=Nope&q=6&v=0&pass=1"), 404)
            check("malformed query -> 400", status(f"{base}/api/book?item=Ramp&q=six"), 400)
            check("page served at /", status(base + "/"), 200)
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("  %d FAILED: %s" % (len(fails), ", ".join(fails)))
        sys.exit(1)
    print("  all checks passed")


main()

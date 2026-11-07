"""One interactive HTML page over everything the engine computes.

Tabs: watchlist (ratchet-watch), signals (every feature's lift + the backtest),
events, movers, climbers, book audit (what each read rule dropped in the latest
pass), and an item explorer (chart of best bid/ask/pricedb with step markers,
every feature for the item's latest pass, and -- in --serve mode -- the full
buy/sell order book behind any point, each listing marked kept or dropped and
why). A toggle switches every view between "any step" and "new high". With
--forecast, a Forecast tab: the Stage-5 model's picks for the latest pass and its
walk-forward track record (engine.forecast; trains the models, ~1-3 min more).

    python -m engine dashboard                  # -> engine_dashboard.html (static)
    python -m engine dashboard --serve --open   # live: order books + every listing per pass
    python -m engine dashboard --forecast       # + the Forecast tab
    python -m engine dashboard --db trading     # the frozen classifieds history instead

--serve runs a local server on 127.0.0.1 only; the db (trading_app.db by default) is
opened read-only. The static file works without it, minus the per-point order books and
the explorer's listing dots / fence medians (`/api/history`).
"""
import json
import os
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from engine import build_frame, label_col
from engine.book import (FENCE_DEPTH, RULES_VERSION, explain_book, fence_buys, fence_median,
                         fence_sells, valid_buys, valid_sells)
from engine.events import STEP_PCT
from engine.features import FEATURE_DOCS, FEATURES
from engine.panel import (SERIES, book_decoder, cache_dir_for, connect_ro, latest_pass, plain_sql,
                          resolve_db, universe_sql)
from engine.screens._common import parser, qualities
from engine.screens.climbers import rank_climbers
from engine.signals import backtest, feature_lifts, fired, score, select_signals

_TEMPLATE = Path(__file__).with_name("dashboard.html")


def _num(x, nd=4):
    return None if x is None or x != x or np.isinf(x) else round(float(x), nd)


def _definition(f, lab, horizon, frac, min_lift, top):
    """Lifts, picked signals, backtest and the latest-pass watchlist for one label."""
    known = f[f[lab].notna()]
    lifts, base = feature_lifts(known, lab, frac)
    sigs = select_signals(known, lab, frac, min_lift)
    side = {s.feature: s.side for s in sigs}
    bt = backtest(f, lab, horizon, frac=frac, min_lift=min_lift)

    out = {
        "base": _num(base), "n_known": int(len(known)),
        "signals": [{"f": s.feature, "side": s.side, "lift": _num(s.lift, 3),
                     "cut": _num(s.cut, 6), "strict": bool(s.strict)} for s in sigs],
        "lifts": [{"f": r.feature, "cover": _num(r.coverage, 3),
                   "hr": _num(r.high_rate), "hl": _num(r.high_lift, 3),
                   "lr": _num(r.low_rate), "ll": _num(r.low_lift, 3),
                   "chosen": side.get(r.feature, "")} for r in lifts.itertuples()],
        "backtest": None, "watch": [], "score": None,
    }
    if bt is not None:
        out["backtest"] = {
            "split": bt["split"].isoformat(), "base": _num(bt["base"]),
            "passes": int(bt["n_test_passes"]),
            "signals": [f"{s.feature} ({s.side})" for s in bt["signals"]],
            "prec": [[k, _num(p), _num(l, 3)] for k, (p, l) in bt["precision"].items()],
        }
    if sigs:
        sc = score(f, sigs)
        out["score"] = sc                                   # consumed by build(), not dumped
        now = f[f.pass_id == latest_pass(f)].assign(wscore=sc)
        for _, r in now.sort_values("wscore", ascending=False).head(top).iterrows():
            out["watch"].append([int(r.sid), _num(r.wscore, 3), fired(r, sigs)])
    return out


def _depth_check(valid, fence):
    """The fence over the top FENCE_DEPTH (the rule) vs over every valid listing (the old
    rule): both medians, and how many listings the two keep differently."""
    m, m_all = fence_median(valid), fence_median(valid, None)
    a = {id(o) for _, o in fence(valid) or []}
    b = {id(o) for _, o in fence(valid, None) or []}
    return [_num(m), _num(m_all), len(a ^ b)]


def _audit(db, pass_id, qs, sid_of):
    """What each read rule dropped in one pass: totals per side and reason, and
    one row per book. Keeps the last snapshot per series, as the panel does."""
    con = connect_ro(db)
    try:
        decode = book_decoder(con)
        rows = [(it, q, v, nb, ns, decode(bj)) for it, q, v, nb, ns, bj in con.execute(
            "SELECT item, quality, variant, n_buy_page, n_sell_page, book_json FROM snapshots "
            f"WHERE pass_id = ? AND quality IN ({','.join(map(str, qs))}) AND {universe_sql(con)} ORDER BY id",
            (pass_id,))]
    finally:
        con.close()
    latest = {(it, q, v): (nb, ns, bj) for it, q, v, nb, ns, bj in rows}
    totals = {"buy": Counter(), "sell": Counter()}
    books = []
    for key, (nb, ns, bj) in latest.items():
        if key not in sid_of:
            continue
        e = explain_book(bj)
        drops = Counter()
        for side, lst in (("buy", e["buys"]), ("sell", e["sells"])):
            for r in lst:
                totals[side][r["reason"]] += 1
                if r["reason"] != "kept":
                    drops[f"{side}: {r['reason']}"] += 1
        books.append([sid_of[key], e["buy"]["n_raw"], e["buy"]["n_kept"],
                      e["sell"]["n_raw"], e["sell"]["n_kept"], nb, ns, dict(drops),
                      _depth_check(valid_buys(bj), fence_buys),
                      _depth_check(valid_sells(bj), fence_sells)])
    return {"pass": pass_id, "totals": {k: dict(v) for k, v in totals.items()},
            "books": books}


def _forecast(db, qs, sid_of, use_cache, backtest=True, retrain=False):
    """The Forecast tab: the latest pass's picks, and the walk-forward track record."""
    from engine import forecast as fc
    ff = fc.forecast_frame(db, use_cache=use_cache)
    ff = ff[ff.quality.isin(qs)]
    picks = fc.predict_latest(ff, model_dir=cache_dir_for(db), retrain=retrain)
    out = {"horizon": fc.HORIZON, "target": fc.TARGET, "take_profit": fc.TAKE_PROFIT,
           "max_spread": fc.MAX_SPREAD, "min_exp_ret": fc.MIN_EXP_RET, "min_price": fc.MIN_PRICE,
           "base": _num(picks.attrs.get("base_rate")), "n_train": picks.attrs.get("n_train", 0),
           "trained_at": picks.attrs.get("trained_at"),
           "picks": [[sid_of[(r.item, r.quality, r.variant)], _num(r.p_up, 3), _num(r.exp_ret, 4),
                      bool(r.buy), _num(r.bid1), _num(r.bid_med), _num(r.ask1), _num(r.spread, 4),
                      _num(r.sell_at), [d for d in r.drivers.split(", ") if d]]
                     for r in picks.itertuples() if (r.item, r.quality, r.variant) in sid_of],
           "folds": [], "portfolio": None, "reliability": []}
    if not backtest:
        return out
    rep = fc.walk_forward(ff)
    for _, r in rep["folds"].iterrows():
        out["folds"].append([r.test_start.isoformat(), r.test_end.isoformat(), int(r.n_train),
                             int(r.n_test), _num(r["base"]), _num(r["auc"], 3),
                             _num(r["prec@5"], 3), _num(r["prec@10"], 3), _num(r["ret@10"], 4),
                             _num(r["ret_spread@10"], 4), _num(r["ret_all"], 4)])
    if len(rep["preds"]):
        p, up = rep["preds"], fc.up_col(fc.HORIZON)
        rel = (p.groupby(pd.cut(p.p_up, [0, .1, .2, .3, .4, .5, .6, 1.0]), observed=True)
                .agg(n=(up, "size"), said=("p_up", "mean"), happened=(up, "mean")))
        out["reliability"] = [[_num(r.said, 3), _num(r.happened, 3), int(r.n)] for r in rel.itertuples()]
        tr = fc.simulate(p)
        if len(tr):
            day = tr.groupby(tr.t.dt.strftime("%m-%d")).ret
            out["portfolio"] = {"n": int(len(tr)), "win": _num((tr.ret > 0).mean(), 3),
                                "mean": _num(tr.ret.mean(), 4), "keys": _num(tr.profit_keys.sum(), 3),
                                "days": [[d, int(g.size), _num(g.mean(), 4)] for d, g in day]}
    out["forward"] = _forward(ff, fc)
    return out


def _forward(ff, fc):
    """The forward test: the same recipe on every labelled day after it was frozen
    (fc.RECIPE_FROZEN_AT) -- data none of its choices saw. None until a pass is labelled."""
    rep = fc.walk_forward(ff, since=fc.RECIPE_FROZEN_AT)
    if rep["folds"].empty:
        return None
    tr = fc.simulate(rep["preds"])
    f = rep["folds"]
    return {"since": fc.RECIPE_FROZEN_AT, "passes": int(f.passes.sum()), "auc": _num(f.auc.mean(), 3),
            "ret10": _num(f["ret@10"].mean(), 4), "ret_all": _num(f.ret_all.mean(), 4),
            "n": int(len(tr)), "unknown": int(tr.ret.isna().sum()) if len(tr) else 0,
            "win": _num((tr.ret > 0).mean(), 3) if len(tr) else None,
            "mean": _num(tr.ret.mean(), 4) if len(tr) else None,
            "keys": _num(tr.profit_keys.sum(), 3) if len(tr) else None}


def build(db, horizon=24.0, pct=STEP_PCT, qs=(6, 11), frac=0.10, min_lift=1.5,
          top=150, use_cache=True, forecast=False, retrain=False):
    """The dashboard payload (a JSON-ready dict)."""
    f = build_frame(db, pct=pct, horizons=(horizon,), use_cache=use_cache)
    f = f[f.quality.isin(qs)].reset_index(drop=True)
    f["sid"] = f.groupby(SERIES, sort=False).ngroup()
    t0 = f.t.min()
    f["hrs"] = (f.t - t0).dt.total_seconds() / 3600.0

    defs = {"any": _definition(f, label_col(horizon), horizon, frac, min_lift, top),
            "new": _definition(f, label_col(horizon, True), horizon, frac, min_lift, top)}

    # per series: chart arrays + the latest row's features, pass-percentiles, scores
    pr = f.groupby("pass_id")[FEATURES].rank(pct=True)
    last = f.groupby("sid").tail(1).index
    series = []
    for sid, s in f.groupby("sid", sort=True):
        ev = [[i, 2 if nh else 1] for i, (st, nh) in
              enumerate(zip(s.step.to_numpy(), s.new_high.to_numpy())) if st]
        series.append({
            "l": s.label.iloc[0], "q": int(s.quality.iloc[0]), "it": s.item.iloc[0],
            "v": None if pd.isna(s.variant.iloc[0]) else int(s.variant.iloc[0]),
            "t": [round(h, 2) for h in s.hrs],
            "b": [_num(v) for v in s.bid1], "a": [_num(v) for v in s.ask1],
            "bf": [_num(v) for v in s.bid1_fill],
            "bm": [_num(v) for v in s.bid_med], "am": [_num(v) for v in s.ask_med],
            "pb": [_num(v) for v in s.pdb_buy], "ps": [_num(v) for v in s.pdb_sell],
            "e": ev,
        })
    for i in last:
        sid = int(f.at[i, "sid"])
        series[sid]["fv"] = [_num(f.at[i, c]) for c in FEATURES]
        series[sid]["fp"] = [_num(pr.at[i, c], 3) for c in FEATURES]
        series[sid]["lt"] = round(float(f.at[i, "hrs"]), 2)
        for k in ("any", "new"):
            sc = defs[k]["score"]
            series[sid]["s" + k[0]] = None if sc is None else _num(sc.at[i], 3)
    for k in defs:
        defs[k].pop("score")

    # recent events, newest first
    ev = f[f.step & (f.t >= f.t.max() - pd.Timedelta(hours=72))]
    ev = ev.sort_values(["t", "r_bid1"], ascending=[False, False])
    events = [[int(r.sid), round(r.hrs, 2), _num(r.bid1 / (1 + r.r_bid1)), _num(r.bid1),
               _num(r.r_bid1), 1 if r.new_high else 0] for r in ev.itertuples()]

    # movers: first -> last fenced top-5 bid for every series
    movers = []
    for sid, s in f[f.bid5.notna()].groupby("sid", sort=False):
        if len(s) < 2 or s.bid5.iloc[0] <= 0:
            continue
        a, b = s.iloc[0], s.iloc[-1]
        movers.append([int(sid), _num(a.bid5), _num(b.bid5),
                       _num((b.bid5 - a.bid5) / a.bid5, 4),
                       _num(max(a.bid_disp, b.bid_disp), 3), int(len(s))])

    climbers = [[int(s.sid.iloc[0]), _num(r["score"], 2), _num(r["med_spread"], 4),
                 _num(r["bid_rise"], 4), _num(r["ask_rise"], 4), r["lead_l"],
                 _num(r["lead_r"], 3), _num(r["bid_last"]), _num(r["ask_last"])]
                for r, s in rank_climbers(f)[:100]]

    lp = latest_pass(f)
    sid_of = {k: int(g.sid.iloc[0]) for k, g in f.groupby(SERIES, sort=False)}
    # the same round() the series times get, so the page can map a point to its pass
    passes = sorted((round(h, 2), int(p)) for p, h in f.groupby("pass_id").hrs.first().items())
    return {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "t0": t0.isoformat(), "last_t": f.t.max().isoformat(), "last_pass": lp,
            "n_snap": int(len(f)), "n_series": int(f.sid.nunique()),
            "n_passes": int(f.pass_id.nunique()), "horizon": horizon, "pct": pct,
            "rules": RULES_VERSION, "depth": FENCE_DEPTH, "n_now": int((f.pass_id == lp).sum()),
            "db": Path(resolve_db(db)).name,
            "n_step": int(f.step.sum()), "n_new": int(f.new_high.sum()),
            "passes": [[p, h] for h, p in passes], "live": False,
        },
        "audit": _audit(db, lp, qs, sid_of),
        "features": [{"f": c, "doc": FEATURE_DOCS[c]} for c in FEATURES],
        "defs": defs, "series": series, "events": events,
        "movers": movers, "climbers": climbers,
        "forecast": _forecast(db, qs, sid_of, use_cache, retrain=retrain) if forecast else None,
    }


def book_at(db, item, quality, variant, pass_id):
    """The explained order book for one series at one pass, or None."""
    con = connect_ro(db)
    try:
        row = con.execute(
            "SELECT id, scraped_at, source, key_price_ref, n_buy_page, n_buy_clean, "
            "n_sell_page, n_sell_clean, book_json FROM snapshots WHERE item = ? AND "
            f"quality = ? AND variant IS ? AND pass_id = ?{plain_sql(con)} ORDER BY id DESC LIMIT 1",
            (item, quality, variant, pass_id)).fetchone()
        book = book_decoder(con)(row[-1]) if row is not None else None
    finally:
        con.close()
    if row is None:
        return None
    sid, scraped, source, kref, nbp, nbc, nsp, nsc, _ = row
    return {"snapshot": {"id": sid, "scraped_at": scraped, "source": source,
                         "key_price_ref": kref, "n_buy_page": nbp, "n_buy_clean": nbc,
                         "n_sell_page": nsp, "n_sell_clean": nsc},
            **explain_book(book)}


# a listing's verdict, one letter for the explorer's dots (anything else = a flag)
_CODE = {"kept": "k", "fence: too high": "h", "fence: too low": "l", "duplicate account": "d",
         "untrusted account": "u", "no median": "n"}


def history_at(db, item, quality, variant):
    """Every stored listing of one series at every pass, with its verdict, plus each
    pass's fence medians (top FENCE_DEPTH, and over all valid listings): the explorer's
    dots and median lines. {"passes": [[pass_id, buy_m, buy_m_all, sell_m, sell_m_all,
    [[side 'b'|'s', keys, code], ...]], ...]}. Keeps the last snapshot per pass."""
    con = connect_ro(db)
    try:
        decode = book_decoder(con)
        last = {}
        for pid, bj in con.execute(
                "SELECT pass_id, book_json FROM snapshots WHERE item = ? AND quality = ? AND "
                f"variant IS ? AND pass_id IS NOT NULL{plain_sql(con)} ORDER BY id", (item, quality, variant)):
            last[pid] = bj
        books = {pid: decode(bj) for pid, bj in last.items()}
    finally:
        con.close()
    out = []
    for pid in sorted(books):
        e = explain_book(books[pid])
        pts = [[side, _num(r["tk"], 5), _CODE.get(r["reason"], "f")]
               for side, rows in (("b", e["buys"]), ("s", e["sells"])) for r in rows
               if r["tk"] is not None]
        out.append([int(pid), _num(e["buy"]["median"]), _num(e["buy"]["median_all"]),
                    _num(e["sell"]["median"]), _num(e["sell"]["median_all"]), pts])
    return {"passes": out}


def make_server(html, db, port):
    """An HTTP server on 127.0.0.1 for the page plus /api/book (port 0 = any free)."""
    page = html.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if u.path == "/api/book":
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    v = None if q.get("v") in (None, "", "null") else int(q["v"])
                    out = book_at(db, q["item"], int(q["q"]), v, int(q["pass"]))
                except (KeyError, ValueError):
                    return self._send(400, b'{"error":"bad query"}', "application/json")
                if out is None:
                    return self._send(404, b'{"error":"no snapshot"}', "application/json")
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                return self._send(200, body, "application/json; charset=utf-8")
            if u.path == "/api/history":
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    v = None if q.get("v") in (None, "", "null") else int(q["v"])
                    out = history_at(db, q["item"], int(q["q"]), v)
                except (KeyError, ValueError):
                    return self._send(400, b'{"error":"bad query"}', "application/json")
                body = json.dumps(out, separators=(",", ":")).encode("utf-8")
                return self._send(200, body, "application/json")
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(html, db, port, open_browser):
    """Serve the page plus /api/book on 127.0.0.1 until Ctrl+C."""
    httpd = make_server(html, db, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"serving {url}  (Ctrl+C to stop; restart to pick up new passes)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--out", default="engine_dashboard.html")
    ap.add_argument("--serve", action="store_true",
                    help="serve live on 127.0.0.1 with per-point order books")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--horizon", type=float, default=24.0, help="H in hours (default 24)")
    ap.add_argument("--pct", type=float, default=STEP_PCT)
    ap.add_argument("--open", action="store_true", help="open the page when written")
    ap.add_argument("--forecast", action="store_true",
                    help="add the Forecast tab (trains the Stage-5 models, ~1-3 min more)")
    ap.add_argument("--retrain", action="store_true",
                    help="Forecast tab: rebuild the day's saved model now (else at 24 h old)")
    args = ap.parse_args(argv)

    data = build(args.db, args.horizon, args.pct, tuple(sorted(qualities(args))),
                 use_cache=not args.no_cache, forecast=args.forecast, retrain=args.retrain)
    data["meta"]["live"] = args.serve
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = _TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", payload)
    if args.serve:
        return serve(html, args.db, args.port, args.open)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {args.out}  ({data['meta']['n_series']} items, "
          f"{os.path.getsize(args.out) / 1e6:.1f} MB)")
    if args.open:
        webbrowser.open(Path(args.out).resolve().as_uri())

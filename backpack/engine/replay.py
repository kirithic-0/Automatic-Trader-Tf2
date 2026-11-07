"""The agent's rules replayed on the stored books -- the paper test (replaces the paper
agent, 2026-10-26; its blueprint is docs/extra/paper-agent.md).

Out-of-sample predictions (engine.forecast.walk_forward: each test day scored by a model
trained only on labels known before it) are picked pass by pass with the agent's own rule
(forecast.pick, AGENT_RULE by default), and each pick is traded on the books the collector
stored, by the agent's own listing rules (engine.listings):

  buy  -- at the pick's pass, the cheapest tradable sell (bot + instant, trusted, fenced, a
          trade URL, an asset id, payable in metal); skipped if it costs more than the
          pass's ask1 (the pre-send check, engine.recheck);
  sell -- the first later pass whose best tradable bid (<= MAX_JUMP x entry) is >= entry x
          (1 + TAKE_PROFIT); else from HORIZON the best tradable bid, waiting up to
          EXIT_WAIT_H more; no bid by then = `stuck`.

A position holds its series until it exits, so a series is never bought twice at once.
What it cannot test: whether the counterparty accepts (fills are assumed), and the minutes
between a pass and the agent acting. Hourly passes, not the 10-min watch: a take-profit
seen a little later is the same price.

    trades = replay(preds, con)                 # one row per pick: status, entry, exit, ret
    s = summary(trades, days)                   # bought / won / ret a trade / keys
"""
import numpy as np
import pandas as pd

from engine import forecast as fc
from engine import listings as el
from engine.panel import plain_sql

SOLD = ("take_profit", "horizon")
BOUGHT = SOLD + ("stuck", "not known yet")


class Books:
    """One series' stored books from a pass on, decoded on demand and kept."""

    def __init__(self, con):
        from book_codec import Codec
        self.con, self.codec, self._cache = con, Codec.from_con(con), {}
        self.urls = el.trade_urls(con)

    def after(self, item, quality, variant, pass_id, t0, hours):
        """[(pass time, book)] for the series: the pass `pass_id` (started at `t0`), then
        every later pass within `hours` of it."""
        key = (item, int(quality), int(variant), int(pass_id))
        if key not in self._cache:
            hi = (pd.Timestamp(t0) + pd.Timedelta(hours=hours)).isoformat()   # = passes.started_at's form
            db_variant = max(int(variant), 0)
            rows = self.con.execute(
                "SELECT p.started_at, s.book_json, s.pass_id FROM snapshots s JOIN passes p ON p.id = s.pass_id "
                "WHERE s.item = ? AND s.quality = ? AND s.variant = ? AND s.pass_id >= ? AND p.started_at <= ?"
                f"{plain_sql(self.con, 'snapshots', 's')} ORDER BY p.started_at",
                (item, int(quality), db_variant, int(pass_id), hi)).fetchall()
            out = [(pd.Timestamp(t), self.codec.decode(b)) for t, b, pid in rows]
            self._cache[key] = out if rows and rows[0][2] == pass_id else []
        return self._cache[key]


def trade(books, r, horizon=fc.HORIZON, take_profit=fc.TAKE_PROFIT, max_jump=fc.MAX_JUMP,
          wait_h=fc.EXIT_WAIT_H):
    """{status, entry, exit, ret, hours, seller, buyer} for one pick `r` (a preds row)."""
    out = dict(status=None, entry=np.nan, exit=np.nan, ret=np.nan, hours=np.nan, seller=None, buyer=None)
    bs = books.after(r.item, r.quality, r.variant, r.pass_id, r.t, horizon + wait_h + 1)
    if not bs:
        return dict(out, status="no book")
    sells = el.tradable_sells(bs[0][1], books.urls)
    if not sells:
        return dict(out, status="no tradable seller")
    entry = sells[0]["total_keys"]
    out.update(entry=entry, seller=sells[0]["user"])
    if entry > r.ask1 + 1e-9:
        return dict(out, status="seller dearer than ask1")
    t0 = bs[0][0]
    for t, b in bs[1:]:
        h = (t - t0).total_seconds() / 3600
        bids = [x for x in el.tradable_buys(b, books.urls) if x["total_keys"] <= max_jump * entry]
        if not bids:
            continue
        best = bids[0]["total_keys"]
        if (h < horizon and best >= entry * (1 + take_profit) - 1e-9) or h >= horizon:
            return dict(out, status="take_profit" if h < horizon else "horizon", exit=best,
                        ret=best / entry - 1, hours=h, buyer=bids[0]["user"])
    last_h = (bs[-1][0] - t0).total_seconds() / 3600
    return dict(out, status="stuck" if last_h >= horizon + wait_h - 1 else "not known yet")


def replay(preds, con, rule=None, k=fc.MAX_NEW_BUYS, horizon=fc.HORIZON, wait_h=fc.EXIT_WAIT_H, books=None):
    """Pass by pass: pick with `rule` (forecast.pick arguments, default AGENT_RULE) among the
    series not held, trade each pick on the stored books (`books`: a Books, else one on
    `con`). One row per pick."""
    rule = dict(fc.AGENT_RULE if rule is None else rule)
    books = books or Books(con)
    held_until, rows = {}, []
    for pid, d in preds.sort_values("t").groupby("pass_id", sort=False):
        t = d.t.iloc[0]
        held = {s for s, u in held_until.items() if u > t}
        for r in fc.pick(d, held, k, **rule).itertuples():
            x = trade(books, r, horizon=horizon, wait_h=wait_h)
            if x["status"] in BOUGHT:
                hold_h = x["hours"] if x["status"] in SOLD else horizon + wait_h
                held_until[(r.item, r.quality, r.variant)] = t + pd.Timedelta(hours=float(hold_h))
            rows.append(dict(t=t, pass_id=pid, item=r.item, quality=r.quality, variant=r.variant,
                             label=r.label, ask1=r.ask1, p_up=r.p_up, exp_ret=r.exp_ret, **x))
    return pd.DataFrame(rows)


def peak_keys(sold):
    """The most keys tied up at once by the sold trades (entry .. exit)."""
    ev = sorted([(t, e) for t, e in zip(sold.t, sold.entry)]
                + [(t + pd.Timedelta(hours=h), -e) for t, h, e in zip(sold.t, sold.hours, sold.entry)])
    cur = peak = 0.0
    for _, x in ev:
        cur += x
        peak = max(peak, cur)
    return peak


def summary(trades, days):
    """The headline numbers of one replay."""
    if trades.empty:
        return dict(picks=0, bought=0)
    sold = trades[trades.status.isin(SOLD)]
    return dict(picks=len(trades), bought=int(trades.status.isin(BOUGHT).sum()),
                per_day=trades.status.isin(BOUGHT).sum() / max(days, 1e-9),
                skipped=trades[~trades.status.isin(BOUGHT)].status.value_counts().to_dict(),
                stuck=int((trades.status == "stuck").sum()), sold=len(sold),
                win=float((sold.ret > 0).mean()) if len(sold) else np.nan,
                ret=float(sold.ret.mean()) if len(sold) else np.nan,
                worst=float(sold.ret.min()) if len(sold) else np.nan,
                keys=float((sold.ret * sold.entry).sum()),
                take_profit=float((sold.status == "take_profit").mean()) if len(sold) else np.nan,
                hold_h=float(sold.hours.median()) if len(sold) else np.nan,
                peak_keys=peak_keys(sold) if len(sold) else 0.0)

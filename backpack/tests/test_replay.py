# -*- coding: utf-8 -*-
"""Offline test for engine.replay: the agent's rules traded on stored books (the paper test).

    python test_replay.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import sys

import pandas as pd

from engine import forecast as fc
from engine import replay as rp

fails = []
T0 = pd.Timestamp("2026-10-20T00:00:00", tz="UTC")


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True):
    """A listing priced in whole scrap (payable), the asset id from its steam id."""
    scrap = round(keys * 60 * 9)            # a 60 ref key: keys -> whole scrap
    ref = scrap // 9 + (scrap % 9) * 0.11
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": 0, "ref": round(ref, 2), "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False,
            "id": f"440_{17000000000 + int(sid)}"}


def B(ask, bid):
    """A book: three sells from `ask` up, three bids from `bid` down (enough for a fence)."""
    return {"sellOrders": [L("1", ask), L("2", ask * 1.01), L("3", ask * 1.02)],
            "buyOrders": [L("11", bid), L("12", bid * 0.995), L("13", bid * 0.99)]}


class Fake:
    """Books keyed by item: [(hours after the pick, book)]."""

    def __init__(self, series):
        self.series = series
        self.urls = {s: "url" + s for s in ("1", "2", "3", "11", "12", "13")}

    def after(self, item, quality, variant, pass_id, t0, hours):
        return [(T0 + pd.Timedelta(hours=h), b) for h, b in self.series.get(item, []) if h <= hours]


def row(item, ask1, p_up, pass_id=1, t=T0):
    return pd.Series(dict(item=item, quality=6, variant=0, label=item, pass_id=pass_id, t=t, ask1=ask1,
                          p_up=p_up, exp_ret=0.0, spread=0.01))


def main():
    print("replay.trade: one pick on its stored books")
    up = Fake({"A": [(0, B(0.50, 0.49)), (1, B(0.50, 0.50)), (5, B(0.53, 0.521))]})
    one = lambda *a: next(pd.DataFrame([row(*a)]).itertuples())   # a row as replay passes it
    x = rp.trade(up, one("A", 0.50, 0.8))
    check("take-profit the first pass the best tradable bid is >= entry x 1.04",
          (x["status"], round(x["exit"], 3), x["hours"]), ("take_profit", 0.521, 5.0))
    flat = Fake({"A": [(0, B(0.50, 0.49))] + [(h, B(0.50, 0.495)) for h in range(1, 60)]})
    x = rp.trade(flat, one("A", 0.50, 0.8))
    check("never reached: sold to the best bid at the 48 h horizon",
          (x["status"], x["hours"], round(x["ret"], 3)), ("horizon", 48.0, -0.01))
    check("a seller dearer than the pass's ask1 is skipped (the pre-send check)",
          rp.trade(up, one("A", 0.49, 0.8))["status"], "seller dearer than ask1")
    human = Fake({"A": [(0, {**B(0.5, 0.49), "sellOrders": [L("1", 0.5, bot=False), L("2", 0.505, bot=False),
                                                             L("3", 0.51, bot=False)]})]})
    check("only humans sell: no tradable seller", rp.trade(human, one("A", 0.5, 0.8))["status"], "no tradable seller")
    gone = Fake({"A": [(0, B(0.5, 0.49))] + [(h, {"sellOrders": [], "buyOrders": []}) for h in range(1, 73)]})
    check("no tradable bid through horizon + wait: stuck", rp.trade(gone, one("A", 0.5, 0.8))["status"], "stuck")
    check("the data stops before the wait is over: not known yet",
          rp.trade(Fake({"A": [(0, B(0.5, 0.49)), (3, B(0.5, 0.49))]}), one("A", 0.5, 0.8))["status"], "not known yet")

    print("replay.replay: picks pass by pass with the agent's rule")
    preds = pd.DataFrame([row("A", 0.50, 0.8), row("C", 0.50, 0.6), row("D", 2.5, 0.9),
                          row("A", 0.50, 0.9, pass_id=2, t=T0 + pd.Timedelta(hours=1)),
                          row("A", 0.50, 0.9, pass_id=3, t=T0 + pd.Timedelta(hours=6))])
    tr = rp.replay(preds, None, books=up)
    check("p_up < 0.65 and >= 2 keys never picked; A held until its exit at 5 h, then again",
          list(zip(tr.pass_id, tr.item)), [(1, "A"), (3, "A")])
    s = rp.summary(tr.iloc[:1], days=1.0)
    check("summary: bought, won, keys", (s["bought"], s["win"], round(s["keys"], 3)), (1, 1.0, 0.021))
    check("the default rule is the agent's", fc.AGENT_RULE["min_p_up"], 0.65)

    print()
    if fails:
        print(f"{len(fails)} failed: {fails}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

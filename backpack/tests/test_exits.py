# -*- coding: utf-8 -*-
"""Offline test for engine.exits (Stage 6, 3.7): the model's exit, live -- take-profit before
the horizon, the best bid from it, overdue past the wait.

    python test_exits.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import sys

import pandas as pd

from engine import exits as ex
from engine.forecast import EXIT_WAIT_H, HORIZON, TAKE_PROFIT

fails = []
T0 = pd.Timestamp("2026-10-19T12:00:00", tz="UTC")


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def L(sid, keys, bot=True):
    return {"steamId": sid, "user": "u" + sid, "premium": True,
            "price": {"keys": int(keys), "ref": 0.0, "total_keys": keys},
            "listingType": "bot" if bot else "normal", "dealType": "instant",
            "strange": False, "spelled": False, "painted": False, "parted": False,
            "id": f"440_{sid}_x"}


def book(bids, bot=True):
    buys = [L(str(10 + i), b, bot) for i, b in enumerate(bids)]
    sells = [L("1", 1.10), L("2", 1.11), L("3", 1.12)]
    return {"sellOrders": sells, "buyOrders": buys, "tradeUrls": {o["steamId"]: "u" for o in buys + sells},
            "scraped_at": T0.isoformat(), "watch_id": 1}


POS = {"id": 1, "item": "X", "quality": 6, "variant": 0, "entry_keys": 1.00,
       "take_profit_at": 1.00 * (1 + TAKE_PROFIT), "horizon_at": (T0 + pd.Timedelta(hours=HORIZON)).isoformat()}


def at(h):
    return T0 + pd.Timedelta(hours=h)


def main():
    print("decide")
    d = ex.decide(POS, book([1.05, 1.04, 1.03]), now=at(5))
    check("before the horizon, a bid at +5%: sell, take-profit", (d["action"], d["why"]), ("sell", "take_profit"))
    check("... into the best bid, at least the take-profit price", (d["listing"]["total_keys"], d["min_keys"]),
          (1.05, POS["take_profit_at"]))
    d = ex.decide(POS, book([1.03, 1.02, 1.01]), now=at(5))
    check("before the horizon, +3%: hold", (d["action"], d["why"]), ("hold", "under the take-profit"))
    d = ex.decide(POS, book([1.05, 1.04, 1.03], bot=False), now=at(5))
    check("a +5% bid from humans only: hold (the agent sells to bot+instant)", d["action"], "hold")
    d = ex.decide(POS, book([3.5, 3.4, 3.3]), now=at(5))
    check("bids above MAX_JUMP x entry: a misread, hold", d["action"], "hold")
    d = ex.decide(POS, book([0.95, 0.94, 0.93]), now=at(HORIZON))
    check("at the horizon: sell into the best bid, even at a loss",
          (d["action"], d["why"], d["listing"]["total_keys"], d["min_keys"]), ("sell", "horizon", 0.95, 0.0))
    d = ex.decide(POS, book([0.95, 0.94]), now=at(HORIZON + 3))
    check("past the horizon, no fenced bid: hold, waiting for a buyer",
          (d["action"], d["why"]), ("hold", "horizon: waiting for a buyer"))
    d = ex.decide(POS, book([0.95, 0.94, 0.93]), now=at(HORIZON + EXIT_WAIT_H + 1))
    check("past the wait, a bid: sell, flagged overdue", (d["action"], d["why"]), ("sell", "overdue"))
    d = ex.decide(POS, book([]), now=at(HORIZON + EXIT_WAIT_H + 1))
    check("past the wait, no bid: hold, overdue", (d["action"], d["why"]), ("hold", "overdue: no buyer"))
    check("hours to the horizon", ex.decide(POS, book([]), now=at(10))["hours_to_horizon"], HORIZON - 10)
    cheap = dict(POS, entry_keys=0.98, take_profit_at=0.98 * (1 + TAKE_PROFIT))
    d = ex.decide(cheap, book([1.02, 1.01, 1.00]), now=at(5))
    check("the take-profit is on the price paid (0.98 -> 1.0192)", (d["action"], d["listing"]["total_keys"]),
          ("sell", 1.02))
    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

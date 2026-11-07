"""Stage 6, step 3.7 -- when to sell a held position: the model's own exit (engine.forecast
label_forecast / _end_exit), live.

For an open position (a row of the agent's ledger, backpack/agent.db) and its newest book:

  before the horizon -- sell into the best tradable bid the first time it is at least
      `take_profit_at` (entry x (1 + TAKE_PROFIT)); else hold;
  from `horizon_at` (entry + HORIZON) -- sell into the best tradable bid, whatever it is;
      none: hold, waiting for a buyer (the label waits up to EXIT_WAIT_H);
  after horizon + EXIT_WAIT_H -- the label stops here and marks the position at the last
      bid seen; a real agent still holds the item, so it keeps selling into any tradable
      bid, and the decision says `overdue` (for the user's eye).

A bid above MAX_JUMP x entry is a misread, never a sale. "Tradable" = engine.listings (bot
+ instant, fenced, not untrusted, a trade URL) -- stricter than the backtest's bid1_fill,
which also sells to humans (measured: same price in 97.4% of books, data-model.md).

Differences from the label, on purpose: the take-profit and the jump cap are against the
price actually paid (`entry_keys`, which the pre-send check can make lower than the pick's
ask1), not the pass's ask1; and the decision here is a plan -- the sale itself goes through
engine.recheck.recheck_sell on a fresh book with this `min_keys`.

    d = decide(position, book, now)   # {"action": "hold" | "sell", "why", "min_keys", "listing", ...}
"""
import pandas as pd

from engine.forecast import EXIT_WAIT_H
from engine.recheck import recheck_sell

HOLD, SELL = "hold", "sell"


def _ts(x):
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t


def decide(position, book, now=None, wait_h=EXIT_WAIT_H):
    """What to do with one open position now, from its newest book (stored or fresh)."""
    now = now or pd.Timestamp.now(tz="UTC")
    horizon = _ts(position["horizon_at"])
    pos = {"entry_keys": position["entry_keys"]}          # MAX_JUMP against what was paid
    out = {"position_id": position.get("id"), "item": position.get("item"),
           "quality": position.get("quality"), "variant": position.get("variant", 0),
           "hours_to_horizon": round((horizon - now).total_seconds() / 3600, 2)}

    if now < horizon:
        tp = position["take_profit_at"]
        d = recheck_sell(pos, book, min_keys=tp, max_age_s=float("inf"), now=now)
        if d["ok"]:
            return {**out, "action": SELL, "why": "take_profit", "min_keys": tp, "listing": d["listing"],
                    "candidates": d["candidates"]}
        return {**out, "action": HOLD, "why": "under the take-profit", "min_keys": tp,
                "listing": d["listing"], "detail": d["reason"]}

    overdue = now >= horizon + pd.Timedelta(hours=wait_h)
    d = recheck_sell(pos, book, min_keys=0.0, max_age_s=float("inf"), now=now)
    if d["ok"]:
        return {**out, "action": SELL, "why": "overdue" if overdue else "horizon", "min_keys": 0.0,
                "listing": d["listing"], "candidates": d["candidates"]}
    return {**out, "action": HOLD, "why": "overdue: no buyer" if overdue else "horizon: waiting for a buyer",
            "min_keys": 0.0, "listing": None, "detail": d["reason"]}

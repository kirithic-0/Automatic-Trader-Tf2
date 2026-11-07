# -*- coding: utf-8 -*-
"""Offline test for engine.bot_features: the decayed sums, what each listing did since the
book's previous pass, the per-account rates (shrinkage, point-in-time) and the per-book
aggregates -- over hand-built bot_obs tables, no database.

The market: one book side (Cap, Unique, buys), hourly passes. A raises one scrap over
the pack at pass 2 while the median has not moved, and the median follows at pass 3
(a lead the book followed); B arrives at pass 2; A lowers at pass 4.

    python test_bot_features.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import math
import sys

import numpy as np
import pandas as pd

from engine import bot_features as bf

fails = []
T0 = pd.Timestamp("2026-09-20", tz="UTC")


def check(name, got, want, tol=1e-9):
    if isinstance(want, float) and isinstance(got, (float, np.floating)):
        ok = (math.isnan(want) and math.isnan(got)) or abs(got - want) <= tol
    else:
        ok = got == want
    print("  %-4s %-62s %s" % ("PASS" if ok else "FAIL", name,
                               got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def obs_row(sid, p, price, median, rank, gap=np.nan, item="Cap", side="b"):
    return {"snapshot_id": p * 10 + (0 if item == "Cap" else 1), "pass_id": p, "steamId": sid,
            "item": item, "quality": 6, "side": side, "price": price, "median": median,
            "rank": rank, "n_valid": 3, "gap_ref": gap, "t": T0 + pd.Timedelta(hours=p)}


def market():
    rows = [
        # pass 1: median 1.00
        obs_row("A", 1, 1.00, 1.00, 1, 0.0), obs_row("C", 1, 1.00, 1.00, 2, 0.0),
        obs_row("D", 1, 0.99, 1.00, 3),
        # pass 2: A raises one scrap to the top, the median has not moved; B arrives
        obs_row("A", 2, 1.0022, 1.00, 1, 0.11), obs_row("C", 2, 1.00, 1.00, 2, 0.0),
        obs_row("B", 2, 1.00, 1.00, 3, 0.0), obs_row("D", 2, 0.99, 1.00, 4),
        # pass 3: the median follows (+1%)
        obs_row("A", 3, 1.0022, 1.01, 1, 0.0), obs_row("C", 3, 1.01, 1.01, 2, 0.0),
        obs_row("B", 3, 1.01, 1.01, 3, 0.0), obs_row("D", 3, 1.01, 1.01, 4),
        # pass 4: A lowers; B is gone
        obs_row("A", 4, 0.98, 1.01, 3), obs_row("C", 4, 1.01, 1.01, 1, 0.0),
        obs_row("D", 4, 1.01, 1.01, 2, 0.03),
    ]
    return pd.DataFrame(rows)


def main():
    print("decayed_sums")
    ev = pd.DataFrame({"k": ["A", "A"], "t": [T0, T0 + pd.Timedelta(hours=48)], "x": [1.0, 1.0]})
    q = pd.DataFrame({"k": ["A", "A", "A", "B"],
                      "t": [T0, T0 + pd.Timedelta(hours=24), T0 + pd.Timedelta(hours=48), T0]})
    s = bf.decayed_sums(ev, q, "k", ["x"], 48.0, T0).x.tolist()
    check("an event counts in full at its own time", s[0], 1.0)
    check("half a half-life later: 2^-0.5", s[1], 2 ** -0.5)
    check("one half-life later + a new event: 0.5 + 1", s[2], 1.5)
    check("a key with no events is 0", s[3], 0.0)

    print("listing_moves")
    m = bf.listing_moves(market()).set_index(["steamId", "pass_id"])
    check("A at pass 2 is a transition", m.loc[("A", 2), "trans"], 1.0)
    check("A at pass 2 raised", m.loc[("A", 2), "raise"], 1.0)
    check("A at pass 2 is one scrap over #2 at rank 1 (cut1)", m.loc[("A", 2), "cut1"], 1.0)
    check("B at pass 2 is new, not a transition", (m.loc[("B", 2), "new"], m.loc[("B", 2), "trans"]),
          (1.0, 0.0))
    check("A at pass 3 held its price", (m.loc[("A", 3), "raise"], m.loc[("A", 3), "lower"]), (0.0, 0.0))
    check("A at pass 4 lowered", m.loc[("A", 4), "lower"], 1.0)
    check("A's raise ahead of the median is a lead the book followed",
          (m.loc[("A", 2), "lead"], m.loc[("A", 2), "lead_f"]), (1.0, 1.0))
    check("C's pass-3 raise came with the median: not a lead", m.loc[("C", 3), "lead"], 0.0)
    check("the lead is known LEAD_H after it", m.loc[("A", 2), "t_lead"] - m.loc[("A", 2), "t"],
          pd.Timedelta(hours=bf.LEAD_H))
    check("buy premium = price / median - 1", m.loc[("A", 2), "prem"], 0.0022, tol=1e-12)

    print("account_features")
    groups = pd.DataFrame({"steamId": ["A", "C", "D"], "as_of": [T0 - pd.Timedelta(days=1)] * 3,
                           "group": ["g", "g", "g"]})
    mv = bf.listing_moves(market())
    own = bf.account_features(mv, groups=groups, k_shrink=0.0).set_index(["steamId", "pass_id"])
    check("k=0: A's raise rate at pass 2 is its own (1 of 1)", own.loc[("A", 2), "raise_b"], 1.0)
    w = 2 ** (-1 / 48)                      # one hour of decay
    check("k=0: A's cut rate at pass 2, 1 of 2 rank-1 listings decayed: 1 / (w + 1)",
          own.loc[("A", 2), "cut1_b"], 1 / (w + 1), tol=1e-12)
    check("k=0: no lead known yet at pass 3 (it matures at +6 h): NaN",
          own.loc[("A", 3), "lead_b"], float("nan"))
    late = bf.account_features(bf.listing_moves(pd.concat(
        [market(), pd.DataFrame([obs_row("A", 9, 0.98, 1.01, 2)])])), groups=groups, k_shrink=0.0)
    check("k=0: at pass 9 (+7 h) A's one lead, followed: 1.0",
          late.set_index(["steamId", "pass_id"]).loc[("A", 9), "lead_b"], 1.0)
    check("group: A and C are in g, B is nonbot",
          (own.loc[("A", 2), "group"], own.loc[("B", 2), "group"]), ("g", "nonbot"))
    big = bf.account_features(mv, groups=groups, k_shrink=1e9).set_index(["steamId", "pass_id"])
    # at pass 2, group g's transitions: A raise (1), C hold, D hold -> 1/3 raise
    check("k huge: A's raise rate is the group's (1 of 3)", big.loc[("A", 2), "raise_b"], 1 / 3,
          tol=1e-6)
    cut = bf.account_features(bf.listing_moves(market()[market().pass_id <= 2]), groups=groups)
    full = bf.account_features(mv, groups=groups)
    a = cut.set_index(["steamId", "pass_id"]).sort_index()
    b = full.set_index(["steamId", "pass_id"]).loc[a.index].sort_index()
    same = np.allclose(a[bf.ACCOUNT_FEATURES].to_numpy(float), b[bf.ACCOUNT_FEATURES].to_numpy(float),
                       equal_nan=True)
    check("point-in-time: later passes never change earlier features", same, True)

    print("book_bot_features")
    book = bf.book_bot_features(mv, full).set_index(["item", "quality", "pass_id"])
    r2 = book.loc[("Cap", 6, 2)]
    check("pass 2: one bidder raised, one arrived", (r2.bot_b_n_raise, r2.bot_b_n_new), (1.0, 1.0))
    check("pass 2: the top bidder raised this pass", r2.bot_b_top_raised, 1.0)
    check("pass 2: a quarter of the bidders are unregistered (B)", r2.bot_b_sh_human, 0.25)
    r4 = book.loc[("Cap", 6, 4)]
    check("pass 4: one bidder lowered", r4.bot_b_n_lower, 1.0)
    check("every BOOK_BOT_FEATURES buy column exists",
          all(c in book.columns for c in bf.BOOK_BOT_FEATURES if c.startswith("bot_b_")), True)

    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

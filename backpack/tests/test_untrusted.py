# -*- coding: utf-8 -*-
"""Offline test for engine.untrusted: the candidate rule for the untrusted-account list --
only bids that get past the fence count -- on planted bot observations (the rows
engine.bot_groups.load_bot_obs returns).

    python test_untrusted.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import sys

import pandas as pd

from engine import untrusted as ut

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


def planted():
    """Accounts bidding on 30 items over 60 passes against a book median of 1.0; `kept` is
    whether the fence let the bid through."""
    rows = []
    #          steamId: (bid, kept)
    bids = {"fair": (1.0, True), "over4": (1.04, True), "over15": (1.15, True),
            "rejected": (6.5, False),                      # far above, but the fence drops it
            "few": (1.08, True), "listed": (1.04, True), "sometimes": (1.05, None)}
    for p in range(60):
        for i in range(30):
            for sid, (x, kept) in bids.items():
                if sid == "few" and i >= 5:            # overbids, but on 5 items only
                    continue
                if kept is None:                       # gets past in 1 pass of 3
                    kept = p % 3 == 0
                rows.append({"snapshot_id": p * 100 + i, "pass_id": p, "steamId": sid, "item": f"I{i}",
                             "quality": 6, "side": "b", "price": x, "median": 1.0, "n_valid": 7,
                             "kept": kept})
            rows.append({"snapshot_id": p * 100 + i, "pass_id": p, "steamId": "seller", "item": f"I{i}",
                         "quality": 6, "side": "s", "price": 1.5, "median": 1.0, "n_valid": 7,
                         "kept": True})                  # sell side
    return pd.DataFrame(rows)


def main():
    print("candidates")
    obs = planted()
    c = ut.candidates(obs, untrusted={"listed": 1}).set_index("steamId")
    check("a fair bidder is not a candidate", "fair" in c.index, False)
    check("~1.04x, always past the fence: a candidate, suggested level 1", int(c.loc["over4", "suggested"]), 1)
    check("~1.15x past the fence: suggested level 2", int(c.loc["over15", "suggested"]), 2)
    check("far above but always rejected by the fence: not a candidate", "rejected" in c.index, False)
    check("past the fence only 1 time in 3: not 'often'", "sometimes" in c.index, False)
    check("a high bid on too few items is not a habit", "few" in c.index, False)
    check("sells are not read", "seller" in c.index, False)
    check("a listed account is reported with its level", (int(c.loc["listed", "level_now"]),
                                                          int(c.loc["listed", "suggested"])), (1, 1))
    check("the window: only the last `passes` passes", int(c.loc["over4", "passes_seen"]), ut.PASSES)
    check("items counted per (item, quality)", int(c.loc["over4", "n_items"]), 30)
    check("top_share: the highest kept bid of its book", float(c.loc["over15", "top_share"]), 1.0)
    check("candidates first, highest overbid first; listed last",
          list(c.index), ["over15", "over4", "listed"])
    o = ut.overbids(obs).set_index("steamId")
    check("pass_rate of an always-rejected account is 0", float(o.loc["rejected", "pass_rate"]), 0.0)
    loose = ut.candidates(obs, min_pass=0.3, untrusted={}).set_index("steamId")
    check("min_pass is a parameter (0.3 -> the 1-in-3 account too)", "sometimes" in loose.index, True)
    print()
    if fails:
        print(f"FAILED: {len(fails)}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

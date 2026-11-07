"""Candidates for the untrusted-account list (engine.book.UNTRUSTED): measured here,
confirmed by the user one at a time (user decision 2026-09-26).

    cands = candidates(load_bot_obs())    # accounts whose high bids get past the fence

The list is for accounts the median fence does **not** already catch (user, 2026-09-26: "the
specific list for bots which often got past the median formula") -- a bid the fence rejects
never reaches a price, so such an account needs no entry. The rule (agent-chosen parameters,
each an argument): over the last `passes` passes, take every buy order an account posts in a
book where a median forms. Its **pass rate** = the share of those bids the fence keeps; on the
kept ones, its **overbid** = its bid / the book's median valid bid. An account that gets past
the fence at least `min_pass` of the time, with a median kept overbid of at least `min_ratio`,
across at least `min_items` distinct items, is a candidate -- the pattern of the two accounts
the user named unstable (~1.04x the median, inside every trust cut). The suggested level is
"by how much": 1 (never a fill) below `level2_ratio`, 2 (out of the book) at or above it.

Buy side only: a sell far below the median is a cheap seller, which the sell fence already
weighs by trust. Accounts already listed keep their level and are reported, not re-suggested;
a level-2 account is out of the book, so it has no observations at all.
"""
import numpy as np

from engine.book import MIN_VALID, UNTRUSTED

PASSES = 48              # the window: the last this many passes (~2 days)
MIN_PASS = 0.5           # a candidate gets past the fence at least this often ("often")
MIN_RATIO = 1.03         # ... its median overbid on the bids that got past is at least this
MIN_ITEMS = 20           # ... across at least this many distinct items (a habit, not one book)
LEVEL2_RATIO = 1.10      # suggested level 2 at or above this median overbid, else level 1


def overbids(obs, passes=PASSES, min_valid=MIN_VALID):
    """Per account over the last `passes` passes, from engine.bot_groups.load_bot_obs rows
    (buy side, books with >= min_valid valid bids so a median forms): books, pass_rate (the
    share the fence kept), and over the kept bids only -- items, the median overbid (bid /
    book median), the share at >= MIN_RATIO, and top_share (the share where it is the best
    kept bid, i.e. sets bid1)."""
    last = obs.pass_id.max()
    b = obs[(obs.side == "b") & (obs.pass_id > last - passes) & (obs.n_valid >= min_valid)
            & (obs["median"] > 0)]
    b = b.assign(ratio=b.price / b["median"], key=b.item + "|" + b.quality.astype(str))
    k = b[b.kept]
    k = k.assign(top=(k.price >= k.groupby("snapshot_id").price.transform("max")).astype(float))
    seen = b.groupby("steamId").agg(n_books=("ratio", "size"), pass_rate=("kept", "mean"))
    kept = k.groupby("steamId").agg(
        n_kept=("ratio", "size"), n_items=("key", "nunique"), passes_seen=("pass_id", "nunique"),
        overbid=("ratio", "median"), top_share=("top", "mean"),
        share_over=("ratio", lambda r: float((r >= MIN_RATIO).mean())))
    g = seen.join(kept, how="left")
    g[["n_kept", "n_items", "passes_seen"]] = g[["n_kept", "n_items", "passes_seen"]].fillna(0).astype(int)
    return g.reset_index()


def candidates(obs, passes=PASSES, min_valid=MIN_VALID, min_pass=MIN_PASS, min_ratio=MIN_RATIO,
               min_items=MIN_ITEMS, level2_ratio=LEVEL2_RATIO, untrusted=None):
    """Accounts whose high bids get past the fence as a habit (see module doc), highest
    overbid first, then the listed ones: the overbids() columns plus `level_now` (0 = not
    listed) and `suggested`."""
    untrusted = UNTRUSTED if untrusted is None else untrusted
    g = overbids(obs, passes, min_valid)
    g["level_now"] = g.steamId.astype(str).map(untrusted).fillna(0).astype(int)
    hit = (g.pass_rate >= min_pass) & (g.overbid >= min_ratio) & (g.n_items >= min_items)
    c = g[hit | (g.level_now > 0)].copy()
    c["suggested"] = np.where(c.level_now > 0, c.level_now,
                              np.where(c.overbid >= level2_ratio, 2, 1))
    return c.sort_values(["level_now", "overbid"], ascending=[True, False]).reset_index(drop=True)

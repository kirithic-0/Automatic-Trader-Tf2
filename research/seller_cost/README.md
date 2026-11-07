# What did the seller pay? A bot's cost basis from its own buy order (2026-10-27)

Research mode. Idea (user, 2026-10-27): a bot's sell listing was usually bought through its
own buy order, so that order's price is roughly what it paid, and ask / cost is its margin
-- how much room it has to cut. Not in the KB before this; per-bot *move* models were
(`research/bot_moves`) and did not trade better.

## Step 1: coverage (`coverage.py`)

A Steam item gets a new asset id each time it changes hands (general Steam knowledge), so
a bot sell listing `440_<assetid>` the series has never shown is an item the bot has just
got. Every Azure pass of `trading_app.db` (>= 133, 2026-10-16 -> 10-27, 709,933 books, 2,881
series), the item itself only (no strange / spelled / parted / painted copies), a series'
previous book <= 3 h old. Runs in ~2 min.

| | new bot sell listings |
|---|---|
| new asset ids | 65,833; 97.2% with `listedAt` after the previous pass (truly new) |
| same account bid on the item **at the previous pass** | **27.0%** |
| ... **at any pass in the 6 h before** | **48.0%** |
| of the previous-pass hits: that bid gone now / still there / same price | 83.9% / 16.1% / 13.1% |
| **baseline**: OLD bot sell listings whose account also bids on the item | **2.8%** (9.86 M listing-passes) |

**The signal is real.** A new listing follows the same bot's buy order 17x more often than
an old listing sits beside one (48% vs 2.8%), and in 84% of the hits the buy order is gone
the moment the item appears: the order filled and closed. It is not a pricer quoting both
sides. Both rates are **lower bounds**: 200,680 books (28%) store fewer sells than the
listing count says, and a bot's buy order below the stored cut is never seen.

**By account it is two kinds of bot**: of the 250 accounts with >= 10 new listings, the
median account's hit rate is 63%, 31% are >= 80% (stock comes from their buy orders), and a
quarter are at 0% (stock comes from elsewhere). Gladiator bots hit 63%, the rest 43%.

**Margin** = ask / that account's last bid (30,756 hits within 6 h):

| p10 | p25 | median | p75 | p90 | <= 0 | 0-5% | 5-15% | > 15% |
|---|---|---|---|---|---|---|---|---|
| +1.1% | +2.8% | **+6.5%** | +9.9% | +18.0% | 2.1% | 39.5% | 43.4% | 15.0% |

Gladiator and the rest: the same median, +6.5%. For scale, the same listings' ask is a
median +5.3% over the book's best bid: a bot lists about 1 pt over the spread it bought at.

**Verdict: enough coverage to build on.** About half the new bot stock has a known cost,
more for the buy-order bots, and the margin spreads widely (40% under 5%, 15% over 15%):
room to cut differs a lot from listing to listing.

## Step 2: the margin features in the trade model (`features.py`, `test.py`)

`features.py` (one scan of all 1.06 M books, ~3 min): a bot sell listing's cost is set when
its asset id first shows after the same account's bid within 6 h, and stays with the asset
while it is listed. Nine columns a book: `sm_known` (share of bot sells with a known cost),
`sm_n_known`, `sm_cheap_m` (the cheapest bot sell's margin), `sm_min_m`, `sm_med_m`,
`sm_floor` (share within 3% of cost), `sm_cost_ask` (lowest known cost vs ask1), `sm_new_24h`,
`sm_fill_24h` (new listings, and those that followed the bot's own buy order). Known on 45%
of books, but on the cheapest seller on only 13%.

`test.py`: the engine's walk-forward on trading_app.db, 24 h test days 10-16 21:21 -> 10-25
08:06 (9 days, 230,167 universe rows, base rate 10.8%), xgboost with the 138 features (base)
vs 138 + 9 (margin), seeds 0-2 (A) and 3-5 (B), all on the same training rows. Scored by the
replay (fills assumed) at the agent's `p_up >= 0.7` and at matched volume (67 bought).

| variant | AUC | rows >= 0.7 (went up) | replay at 0.7: bought, won, a trade, keys | matched ~67: won, a trade, keys |
|---|---|---|---|---|
| baseA | 0.767 | 206 (77%) | 67, 91.0%, +4.15%, 0.97 | 89.6%, +4.13%, 0.96 |
| baseB | 0.767 | 211 (80%) | 71, 85.9%, +3.62%, 0.85 | 86.8%, +3.68%, 0.85 |
| marginA | 0.768 | 205 (78%) | 69, 89.9%, +4.18%, 1.02 | 89.4%, +4.15%, 0.95 |
| marginB | 0.769 | 200 (82%) | 69, 88.4%, +3.89%, 0.90 | 88.1%, +3.89%, 0.85 |

Same seeds, margin minus base: **+0.03 / +0.27 pt** a trade at 0.7, **+0.02 / +0.21** at
matched volume; AUC +0.001 / +0.002. **The two base seed sets differ by 0.5 pt** (+4.15 vs
+3.62) on these ~67 trades -- far more than app48's 0.1, because 67 trades is a small sample
-- so a +0.02 to +0.27 gain is inside the noise. Gain share in `p_up`: the nine columns
2.4% together; the most used is `sm_known` (#24 of 147), the margins themselves #56-#122.

**Verdict: not a pass, not a fail.** Both seed sets lean positive, never negative, but by
less than the seed sets differ from each other. The column the model leans on is *how much
of the stock came through bot buy orders*, more than the margin. The cost is known for the
cheapest seller -- the one that sets ask1 -- on 13% of books, which caps what it can do.

What would settle it, in order: (1) more seed sets (4-6) on the same 9 days, to get the
paired difference's own spread; (2) recheck in ~2 weeks on new days; (3) raise coverage of
the cheapest seller: buy orders below the stored cut are missing (28% of books cut short).

`seeds.py` (seed sets C-F on the same 9 days, then the paired difference's spread over all
six) was started 2026-10-27 and stopped by the user before it scored: not run. Open: run it
on all 9 days, or only on the late days (10-21 -> 10-25, trained on 274k-355k rows vs 168k).

    python coverage.py              # step 1 (events in $SC_WORK)
    python features.py              # step 2a, the columns
    python test.py fit && python test.py score    # step 2b, ~12 min

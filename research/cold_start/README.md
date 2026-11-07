# Cold start: scoring an item with little history (2026-10-27)

Research mode. Question (user, 2026-10-27): if ~500 items were added to the collection,
could the same model score them without weeks of their own books? The model has no
item-identity feature. Its history features need up to 72 h (changes, windows) and 168 h
(`b1max168_over_ask`, `medmax168_over_ask`). The window features are computed over
whatever rows exist, so a young series' values are short-window, not blank.

## The test (`test.py`)

- **Six draws.** Every series (2,881) was put in one of 6 random draws of ~480, about 500
  new items next to ~2,400 known ones.
- **A random start per series.** Each series in a draw got a random start between 10-17
  10:00 and 10-24 10:00, and its panel rows before that start were deleted. The forecast
  frame was then rebuilt exactly as `fc.forecast_frame` does.
- **The same walk-forward test days** as `research/exit_sweep` (10-17 10:00 -> 10-25
  10:06), on both seed sets. A young series' later rows join training as their labels
  close, as they would live.
- **Stitching.** Each series is young in exactly one draw, so stitching the draws gives a
  young copy of every row. Each was scored against the same row with full history, by the
  series' age at that row.

    python test.py fit     # 6 draws x 2 seed sets, ~23 min (GPU)
    python test.py score   # ~3 min

"Full history" means since the collector's restart on 10-16 21:21, after a 153 h gap. So
the baseline itself has 0.5-8.5 days, which makes the gap between young and full a lower
bound.

## Ranking (universe rows, 137,234 matched; A | B)

| age | rows | AUC young | AUC full | p_up rank corr | rows flagged (p_up >= 0.7) young / full | flagged that went up, young / full |
|---|---|---|---|---|---|---|
| 0-24 h | 25,142 | 0.729 \| 0.728 | 0.758 \| 0.758 | 0.82 | 12 / 36 \| 8 / 38 | 42% / 64% \| 75% / 66% |
| 24-72 h | 44,950 | 0.741 \| 0.741 | 0.747 \| 0.749 | 0.93 | 45 / 104 \| 42 / 102 | 71% / 75% \| 69% / 79% |
| 72-168 h | 64,296 | 0.760 \| 0.761 | 0.755 \| 0.754 | 0.96 | 62 / 72 \| 49 / 69 | 77% / 64% \| 82% / 74% |
| 168 h+ | 2,846 | 0.835 | 0.835 | 0.98 | 3 / 4 | 100% / 100% |

- **From 72 h on, a young series is scored as well as a full one.** AUC is the same (0.760
  against 0.755), and p_up ranks the rows the same way (0.96).
- **Younger than 72 h, the model mostly holds back instead of guessing.** A young series'
  mean p_up is lower (0.082-0.084 against 0.093-0.094). It flags 60-70% fewer rows at the
  agent's bar in the first 72 h. The short windows do not make an item look like a sure rise.
- At 0-24 h the ranking is a little worse (AUC -0.03). The precision of the few flags there
  (8-12 rows) is noise.

## The agent's replay, split by the series' age at the pick (A | B)

| age | young: bought, won, ret a trade, keys | full: bought, won, ret a trade, keys |
|---|---|---|
| 0-24 h | 5 \| 4, 60% \| 75%, +2.3% \| +3.4%, +0.04 \| +0.05 | 6 \| 5, 83% \| 80%, +3.7% \| +1.7%, +0.12 \| +0.04 |
| 24-72 h | 7 \| 8, 86% \| 88%, +3.3% \| +3.4%, +0.05 \| +0.07 | 15, 100%, +5.9%, +0.26 |
| 72-168 h | 11 \| 10, 82% \| 90%, +1.1% \| +2.8%, +0.13 \| +0.15 | 11 \| 12, 73% \| 75%, +1.5% \| +1.8%, +0.15 \| +0.16 |
| 168 h+ | 2, 50%, +0.8%, +0.00 | 3, 67%, +2.1%, +0.01 |

(Full-history picks from before a series' start, 28-31 trades, are left out: the young
world never had those rows.)

- **In the first 72 h, a young series makes about half the trades and ~30% of the keys**
  (+0.09 \| +0.12 against +0.38 \| +0.30 keys). Most of the gap is missed trades, not bad
  ones: young picks still win 60-88%.
- **From 72 h on, the keys are the same** (+0.13 \| +0.15 against +0.15 \| +0.16).
- The trade counts are small (4-15 a band), so read the replay for direction and the
  ranking table for size.

## Verdict

- **No need for two weeks, or even 7 days.** After ~72 h of passes the same model scores a
  new series as well as an old one.
- Before 72 h the model is cautious rather than wrong: few flags, fewer keys, no rush of
  false picks. A warm-up gate (no agent buys on a series younger than 72 h) would cost
  little: those trades are few, and some of them lose.
- **This tests history length on cosmetics only.** Non-cosmetics (weapons, tools, cases)
  are a different kind of item that the model has never been trained on. Being out of
  distribution is a separate question this test cannot answer: replay them on ~10 days of
  their own data first.
- Limits: the per-book bot churn counts (`bot_*_n_new` etc.) were computed against the
  real previous book, not a missing one, so a live new series' first pass may read
  differently. The baseline has <= 8.5 days of history (the 10-16 restart).

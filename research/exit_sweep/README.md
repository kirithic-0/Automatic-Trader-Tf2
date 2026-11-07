# Exit and ask-cap sweep on the replay (2026-10-27)

Research mode. Question (user, 2026-10-27): would another take-profit / horizon, or an ask
cap above AGENT_RULE's 1 key, earn more **keys per day per key in play**? Prompted by the
first live run: 6 held, 0 sold after ~15 h, the 2-key cap full.

`sweep.py` copies `engine.replay`'s loop with the take-profit, the horizon, the ask cap and
the live agent's `max_keys_in_play` as parameters. Nothing in the engine or the agent changed.

    python sweep.py preds    # the walk-forward, seed sets A (0-2, the engine's) and B (3-5): ~5 min
    python sweep.py run      # 2 x 4 caps x 4 take-profits x 3 horizons x 3 budgets: ~10 min
    python sweep.py show     # reprint the tables from the saved csv

**Setup.** `trading_app.db`, Unique + Strange, the engine's out-of-sample walk-forward over
the last 8 labelled days (**10-17 10:00 -> 10-25 10:06 UTC, 137 passes**), picked with
`forecast.pick` at `AGENT_RULE` (p_up >= 0.7, ask >= 0.1, <= 10 a pass). Each pick was traded
on the stored books by the agent's listing rules (bot + instant, fenced, payable in metal,
skipped if dearer than ask1), with fills assumed. Budgets: none, 2 keys (the live run's) and
8 keys (about the replay's own peak). Stuck positions hold their keys until horizon + 24 h,
but none were stuck in any run.

**Check.** At the agent's settings (cap 1, +4%, 48 h, no budget) the copy matches
`engine.replay` exactly: 63 bought, 90.5% won, +4.10% a trade, +0.897 keys, peak 7.31 keys.

Columns: `keys/day` = realised keys / 8 days; `peak` = most keys in play at once;
`per key-day` = keys / (sum of entry x hours held / 24), the return on capital while it is
deployed.

## 1. The exit: the current +4% / 48 h is the best

Ask cap 1 key, no budget (A | B):

| take-profit | horizon | won | ret a trade | at take-profit | held (median) | keys/day | peak | per key-day |
|---|---|---|---|---|---|---|---|---|
| 2% | 12 h | 63% \| 68% | +0.4% \| +0.6% | 50% | 11 h | 0.020 \| 0.034 | 4.1 \| 3.7 | 0.018 \| 0.031 |
| 2% | 48 h | 91% \| 89% | +2.4% \| +2.2% | 87% | 10 h | 0.069 \| 0.062 | 5.1 \| 4.5 | 0.036 \| 0.033 |
| 3% | 48 h | 89% \| 87% | +3.2% \| +3.0% | 78% | 15 h | 0.091 \| 0.084 | 6.3 \| 6.3 | 0.038 \| 0.034 |
| 4% | 12 h | 63% \| 69% | +1.2% \| +1.5% | 34% | 12 h | 0.045 \| 0.060 | 3.9 \| 3.6 | 0.034 \| 0.046 |
| 4% | 24 h | 79% \| 78% | +3.0% \| +2.8% | 54% | 20-23 h | 0.090 \| 0.081 | 5.6 \| 4.8 | 0.045 \| 0.040 |
| **4%** | **48 h** | **91% \| 88%** | **+4.1% \| +3.9%** | **75%** | **20-24 h** | **0.112 \| 0.108** | **7.3 \| 7.3** | 0.040 \| 0.036 |
| 6% | 48 h | 81% \| 79% | +3.3% \| +3.0% | 47% | 48 h | 0.103 \| 0.094 | 7.8 \| 7.8 | 0.028 \| 0.023 |

(All 24 rows are in `exit_sweep_results.csv` in the scratchpad.)

- **The agent's exit earns the most keys a day in both seed sets**, with no budget and under
  an 8-key budget (0.112 \| 0.108). Under the live 2-key budget it is tied for best
  (0.058 \| 0.055; 4% / 24 h is 0.059 \| 0.042).
- **A shorter horizon is a forced sale into a dip.** At 12 h the win rate falls to 63-69%.
  Example: Ripped Runts, which took the +4% at 10 h, was sold at **-52%** at 24 h under
  +6% / 24 h.
- **A lower take-profit cuts the winners short.** At +2% the trades turn over faster (10 h
  median), but the keys earned fall by about 40%. Faster turnover does not make up for it.
- 4% / 24 h has a slightly higher return per deployed key-day in A (0.045 vs 0.040), but
  not in B, where 4% / 12 h is best. That is inconsistent and inside the noise, while
  keys/day is clearly lower.
- **The live hold is normal.** The replay's median hold is 20-24 h, and a quarter of trades
  exit at the horizon, not at the take-profit. Six positions within 1-4% of entry after
  15 h is what the replay expects.
- Bias to note: the model predicts the +4% / 48 h label, so its picks are chosen for that
  exit. A fair test of another exit would retrain on that exit's label. Not done.

## 2. The budget: 2 -> 8 keys doubles keys a day, not 4x

Ask cap 1 key, +4% / 48 h: **2-key budget 0.058 \| 0.055 keys/day, 8-key 0.112 \| 0.108**
(the same as no budget: the peak is 7.3). So the 6 extra keys earn ~0.055 keys/day, about
**0.9% a key a day**. The first 2 keys earn ~2.8% a key a day, because they take the best
picks first. Past ~8 keys nothing more is earned at this rule (as the handoff said).

## 3. The ask cap: 2 keys helps once the budget is ~8; 3+ keys is one disaster away

+4% / 48 h:

| ask cap | budget | bought | won | ret a trade | keys | keys/day | peak |
|---|---|---|---|---|---|---|---|
| 1 | none | 63 \| 66 | 91% \| 88% | +4.1% \| +3.9% | +0.897 \| +0.864 | 0.112 \| 0.108 | 7.3 |
| 2 | none | 73 \| 75 | 88% \| 85% | +3.8% \| +3.7% | **+1.197 \| +1.164** | 0.150 \| 0.145 | 16.5 \| 15.3 |
| 3 | none | 74 \| 77 | 87% \| 84% | +3.2% \| +3.1% | +0.209 \| +0.272 | 0.026 \| 0.034 | 19.0 \| 20.7 |
| 5 | none | 75 \| 78 | 87% \| 85% | +3.3% \| +3.1% | +0.337 \| +0.400 | 0.042 \| 0.050 | 22.2 \| 23.9 |
| 1 | 8 keys | | | | | 0.112 \| 0.108 | |
| 2 | 8 keys | | | | | **0.132 \| 0.125** | |
| 3 / 5 | 8 keys | | | | | 0.132 \| 0.127 | |
| 1 / 2 / 3 / 5 | 2 keys | | | | | 0.058 \| 0.055 (all caps) | |

- **Cap 2 keys** adds 9-10 trades of 1.06-1.61 keys: **+0.30 keys in both seed sets**. Only
  3 hit the take-profit; the rest were sold at the horizon at -1.4% to +3.6%. Under an 8-key budget that is **+17%
  keys/day** (0.112 -> 0.132 \| 0.108 -> 0.125), and the gap between seed sets (~0.004)
  is far smaller than the gain. **Under the live 2-key budget it changes nothing**: a 1-2 key
  item rarely fits beside the cheap ones.
- **Cap 3 or 5 keys** adds only 2-3 trades above 2 keys in 8 days. One of them, **Mad Drip
  (Strange) at 2.52 keys, lost 39% (-0.99 keys) in both seed sets**, which wiped out the
  week's profit.
  - It was a real collapse, not a misread. The best tradable bid ratcheted up ~1 scrap an
    hour, from 2.52 to 2.614 at 40 h (the take-profit needed 2.624). At 41 h the top bids
    vanished, it fell to 1.52, and it was sold at the horizon at 1.53.
  - Under the 8-key budget, caps 3 and 5 score like cap 2 only because the budget happened
    to block Mad Drip. That is luck, not a rule.
  - Too few trades to judge, and the tail is large.

## 4. 20 keys and no other limit (`python sweep.py big`, user follow-up 2026-10-27)

+4% / 48 h, p_up >= 0.7 unchanged; "no limits" = no ask cap and no buys-a-pass cap (A | B):

| run | bought | won | keys (8 days) | keys/day | peak | avg in play | worst trade | keys without it |
|---|---|---|---|---|---|---|---|---|
| the agent today (cap 1, 2 keys) | 41 \| 41 | 85% \| 88% | +0.466 \| +0.439 | 0.058 \| 0.055 | 2.0 | 1.3 \| 1.4 | -0.014 | |
| cap 1, 20 keys | 63 \| 66 | 91% \| 88% | +0.897 \| +0.864 | 0.112 \| 0.108 | 7.3 | 2.8 \| 3.0 | -0.041 | |
| cap 2, 20 keys (section 3: peak < 20) | 73 \| 75 | 88% \| 85% | +1.197 \| +1.164 | **0.150 \| 0.145** | 16.5 \| 15.3 | | | |
| **no limits, 20 keys** | 73 \| 76 | 86% \| 84% | **+0.298 \| +0.243** | 0.037 \| 0.030 | 20.0 | 6.4 \| 6.9 | **-0.988 (Mad Drip)** | +1.286 \| +1.231 |
| ... and no 0.1-key floor | 123 \| 124 | 80% \| 78% | +0.409 \| +0.294 | 0.051 \| 0.037 | 20.0 | 7.0 \| 7.2 | -0.988 | +1.397 \| +1.281 |

- **More capital buys almost no more trades.** With the ask cap and the pass limit gone,
  the replay buys 73-76 in 8 days, against 63-66. The 10-a-pass limit never binds. **The
  p_up >= 0.7 bar is the real limit**: the model passes ~9 picks a day, and 20 keys sit
  mostly idle (6.4-6.9 keys in play on average).
- **With no limits it earns less than the agent does today** (0.03-0.04 keys/day against
  0.055). The few > 2-key trades include Mad Drip's -0.99 keys. Without that one trade it
  would be ~0.16 keys/day, barely above a 2-key cap (0.15), with 20 keys at risk instead of 16.
- Dropping the 0.1-key floor adds ~50 tiny trades at 78-80% won, for about +0.1 keys.

## 5. Lower p_up bars at 20 keys, cap 2 (`python sweep.py bars`, user follow-up 2026-10-27)

+4% / 48 h, <= 10 a pass, 20-key budget, ask < 2 keys (A | B):

| bar | bought | over budget | won | ret a trade | keys (8 days) | keys/day | avg in play | per key-day | losses summed |
|---|---|---|---|---|---|---|---|---|---|
| 0.50 | 214 \| 216 | 407 \| 363 | 63% \| 64% | +0.6% \| +0.8% | +0.80 \| +0.94 | 0.100 \| 0.118 | 15.9 \| 15.5 | 0.006 \| 0.008 | -1.34 \| -1.32 |
| 0.55 | 187 \| 187 | 169 \| 164 | 70% \| 70% | +1.4% \| +1.4% | +1.51 \| +1.56 | 0.188 \| 0.195 | 15.0 \| 15.2 | 0.013 \| 0.013 | -0.89 \| -0.83 |
| 0.60 | 159 \| 151 | 16 \| 5 | 72% \| 74% | +1.8% \| +2.0% | +1.60 \| +1.68 | 0.200 \| 0.210 | 13.2 \| 12.4 | 0.015 \| 0.017 | -0.68 \| -0.57 |
| **0.65** | 117 \| 108 | 0 | **82% \| 82%** | +3.2% \| +3.1% | **+1.80 \| +1.62** | **0.225 \| 0.203** | 9.1 \| 8.5 | **0.025 \| 0.024** | -0.23 \| -0.22 |
| 0.70 (today) | 73 \| 75 | 0 | 88% \| 85% | +3.8% \| +3.7% | +1.20 \| +1.16 | 0.150 \| 0.145 | 5.6 \| 5.6 | 0.026 \| 0.026 | -0.09 \| -0.11 |

- **0.65 is the best bar for keys a day**, at **+40-50% over 0.7** (0.225 \| 0.203 against
  0.150 \| 0.145). It keeps the same return per deployed key-day (0.025 against 0.026). The
  extra ~40 trades are about as good per key as the old ones, and use ~9 keys on average.
- **Below 0.65 the profit stops growing and the risk grows.** 0.60 makes about the same keys
  (0.20-0.21 a day) but needs 12-13 keys in play and wins only 72-74%. The return per
  key-day falls to 0.015, and losses triple. At 0.50 the 20-key budget blocks 360-400 buys,
  the win rate is 63%, and the keys a day fall back to 0.10-0.12.
- **The live win-rate stop would interfere.** It pauses buys when < 75% of the last 20 sales
  won. At 0.65 (82% on average) a bad patch of 20 can dip under 75%. At 0.60 (72-74%) it
  would pause most of the time. A lower bar needs that stop looked at too.
- Same caveats as above, plus one more: the 0.7 bar was itself chosen on an overlapping
  replay (10-16 -> 10-24), so the comparison between bars is in-sample for both. Recheck
  on days the model has not been tuned on before changing the bar.

## Verdict

- **Keep the exit.** +4% / 48 h is the best of the 12 tried, on every measure that is not
  noise.
- **Capital is the lever.** At this rule, 2 -> 8 keys roughly doubles keys a day
  (0.055 -> 0.11).
- **With ~8 keys, raising the cap 1 -> 2 keys adds ~17%** (~0.13 keys/day). At 2 keys it
  does nothing.
- **Don't go past 2 keys a trade** until there are many more trades: one 2.5-key crash
  erased the week.
- **More than ~16 keys can't be used at p_up >= 0.7**: the bar, not the money, limits the
  number of trades.
- **With 20 keys, a p_up bar of 0.65 and an ask cap of 2 keys is the best found: ~0.2 keys a
  day** (~1.4-1.6 keys a week), about 3.5-4x today's agent (0.055). It uses ~9 keys on
  average, and peaks near 19. The bar is the user's "fixed for good" decision (10-26), and
  the win-rate stop (75%) would need a look first. Recheck on fresh days before any change.
- Caveats: one 8-day window, ~65-78 trades, fills assumed (the live run filled 6 of 6
  buys; sells are still untested live).

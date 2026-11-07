# Next-hour bot move model: feasibility ladder (2026-10-16)

Question: can we predict, per (bot, item, hour), whether a bot's **buy order** moves
next hour (down / same / up, moved = |change| > 0.2%), and do sweep state, neighbours
and per-bot behaviour add signal beyond the bot's own history?

Data: `trading_app.db` passes 9-132 (2026-10-04 17:26 -> 2026-10-10 12:00), read rules 1-3
(`engine.book.valid_buys`, before the fence), bot-flagged listings only. "Next hour" = next
pass within 1.5 h. 3.58M rows, 334 bots; label mix 89.6% same / 7.1% up / 3.4% down.

- `build_rows.py OUT.parquet`: step 1, the rows (about 2 min).
- `ladder.py ROWS.parquet`: step 2, XGBoost ladder on two time windows (GPU, about 5 min).

## Results (test-day multiclass log loss; lower = better)

| model | W1 test 120h-end | W2 test 96-120h |
|---|---|---|
| R1 streak rule | 0.3738 | 0.3693 |
| R2 own history | 0.2675 | 0.2690 |
| R3 + sweep | 0.2489 | 0.2553 |
| R4 + neighbours | 0.2460 | 0.2510 |
| R5 + item book | 0.2381 | 0.2474 |
| R6a + per-bot behaviour | **0.2238** | **0.2411** |
| R6b + provider group | 0.2289 | 0.2451 |
| R6c + behaviour cluster | 0.2342 | 0.2462 |

Move AUC: R1 0.63 -> R2 0.89 -> R6a 0.92 (W1).

- **Feasible by the agreed bar:** R4 beats R2 in both windows (-8.0% / -6.7% log loss).
  Most of that is the bot's sweep state (R3); neighbours add a little on top (-1.2% / -1.7%).
- **Per-bot behaviour beats provider groups and clusters** in both windows: gladiator
  and tf2autobot owners configure each bot, so the provider is a weak label.
- On `unknown:repricing`, R4 is worse than the streak rule (0.4149 vs 0.4003); per-bot
  behaviour fixes it (0.3064).
- Top features (R6a gain): m1 11%, h_since_move 10%, b_sweep 5%, d_n_above 4%, b_react 4%.

Not yet shown: whether these predictions improve the **trade** model (steps 3-4:
combine bots per item, then the kill test on the forecast yardstick). Only about 6 days of
data; W2 trains on 3 days.

## Steps 3-4: combine per item, then the kill test (2026-10-16)

- `combine.py ROWS_ALL.parquet ITEMS.parquet` (step 3). The R6a bot model is refit every
  12 h from hour 24, each time on rows whose label closed before the refit, so every
  prediction is out of sample. Per item-hour, 400 simulated next hours with one shared
  shock (Gaussian copula). Fitted rho = **0.6**: independent draws (rho 0) give a movers
  variance of 3.04 against the real 4.06.
- Next-hour "top bid rises" ranking (AUC): structured simulation **0.846**; naive
  any-raise 0.810, sum 0.810, top bot's p_up 0.816. Overconfident about 2x (mean 0.19 vs
  real 0.105; top bin 0.92 vs 0.55): humans are not modelled.
- `killtest.py ITEMS.parquet CACHE_DIR` (step 4): engine.forecast's own frame from
  trading_app.db (books decoded in memory), `walk_forward`, 4 test blocks of 12 h,
  `simulate` k=10. Horizon 12 h and 24 h; 48 h leaves no test days in 6 days of data.

| horizon | variant | p_up AUC | prec@10 | ret@10 | trades | ret/trade | keys |
|---|---|---|---|---|---|---|---|
| 12 h | base (138 features) | 0.8762 | 0.192 | 1.52% | 14 | 1.71% | 0.14 |
| 12 h | + naive | 0.8779 | 0.195 | 1.57% | 17 | 1.79% | 0.22 |
| 12 h | + structured | 0.8799 | 0.197 | 1.59% | 16 | 2.12% | 0.21 |
| 12 h | + both | 0.8782 | 0.193 | 1.49% | 17 | 2.40% | 0.26 |
| 24 h | base | 0.8351 | 0.250 | 2.03% | 58 | 1.97% | 1.29 |
| 24 h | + naive | 0.8356 | 0.253 | 2.01% | 62 | 1.72% | 1.61 |
| 24 h | + structured | 0.8382 | **0.271** | 1.93% | 60 | 1.87% | 1.42 |
| 24 h | + both | 0.8365 | 0.262 | 1.92% | 60 | 2.05% | 1.42 |

**Verdict: does not pass the kill test on profit.** The structured feature helps rank
`p_up` (24 h prec@10 +2.1 pt, AUC +0.003, both horizons) but not the trade return:
ret@10 is +0.07 pt at 12 h and -0.10 pt at 24 h, inside seed noise (about 0.13%).
Structured beats base on ret@10 in 2 of 4 folds at 12 h and 1 of 4 at 24 h. Trade counts
(14-62) are too small to read the keys column. The same pattern as path C: bots are
predictable, the trade model does not gain.

Likely why: the bot signal is next-hour, the trade is 12-24 h. Next to try: a bot label
on the trade's horizon ("this item's top bid rises >= X within 12/24 h" built from bot
moves), use it for exit timing, and rerun on more data (collection stopped 2026-10-10).

## Option 1: the bot side on the trade's horizon (2026-10-17)

- `horizon.py ROWS_ALL PANEL OUT_PREFIX`: per-bot label "this bot's bid reaches the
  take-profit (engine ask1 x 1.04) at some pass in (t, t+H]", H = 12 / 24 h. Base rate
  0.63% / 1.22% of bot-rows. Leak-free refits every 12 h. Out-of-sample per-bot AUC
  **0.903 (12 h) / 0.839 (24 h)**.
- Per item, "an existing bot reaches the take-profit" (AUC): copula 0.894 / 0.814,
  naive any 0.896 / 0.818, **most likely single bot 0.901 / 0.819**. Fitted rho 0.8-0.9,
  so one bot decides and the simulation adds nothing at these horizons.
- `killtest_h.py HZ_ITEMS STEP3_ITEMS CACHE`, passes <= 132, cut **before** labelling.
  (Labelling the db with the 10-16 passes in it and then cutting labelled rows whose
  window falls in the 6-day gap as "never reached": base AUC fell 0.876 -> 0.807.
  After the fix, base reproduces step 4 exactly.)

| horizon | variant | p_up AUC | prec@10 | ret@5 | ret@10 | ret@20 | trades | ret/trade |
|---|---|---|---|---|---|---|---|---|
| 12 h | base | 0.8762 | 0.192 | 2.05% | 1.52% | 0.78% | 14 | 1.71% |
| 12 h | + hz12 | 0.8815 | 0.194 | 1.93% | **1.65%** | 0.60% | 13 | 1.46% |
| 12 h | + hz12 + next-hour | 0.8850 | 0.194 | 1.99% | 1.55% | 0.80% | 14 | 2.24% |
| 24 h | base | 0.8351 | 0.250 | 2.48% | 2.03% | 1.20% | 58 | 1.97% |
| 24 h | + hz24 | 0.8357 | 0.260 | 2.30% | 2.04% | 1.23% | 58 | 1.89% |
| 24 h | + hz24 + next-hour | 0.8378 | 0.264 | 2.43% | 1.91% | 1.18% | 63 | 1.94% |

**Verdict: still not a pass.** At 12 h, + hz12 raises ret@10 by 0.13 pt and beats base in
all 4 folds (the best sign so far), but ret@5 (-0.12) and ret@20 (-0.18) move the other
way and the gain is the size of seed noise. At 24 h, ret@10 is flat. Alone, the best bot
feature ranks the take-profit hit at AUC 0.743 (12 h) / 0.713 (24 h); the 138 features
already hold most of it (the reach features such as `b1max24_over_ask` are close to it).

## Replacing the forecast with the bot model: profit test (2026-10-17)

`profit_test.py HZ_ITEMS STEP3_ITEMS CACHE [TRADES_OUT]`: same frame, test blocks and
`engine.forecast.simulate` (k=10, ask >= 0.1 key) as killtest_h.py, passes <= 132.

| horizon | model | trades | win rate | median ret | ret/trade | keys | keys without best 2 |
|---|---|---|---|---|---|---|---|
| 12 h | forecast (current) | 14 | 57% | +2.7% | +1.7% | +0.14 | +0.07 |
| 12 h | bot rule (likeliest bot pays the take-profit), matched count | 17 | 41% | -0.9% | +2.7% | +2.14 | +0.08 |
| 12 h | bot-only model, matched count | 18 | 11% | -0.5% | -1.2% | -0.06 | -0.07 |
| 24 h | forecast (current) | 58 | 66% | +3.2% | +2.0% | +1.29 | +0.20 |
| 24 h | bot rule, matched count | 61 | 30% | -1.7% | -2.0% | +3.21 | +0.21 |
| 24 h | bot-only model, matched count | 58 | 28% | -0.7% | -3.7% | -0.86 | -0.88 |

- The bot-only model never clears the forecast's own 1% bar (max exp_ret +0.76% / +1.03%),
  so at that bar it makes 0 trades.
- The bot rule's keys are 2-3 big-ticket trades: Earbuds (Unique) bought at ~10 keys
  three times, each sold +9-11% within 1-3 h, and Max's Severed Head (Unique) at 26 keys,
  +2 keys. Without its best 2 trades it ties the forecast. These fills are not verified:
  a high bid on a 10-26 key item that keeps reappearing may be one that never accepts.
- The forecast's 24 h keys are mostly one trade too (Trickster's Turnout Gear, 10.3 keys,
  +1.01). With 14-61 trades, per-trade and median returns are the numbers to read.

**Verdict: do not replace the forecast.** It has the highest win rate and median trade
in both horizons; the bot-only model loses money.

## Exit timing (2026-10-17)

`exit_test.py ITEMS CACHE [TRADES]`. Entries: every universe row (spread 0-5%, ask >= 0.1
key) from hour 24, passes <= 132. Baseline = the engine's exit; it reproduces
`label_forecast` on 100% of entries. Knobs chosen on the first half of entries, scored on
the second. Signals = step 3's leak-free next-hour item features.

Mean return per position, test half (all entries lose about 2.5% on average: buying at
the ask and selling into the bid costs the spread unless the bid rises):

| rule | 12 h | 24 h | 48 h |
|---|---|---|---|
| engine exit (baseline) | -2.56% | -2.54% | -2.63% |
| bail early (drop predicted: sell now) | **-2.40%** | **-2.28%** | **-2.44%** |
| bail early, shuffled signal (control) | -2.49% | -2.45% | -2.59% |
| wait at horizon (raise predicted: wait up to 3 passes) | -2.50% | -2.47% | -2.57% |
| wait at horizon, shuffled signal | -2.57% | -2.57% | -2.64% |
| let it run (hold past the take-profit while a raise is predicted) | -2.56% | -2.54% | -2.60% |
| always hold 1 more pass (control) | -2.58% | -2.57% | -2.72% |
| perfect hindsight (upper bound) | -0.40% | -0.14% | +0.27% |

- Bail early vs baseline: +0.16 / +0.26 / +0.19 pt, item-bootstrap 95% ranges all above 0
  ([+0.09, +0.24], [+0.16, +0.36], [+0.12, +0.28]). About half of it is just selling
  sooner (the shuffled control gains +0.07 / +0.09 / +0.04); the signal's own part is
  about +0.09 / +0.17 / +0.15 pt. It cuts the share of positions losing more than 5%
  (8.5 -> 7.0%, 10.8 -> 8.2%, 15.0 -> 13.1%).
- Wait at horizon: +0.06 / +0.08 / +0.06 pt (48 h range crosses 0); shuffled = no gain.
- Let it run: nothing. The take-profit exit is already right.
- **On the forecast's own trades it does not carry over:** 24 h bail cuts the mean
  +1.97% -> +0.87% (58 trades); 12 h bail changes nothing (14 trades); wait at horizon
  +1.71 -> +2.48% at 12 h (13 trades) but +1.97 -> +1.51% at 24 h. Bail helps the
  average entry, which is a loser, and sells the forecast's winners before they recover.

**Verdict:** the bot signal does carry real exit information (it beats the shuffled
control), but a small amount: about 0.1-0.2 pt of the 2-3 pt that perfect timing would
add, and it hurts the forecast's own picks. Next: an exit rule that knows the position's
state (bail only while under water), tuned on forecast-like entries. That needs more
trades, i.e. the Azure data.

## Decisions, model survey and next steps (moved from handoff.md, 2026-10-17)

Everything in `research/bot_moves/` is on `trading_app.db` passes 9-132. Nothing in `engine/` or `docs/` changed.

**Question (user):** the forecast's bot features ("bot aggressiveness") are noise, so can a
model of *each bot's next price move*, combined per item in a smarter way than averaging,
make the trade model better? Everything is in `research/bot_moves/` (README has every
table) on `trading_app.db` passes 9–132 (2026-10-04 17:26 → 10-10 12:00). Nothing in
`engine/` or `docs/` changed; nothing is committed. Memory note: `bot-move-model`.

### What was measured

| step | result |
|---|---|
| bot moves, raw (hourly) | 84% same / 12% up (median +0.4%) / 4% down (median −2.9%); a bot that just moved moves again 42% vs 10.7%; 90% of raises share the hour with another bot on that item; key price flat at 64.56 ref |
| drivers | 40% of moves happen in **sweeps** (the bot reprices ≥30% of its items that hour); a move by another bot on the item makes yours next hour 15.6% vs 5.6%; 68% of co-raises mix providers; rank (top/tied/below) and same-hour pricedb moves barely matter |
| groups vs clusters (267 bots) | operator fleets cluster clean (scrapyard 6/6, merchanttf 5/5, gradle 4/4, safi 3/3, trademann 9/10); `gladiator:static` + `tf2autobot:static` fall into one cluster (merge); `gladiator:repricing` / `tf2autobot:repricing` split 4–6 ways by catalogue size, step size and reactivity; no crisp clusters (HDBSCAN 39–72% noise) |
| step 2: per-bot next-hour model | XGBoost on 3.58M (bot, item, hour) rows: log loss 0.374 (streak rule) → 0.224; AUC 0.63 → 0.92. Sweep state adds the most; neighbours a little; **per-bot behaviour numbers beat provider groups and clusters** in both windows (user: gladiator and tf2autobot bots are each owner-configured, so don't group by provider) |
| step 3: combine per item | leak-free refits every 12 h; Gaussian-copula Monte Carlo, ρ = 0.6. "Top bid rises next hour" AUC 0.846 vs 0.81 naive sums; overconfident ×2 (19% vs 10.5%) |
| step 4: kill test | engine `walk_forward` at 12 h / 24 h (48 h leaves no test days in 6 days): ranking AUC +0.003, ret@10 +0.07 / −0.10 pt = noise. **Not a pass.** |
| option 1: trade-horizon bot label | per bot "reaches the take-profit within 12 / 24 h": AUC 0.90 / 0.84; per item the likeliest single bot ranks as well as the copula (ρ 0.8–0.9). Kill test: 12 h ret@10 +0.13 pt in 4/4 folds but ret@5 / ret@20 worse; 24 h flat. **Not a pass.** |
| replacing the forecast | bot-only model never expects > +1% (0 trades at the bar) and loses money at matched count; the "bot rule" shows the most keys only from Earbuds / Max's Severed Head (10–26 key items, fills unverified), ties without its best 2 trades. **Keep the forecast.** |
| exit timing | over all universe entries, "bail early when a drop is predicted" beats the engine exit by +0.16 / +0.26 / +0.19 pt (12 / 24 / 48 h; item-bootstrap ranges > 0); the signal's own share vs a shuffled control is ~+0.1–0.17 pt of the 2–3 pt perfect timing would add. **On the forecast's own trades it hurts** (24 h: +1.97% → +0.87%, 58 trades). "Let it run" past the take-profit: nothing. |

**The pattern (three tries now, with path C):** bots are very predictable hour to hour,
but the forecast's 138 features (reach, level, churn) already hold what that is worth for
a 12–48 h trade.

**Comparing the two models:** the bot model's AUC (0.92) is not "better" than the
forecast's (0.84–0.88): it answers an easier question ("will a bot that reprices every
2 h move next hour?" vs "will this item's bid be 2% over the ask tomorrow?"), so the two
scales do not compare. Only the kill test (forecast + bot columns) and the profit test
compare them. Example worth knowing (`MR.Gru Bot.tf`, tf2autobot, on Duck Billed Hatypus):
the bot reprices **every 2 hours**, and the model learned that from sweep state (45% of its
items last hour, 0% this hour → next hour is a sweep hour) and hours since its last move.
That series was picked as a favourable one: before real moves it predicted 79% vs 9%
before stays; a typical series separates by 14 points, not 71 (overall 42% vs 6.4%).

### Decisions (user, this research)

| question | decision |
|---|---|
| mode / where | research mode: scripts and notes in `research/bot_moves/`, engine untouched |
| bot identity | per-bot behaviour numbers, not `provider:pricing_mode` (gladiator and tf2autobot bots are each owner-configured) |
| per-group fine-tuning | dropped: the behaviour numbers do that job |
| label (step 2) | next pass within 1.5 h: down / same / up, moved = abs(change) > 0.2% (the engine's reprice threshold) |
| rows | buy orders, bot-flagged listings only, read rules 1–3 before the fence (as `bot_groups.py` does) |
| feasibility bar | the ladder: R4 (+ sweep + neighbours) must beat R2 (own history) on the test day in both windows (it did) |
| data window | passes ≤ 132; ignore the Azure rows (133+) for now, the VM only just started |
| horizon | 12 / 24 h for anything that retrains the forecast (48 h leaves no test days); the exit test also ran 48 h |

### Options considered but not built

From the model survey (2026-10-12), kept here so it is not re-derived:

- **Tabular:** XGBoost / LightGBM / CatBoost (built: the ladder).
- **Event / timing models:** Hawkes processes (self-exciting; fit the 42%-vs-10.7% streaks
  and the co-moves), discrete-time hazard / DeepHit (time to the next reprice).
- **Sequence:** GRU / LSTM / TCN per series; Temporal Fusion Transformer (static covariates,
  interpretable) the best fit of these. Mamba / S4 / PatchTST: overkill for ~130 steps.
- **Interaction:** Set Transformer / DeepSets over the bots on a book (joint per-bot
  outputs + an item head); temporal graph nets (TGN, TGAT, JODIE: bots ↔ items, also link
  prediction for arrivals); spatio-temporal graphs (DCRNN, Graph WaveNet / MTGNN adaptive
  adjacency). Who-follows-whom: Neural Relational Inference, neural Granger causality. All
  limited by hourly passes: within-hour order is hidden.
- **Cascades:** DeepHawkes / DeepCas (retweet-cascade analogy): how far a bidding war
  pushes the top bid and when it stops.
- **Sharing across bots:** shared encoder + bot embedding + group heads; mixture of
  experts; Bayesian partial pooling (better than hard per-group fine-tuning for small
  groups); MAML / Reptile for bots seen a few times.
- **Rule recovery:** bots are code, so fitting each bot's parametric rule (shallow tree /
  symbolic regression) may be the strongest per-bot model. Inverse RL does not fit (bots
  do not maximise a reward).
- **Decomposition first:** a low-rank tensor decomposition of moves (bot × item × hour)
  splits common shock / bot sweep / item-specific variance; cheap, not run.
- **Your own order as a token:** "if I bid X, do the overcutters jump?" → a market
  simulator for Stage 6 (and RL inside it). No interventional data until real trades are logged.
- **Fusion (2026-10-17):** early fusion = what the kill test did (flat); late fusion =
  stacking on out-of-sample predictions (next step 4 below); joint fusion = one net with a
  sequence encoder + a set encoder on the trade label (next step 8, needs weeks of data).
- **Skip:** GANs / TimeGAN, diffusion and flow forecasters (TimeGrad, CSDI), time-series
  foundation models (TimesFM, Chronos, Moirai: smooth curves, not step jumps; TimesFM was
  already removed), RL for the bot predictor, offline RL on 6 days.
- **No vector DB needed** for clustering ~350 bots (numpy / scikit-learn); one would only
  matter for "find similar past situations" over millions of (bot, item, hour) states.

### Method notes

- Clustering vectors (per bot, ≥ 200 observed hours): behaviour (move rate, raise share,
  raise / drop size, follows-the-book vs spontaneous, top / tied share, bid vs median and
  its spread, sweep mean / p90, reactivity), timing (share of items moved per hour) and
  co-listing (SVD of the bot × item matrix). Clustered blind (no provider in the features);
  k-means picked k = 16, seed stability ARI 0.63–0.72. 5-NN same-group: behaviour 52%
  (chance 19%).
- Ladder windows: W1 train 0–96 h / val 96–120 h / test 120 h–end; W2 shifted back 24 h.
- Combination (step 3): one shared shock per item-hour (Gaussian copula), ρ fitted to the
  variance of the number of movers; a raiser steps by its median raise, a dropper by its
  median drop; the next top bid is the max; humans are only a floor.
- Exit test: knobs picked on the first half of entries by time, scored once on the second;
  controls = the same knobs fed a shuffled signal, and "always hold N" with no signal;
  item bootstrap (500) for the gain's range.

### Ruled out (don't redo without new data)

- Next-hour or trade-horizon bot features as extra columns in the trade model.
- The copula / simulated combination at 12–48 h: the likeliest single bot does as well.
- Replacing the forecast with a bot-only model or bot rule.
- "Let it run" past the take-profit; holding longer without a signal.
- Provider (`provider:pricing_mode`) groups or k-means clusters as the bot identity.

### Next, in order

1. **Data first (user's VM).** Let the Azure collector run ~3–4 weeks unbroken. Ignore
   passes 133–146 for now (VM just started 10-16 21:21; 143–145 are partial, 111–803 of
   2,880 items). Every test above had only 4 × 12 h test blocks and 14–63 trades.
2. **Point the engine at `trading_app.db` (coding, user's call).** Its readers raise on
   compressed books: `engine/book._parse`, `engine/bot_groups._obs_rows` (json.loads) and
   the `json_each` SQL in `engine/bots.py`. `research/bot_moves/killtest_h.py` shows the
   in-memory decode (monkeypatch) that works without touching the engine.
3. **Position-aware exit rule:** bail only while the position is under water *and* a drop
   is predicted, tuned on forecast-like entries (needs hundreds of forecast trades, so
   after step 1).
4. **Late fusion (stacking):** out-of-sample predictions of the forecast + a time-series
   model of the item's last 48 h (GRU / TCN) + bot totals, under a small linear judge;
   score each part alone through the same kill test. ~1 day of work.
5. **Arrival model:** 72% of take-profit exits sell into accounts not on the book at entry
   (path C), which the per-bot model can't see. Predict new bidders (temporal link
   prediction, or an item-level arrival rate).
6. **Scrap-based move label:** a 1-scrap overcut (0.0017 keys) is under the 0.2% threshold
   on items above ~0.9 key, so the label misses it (seen on MR.Gru Bot.tf / Duck Billed
   Hatypus).
7. **Calibrate the step-3 simulation** (×2 overconfident; human bids are not modelled)
   before anyone reads its numbers as probabilities.
8. **Joint fusion net** (GRU over the item's history + attention over the bots on the book,
   one head on the trade label): only with ~3–4 weeks of data, judged against step 4.
9. **Faster snapshots (collector, `../azure-scraper`):** ~200 liquid items every 5–10 min
   would show who moves first inside the hour; hourly passes hide it.
10. **Bot identity in the engine (user's call):** replace `provider:pricing_mode` groups in
    `engine/bot_features.py` with per-bot behaviour numbers (move rate, step sizes, sweep
    size, reactivity), computed on the training window only.
11. **Check the big-ticket fills:** the Earbuds (Unique, ~10 keys) and Max's Severed Head
    (26 keys) take-profits that kept paying +8–11% within hours. If the bidder never
    accepts, it is an untrusted-list candidate (only if it gets past the median fence; the
    user confirms each).
12. **Promote findings to the docs (coding mode):** the bot-move statistics, the sweep
    finding and the group/cluster comparison belong in `docs/data-model.md`, dated.

### Rerun (from this folder, bare `python` 3.10, GPU for xgboost)

The intermediates were written to a session scratch folder and are gone; rebuild them in
this order into any folder `$W`:

```bash
python build_rows.py $W/rows_all.parquet                                   # step 1, ~2 min, all listings
python ladder.py $W/rows_all.parquet                                       # step 2 ladder, ~5 min
python combine.py $W/rows_all.parquet $W/items.parquet                     # step 3, ~10 min (checkpoints preds)
python killtest.py $W/items.parquet $W/engine_cache_app                    # step 4; builds the panel cache
python horizon.py $W/rows_all.parquet $W/engine_cache_app/panel_v3_ub6024f80.parquet $W/hz   # option 1
python killtest_h.py $W/hz_items.parquet $W/items.parquet $W/engine_cache_app
python profit_test.py $W/hz_items.parquet $W/items.parquet $W/engine_cache_app $W/trades.parquet
python exit_test.py $W/items.parquet $W/engine_cache_app $W/trades.parquet
```

### Gotchas from this research

- **Cut before labelling.** With the 10-16 passes in the DB, labelling first and cutting
  to ≤ 132 after turned the 6-day gap into "never reached" (base AUC 0.876 → 0.807).
  `killtest_h.py`, `profit_test.py` and `exit_test.py` cut inside `load_panel`;
  `horizon.py` cuts its rows. **`build_rows.py`, `combine.py` and `killtest.py` do not
  cut**: add `pid <= MAX_PASS` (or split on the gap) before rerunning them on a DB with
  new passes. Always check the base model reproduces before trusting a new number.
- The caches for `trading_app.db` go in their own folder, never `backpack/engine_cache`
  (that one is `trading.db`'s).
- A path like `r"D:\code\...\trading"` written through a heredoc or a Python
  `.replace()` string turns `\t` / `\b` into control characters: use forward slashes.
- `python -c "compile(...)"`, not `ast.parse`, to catch scope errors like a misplaced
  `global`.
- `PYTHONIOENCODING=utf-8` for anything that prints bot names (emoji).
- Scores are noisy: seed noise is ±0.13 pt on ret@10, one 4-day window swings ±0.3–0.4,
  and keys totals are carried by 1–3 big trades. Read win rate and median trade, and
  check every fold.

# Can the trade model get better? Re-scoring on data nobody tuned on (2026-10-17)

Question (user): "check the handoff and get some results, I want the model to be better".
The model is the Stage-5 forecast (`engine/forecast.py`, 138 features, xgboost). Research
mode: everything is here, `engine/` and `docs/` are untouched, nothing committed.

What was tried, each on the same yardstick:

1. the never-merged A/B/C winners A-lin and B-bid (`docs/extra/forecast-abc-stack.md`),
   rebuilt to the blueprint; the stack was deleted 2026-10-01 **without ever being re-scored**;
2. a GRU over each item's last 48 passes, blended late with xgboost (the blueprint's "revisit
   with 2+ weeks of history"; handoff section 7 at `fbee6734`, now `research/bot_moves/README.md`, next step 4);
3. recency-weighted training (old rows count less; not tried before);
4. training on `trading.db` + `trading_app.db` together (handoff section 3 at `fbee6734`, step 2);
5. the BUY bar (`MIN_EXP_RET`);
6. a ranking objective (XGBRanker, one query per pass).

## The yardstick (`yard.py`)

Engine frame, universe, purge and `simulate` unchanged; only the learner is swapped.

| window | data | horizon | test blocks | why |
|---|---|---|---|---|
| tune | trading.db | 48 h | 4 x 24 h, 09-21 09:00 -> 09-25 09:00 | the days A/B/C were tuned on; any new knob is picked here |
| **fresh** | trading.db | 48 h | 24 h blocks, 09-25 09:00 -> 09-29 16:21 (4.3 days) | **never scored by anyone**: A/B/C's last look was passes 163-172 |
| app24 / app12 | trading_app.db passes <= 132 | 24 h / 12 h | 4 x 12 h | the second source (full books); 48 h leaves no training rows in 6 days |

Scores: `auc` (p_up), `rho` (per-pass Spearman of exp_ret vs the realised trade, ask >= 0.1
key), `ret@k` (top k per pass), **`vol25/37.5/50`** = per-trade return at matched volume
(25 / 37.5 / 50 trades per 24 passes of test, the BUY bar moved per model; A/B/C's
100/150/200 over 4 days), and the portfolio at the engine's 1 % bar (trades, win rate,
median, mean, keys, keys without the best 2). Returns in %.

**Seed noise (main, seeds 0-2 vs 3-5):** ret@10 0.07-0.16 pt, matched volume up to
**0.33 pt**, 1 %-bar keys up to 0.57 keys. A difference smaller than that is not a result.

## 1. A-lin, B-bid and the stack

| window | variant | auc | rho | ret@10 | vol25 / 37.5 / 50 | trades @1% | win | mean | keys |
|---|---|---|---|---|---|---|---|---|---|
| tune | main | 0.730 | 0.459 | +1.68 | +2.05 / +1.37 / +1.05 | 195 | 49% | +0.39 | 1.04 |
| tune | main (seeds 3-5) | 0.729 | 0.458 | +1.61 | +2.19 / +1.22 / +1.15 | 200 | 51% | +0.60 | 1.61 |
| tune | A-lin | 0.730 | 0.465 | +2.06 | +2.18 / +1.73 / +1.42 | 159 | 58% | +1.28 | 1.70 |
| tune | B-bid | 0.730 | 0.454 | +2.00 | +2.10 / +1.51 / +1.13 | 212 | 53% | +0.48 | 1.54 |
| tune | A-lin+B-bid | 0.730 | 0.462 | **+2.35** | +2.17 / +1.78 / +1.39 | 147 | 56% | +1.21 | 1.70 |
| **fresh** | main | 0.760 | 0.452 | +3.00 | +2.89 / +2.56 / +2.08 | 149 | 73% | +2.15 | 2.05 |
| **fresh** | main (seeds 3-5) | 0.761 | 0.453 | +3.16 | +2.69 / +2.23 / +2.01 | 152 | 70% | +2.05 | 1.88 |
| **fresh** | A-lin | 0.760 | 0.448 | +2.99 | +2.63 / +2.24 / +1.80 | 110 | 74% | +2.27 | 1.47 |
| **fresh** | B-bid | 0.760 | 0.452 | +3.03 | +3.00 / +2.52 / +2.10 | 159 | 72% | +2.06 | 2.24 |
| **fresh** | B-bid (seeds 3-5) | 0.761 | 0.451 | +3.03 | +2.67 / +2.60 / +2.06 | 162 | 72% | +1.97 | 2.33 |
| **fresh** | A-lin+B-bid | 0.760 | 0.448 | +3.09 | +2.72 / +2.36 / +1.92 | 109 | 77% | +2.45 | 1.45 |
| app24 | main | 0.835 | 0.480 | +2.00 | +1.77 / +1.97 / +1.26 | 58 | 66% | +1.97 | 1.29 |
| app24 | A-lin | 0.835 | 0.478 | +2.34 | +1.12 / +1.21 / +1.30 | 168 | 46% | +0.12 | 1.57 |
| app24 | B-bid | 0.835 | 0.474 | +1.73 | +2.49 / +2.25 / +1.58 | 71 | 66% | +2.27 | 2.82 |
| app24 | A-lin+B-bid | 0.835 | 0.475 | +2.00 | +1.61 / +1.93 / +1.62 | 140 | 50% | +0.54 | 1.82 |
| app12 | main | 0.876 | 0.574 | +1.45 | +0.33 / -0.33 / -0.19 | 14 | 57% | +1.71 | 0.14 |
| app12 | A-lin+B-bid | 0.876 | 0.571 | +0.98 | +1.17 / +0.83 / +0.38 | 10 | 80% | +2.97 | -0.20 |

(app24 main reproduces the bot research's base exactly: 58 trades, +1.97 %, 1.29 keys.)

**Verdict: the stack's edge does not survive fresh data.** On the tune days it wins every
fold (ret@10 +0.67 pt). On the fresh window it is -0.2 pt at matched volume and +0.09 on
ret@10, inside seed noise; A-lin is worse (and on the app windows its linear half inflates
exp_ret, so the 1 % bar lets in 3x the trades at ~0 %). **B-bid** is the only one never worse
at matched volume (tune +0.09 / +0.13, fresh +0.03 / +0.13 over two seed sets, app24 +0.44)
but it loses on ret@10 in both app windows (-0.27 / -0.61). Not a pass; the 10-01 deletion
was right.

## 2. GRU over the item's history (`gru.py`, `run_gru.py`, `blend_gru.py`)

GRU(32) over the last 48 passes of the series (prices as log(x / today's ask): ask1, level,
bid1_fill, bid1, ask_med, bid5; book counts; hours back; a present flag) + 3 static inputs;
heads for ret (Huber) and up. Trained on the same purged rows per block, 6 epochs, 2 seeds,
CPU (~4-6 min a block). Blend weight w picked on tune (stack: 0.35 by matched volume),
then scored once on fresh.

| window | variant | ret@10 | vol25 / 37.5 / 50 | trades @1% | win | mean |
|---|---|---|---|---|---|---|
| tune | GRU alone | +0.70 | +0.10 / -0.33 / -0.19 | 34 | 56% | +1.16 |
| tune | main + GRU w 0.2 / 0.35 | +1.68 / +1.67 | +1.65 / +1.53 / +1.08 ; +1.74 / +1.54 / +1.17 | 144 / 105 | 57% / 59% | +1.08 / +1.54 |
| tune | stack + GRU w 0.35 | +2.16 | +2.47 / +1.92 / +1.50 | 80 | 69% | +2.24 |
| fresh | GRU alone | +2.20 | +1.35 / +1.61 / +1.00 | 81 | 67% | +1.41 |
| fresh | main + GRU w 0.2 | +3.20 | +3.00 / +2.42 / +2.15 | 126 | 75% | +2.37 |
| fresh | main + GRU w 0.35 | +3.18 | +3.26 / +2.23 / +2.02 | 108 | 75% | +2.21 |
| fresh | stack + GRU w 0.35 (locked) | +3.08 | +2.87 / +2.47 / +1.98 | 86 | 78% | +2.49 |

**Verdict: not a pass.** Price history alone gets ~70 % of main's top-10 return on fresh
(+2.20 vs +3.00) but adds little on top: main + GRU is +0.18-0.20 pt on ret@10 on fresh (4 of
5 days) and flat on tune; matched volume moves both ways by less than seed noise. Its
exp_ret correlates 0.74 (tune) / 0.86 (fresh) with xgboost's, so it mostly repeats what the
138 features hold, and it is less extreme at the top (0.7 / 1.3 % of rows over +1 % vs 1.8 /
1.6 % for xgboost): blending it in mostly shrinks the top of the ranking, so fewer trades
clear the 1 % bar at a higher win rate -- what raising the bar does on its own (section 5).

## 3. Recency weights (tune only)

xgboost sample weight 0.5^(age / half-life) on both models. ret@10: main +1.68; half-life
24 / 48 / 96 / 192 h: +1.76 / +1.74 / +1.79 / +1.73; matched volume mixed (+2.08-2.11 /
+1.34-1.62 / +0.95-1.22). **Flat; not taken to fresh.**

## 4. Training on both DBs (`combo.py`; app windows)

Training rows = the app db's own + every trading.db row (09-14 -> 10-01, relabelled on the
same horizon; all close days before the first app test block). Optional `src_app` flag.

| window | variant | auc | rho | ret@10 | vol25 / 37.5 / 50 | trades @1% | win | mean | keys |
|---|---|---|---|---|---|---|---|---|---|
| app24 | main, app only | 0.835 | 0.480 | +2.00 | +1.77 / +1.97 / +1.26 | 58 | 66% | +1.97 | 1.29 |
| app24 | main + trading.db | 0.848 | 0.489 | +2.08 | +1.37 / +0.74 / +0.63 | 71 | 58% | +0.70 | 1.42 |
| app24 | main + trading.db + flag | 0.848 | 0.489 | +2.17 | +1.32 / +0.68 / +1.30 | 69 | 62% | +1.10 | 1.38 |
| app24 | B-bid + trading.db | 0.848 | 0.490 | +1.47 | +1.38 / +1.55 / +1.28 | 95 | 60% | +0.93 | 3.04 |
| app12 | main, app only | 0.876 | 0.574 | +1.45 | +0.33 / -0.33 / -0.19 | 14 | 57% | +1.71 | 0.14 |
| app12 | main + trading.db | 0.898 | 0.571 | +1.52 | +0.60 / +0.87 / +0.97 | 22 | 55% | -0.38 | -0.18 |
| app12 | main + trading.db + flag | 0.897 | 0.571 | +1.64 | +0.77 / +0.81 / +0.84 | 24 | 54% | -0.44 | -0.21 |

**Verdict: mixed.** The old history ranks the whole universe better in both windows (AUC
+0.013 / +0.022, ret@10 +0.07-0.19) and lifts matched volume at 12 h, but at 24 h matched
volume drops 0.4-1.2 pt, and in both windows more trades clear the 1 % bar and they are worse
ones (+1.97 -> +0.70 % at 24 h). The two sources read different books (classifieds HTML vs
the app's full book), so exp_ret's level shifts: reading both needs the bar re-tuned, or the
app rows cut to the old depth (the handoff's "legacy view"), before it is a gain. The flag
column does not fix it.

## 5. The BUY bar (`bar_scan.py`, main's predictions)

| window | bar 1 % | 1.5 % | 2 % | 3 % |
|---|---|---|---|---|
| tune (seeds 0-2) | 195 tr, 49 %, +0.39 %, 1.04 k (-2: 0.53) | 128, 59 %, +1.20 %, 1.62 k (1.11) | 92, 62 %, +1.78 %, 1.68 k (1.16) | 45, 69 %, +2.72 %, 0.92 k (0.56) |
| tune (seeds 3-5) | 200, 51 %, +0.60 %, 1.61 k (1.10) | 129, 58 %, +1.09 %, 1.56 k (1.05) | 89, 61 %, +1.63 %, 1.42 k (0.91) | 46, 70 %, +2.62 %, 0.92 k (0.56) |
| fresh (seeds 0-2) | 149, 73 %, +2.15 %, 2.05 k (1.59) | 98, 80 %, +2.94 %, 1.44 k (0.99) | 66, 76 %, +2.84 %, 0.77 k (0.40) | 33, 85 %, +3.92 %, 0.61 k (0.25) |
| fresh (seeds 3-5) | 152, 70 %, +2.05 %, 1.88 k (1.43) | 100, 76 %, +2.30 %, 1.17 k (0.78) | 64, 78 %, +2.95 %, 0.86 k (0.50) | 28, 82 %, +3.92 %, 0.49 k (0.15) |
| app24 | 58, 66 %, +1.97 %, 1.29 k (0.20) | 35, 69 %, +2.08 %, 1.15 k (0.06) | 25, 68 %, +1.39 %, 0.98 k (-0.11) | 4 trades |

(trades, win rate, mean per trade, keys (keys without the best 2).)

**A trade-off, the user's call:** every window, every seed set: a higher bar means a higher
win rate and per-trade return (1.5 %: +0.1-0.8 pt a trade, about a third fewer trades),
while total keys -- the robust "without the best 2" column -- are highest at today's 1 % in
4 of the 5 rows (tune seeds 0-2 is the exception). Trades are by hand, so fewer, better trades may be worth more than the keys.

## 6. A ranking objective (`rank.py`, `run_rank.py`; tune only)

XGBRanker, one query per pass (the training universe's rows), so it learns only the order
inside a pass and the day's market drift cancels. Labels: `abs` grades (0 below 0 %, 1 to 2 %,
2 to 4 %, 3 at the take-profit), `pct` (within-pass percentile, 5 bins) or the raw return
(`rank:pairwise`). A ranker's score has no unit, so it is scored alone at matched volume,
as a **hybrid** (main's 1 % bar admits, the ranker orders) and as a **blend** (mean of main's
and the ranker's within-pass percentiles).

| variant (tune) | rho | ret@5 | ret@10 | vol25 / 37.5 / 50 |
|---|---|---|---|---|
| main | 0.459 | +2.01 | +1.68 | +2.05 / +1.37 / +1.05 |
| NDCG, abs grades | 0.238 | +0.82 | +0.94 | +0.89 / +0.96 / +0.37 |
| NDCG, pct grades | 0.375 | +1.34 | +0.86 | +0.65 / +0.65 / +0.52 |
| NDCG, abs, trained on ask >= 0.1 only | 0.259 | +0.80 | +0.76 | +0.53 / +0.57 / +0.62 |
| NDCG, abs, min_child_weight 100 | 0.263 | +1.21 | +0.83 | +1.30 / +1.23 / +0.96 |
| pairwise, raw return | 0.448 | +1.83 | +1.55 | +1.56 / +1.19 / +0.94 |
| pairwise, raw, top-k pairs | 0.328 | +1.00 | +1.35 | +1.62 / +0.86 / +0.41 |
| pairwise, raw, min_child_weight 100 | 0.444 | +1.35 | +1.18 | +1.25 / +1.06 / +0.82 |
| blend: main + pairwise raw | 0.458 | +2.42 | +1.82 | +2.00 / +1.10 / +0.57 |
| blend: main + pairwise raw mcw 100 | 0.456 | +2.37 | **+1.97** | +1.63 / +1.19 / +0.68 |

The hybrid makes the same trades as main (194-196 at the 1 % bar, +0.33 to +0.42 % a
trade vs +0.39 %): the bar rarely admits more than 10 items a pass, so reordering them changes
nothing.

**Verdict: fails on its own tune window; not taken to fresh.** Every ranker alone ranks
worse than main's return regressor (ret@10 +0.76 to +1.55 vs +1.68), and every blend that
lifts ret@10 (+0.14 / +0.29) loses at matched volume (vol50 -0.48 / -0.37). The premise was
wrong: ret@k and matched volume already compare items inside a pass, so the regressor's
drift was never costing the ranking; the pseudo-Huber regressor on returns orders the top of
a pass better than LambdaMART's pair losses over ~2,000 items a query.

## 7. What limits profit per day: the model or the market? (`ceiling.py`)

Same test predictions and `simulate` (one unit per item, never the same item twice at once,
ask >= 0.1 key). "Oracle" = perfect foresight of each entry's realised trade: the most this
trade rule could make on those days with unlimited trades.

| window | portfolio | trades/day | win | mean | keys/day | keys/day without best 2 | median ask |
|---|---|---|---|---|---|---|---|
| fresh | model, 10 a pass, 1 % bar | 35 | 73 % | +2.1 % | 0.48 | 0.37 | 0.24 |
| fresh | model, no cap, 1 % bar | 35 | 73 % | +2.1 % | 0.48 | 0.37 | 0.24 |
| fresh | oracle, winners > 1 % | 207 | 100 % | +2.7 % | 5.49 | 4.85 | 0.40 |
| tune | model, 10 a pass, 1 % bar | 51 | 49 % | +0.4 % | 0.27 | 0.14 | 0.34 |
| tune | model, no cap, 1 % bar | 51 | 49 % | +0.3 % | 0.27 | 0.13 | 0.34 |
| tune | oracle, winners > 1 % | 170 | 100 % | +2.6 % | 4.13 | 3.73 | 0.41 |

- **The 10-a-pass cap never binds**: lifting it changes nothing; the 1 % bar is what limits
  the model's trade count.
- **The model makes ~7-10 % of the ceiling** (0.27-0.48 of 4.1-5.5 keys a day).
- **The ceiling itself is small**: only 11-15 % of entries (ask >= 0.1) pay > 1 %, about half
  the ~1,450-1,480 items ever have one, and a perfect pick earns +2.6-2.7 % on a median
  0.4-key item. Most of the ceiling's keys are in expensive items: on fresh, the 115 items
  at 5+ keys give 2.2 of the 4.8 keys a day (every-winner oracle), while the model's
  median buy is 0.24 key (it ranks by %, not keys).
- Not modelled, both cut the ceiling: one unit per listing (no depth), and fills assumed.

## Overall

The model is at a plateau on the data we have: every learner, label, sequence and weighting
change is within seed noise on data it was not tuned on. What moves results by more than the
noise: the window itself (tune vs fresh: +0.4 % vs +2.2 % a trade for the same model), the bar,
and the data source. Next, in order:

1. **More app data (the VM).** With ~2 weeks of Azure passes, add an app-only 48 h window to
   `yard.WINDOWS` and rerun `run_ab.py` / `blend_gru.py` / `combo.py`: B-bid and the GRU are
   the two to recheck (both slightly positive somewhere, never clearly negative).
2. **If the engine reads both DBs** (coding, user's call): cut app books to the classifieds
   depth first, then re-tune the bar on the merged history; section 4 says naive merging
   shifts the calibration.
3. **The bar** (user's call): 1 % (most keys) vs 1.5-2 % (fewer, better trades).
4. Any future comparison: two seed sets minimum; < 0.3 pt matched volume is noise.
5. Ruled out on this data (section 6): ranking objectives (NDCG / pairwise), alone or blended.

## 8. GRU recheck: app-only 48 h, scored the way the agent trades (`app48.py`, 2026-10-27)

Next step 1 above, done once the VM had ~3.5 weeks. The engine's own walk-forward on
`trading_app.db` (no pass cap), 24 h test days 10-16 21:21 -> 10-25 07:06 UTC (9 days, 150
passes, 228,543 universe rows, base rate 10.9%; no 10-20 day: the collector's gap). Each day,
on the same purged training rows: xgboost seeds 0-2 (xgbA) and 3-5 (xgbB), the GRU seeds 0-1
(gruA) and 2-3 (gruB). Blends mix **both** heads, since the agent buys on `p_up`
(`AGENT_RULE`); w = 0.35 locked on the 09-21 tune window, 0.2 / 0.5 for shape. Scored by the
replay (`engine.replay`: the agent's listing rules on the stored books, fills assumed), at
the agent's bar `p_up >= 0.7` and at matched volume (the bar moved until 67 are bought).

| variant | AUC | rows >= 0.7 (went up) | replay at 0.7: bought, won, a trade, keys | matched 67: bar, won, a trade, keys |
|---|---|---|---|---|
| xgbA (the engine) | 0.767 | 206 (76%) | 67, 88%, +3.69%, 0.80 | 0.70, 88%, +3.69%, 0.80 |
| xgbB (seeds 3-5) | 0.767 | 210 (79%) | 67, 88%, +3.72%, 0.88 | 0.71, 88%, +3.80%, 0.91 |
| gruA alone | 0.711 | 0 | -- | 0.45, 52%, -2.01%, -0.31 |
| gruB alone | 0.718 | 0 | -- | 0.39, 52%, -1.80%, -0.41 |
| xgbA + gruA w 0.35 | 0.768 | 12 | 7, 86%, +3.64%, 0.07 | 0.57, 73%, +2.25%, 0.59 |
| xgbB + gruB w 0.35 | 0.769 | 3 | 3, 100%, +3.51%, 0.03 | 0.55, 79%, +2.85%, 0.75 |
| xgbA + gruA w 0.2 | 0.768 | 49 | 21, 86%, +3.37%, 0.24 | 0.62, 81%, +3.14%, 0.74 |
| xgbB + gruB w 0.2 | 0.769 | 36 | 19, 89%, +4.08%, 0.22 | 0.62, 82%, +2.95%, 0.70 |

**Verdict: the GRU is out, for good on this design.** Whole-universe AUC moves +0.001-0.002
(seed noise is ~0.003 a day), but the agent trades only the top, and there the GRU hurts:
at the same 67 trades every blend wins less (73-82% vs 88%) and makes less (+2.25 to +3.14%
vs +3.69 / +3.80%: 0.6-1.5 pt, against 0.1 pt between xgboost seed sets). Alone its top
picks lose money (52% won, -2%). Why: its `p_up` is compressed (99.9th percentile 0.46-0.52,
max 0.66, vs xgboost's 0.74 / 0.90) and correlates only 0.66 with xgboost's (exp_ret 0.83):
it adds noise where the agent buys, and blending it in drags the confident picks under 0.7
(206 rows -> 12 at w 0.35). With more data the 10-17 result (≈ noise) turned negative, not
positive. Not worth a rank-blend retry: the GRU's own top ranking is what loses.

    python app48.py fit --since 2026-10-16    # 46 min (xgboost GPU, torch CPU on 4 threads)
    python app48.py score                     # ~5 min (the replay at 10 variants x 9 bars)

## 9. C-curve recheck: app-only 48 h, scored the way the agent trades (`c48.py`, 2026-10-30)

User: "there was like 3 modification i did to ml model ... maybe I should test it now as it has
more data". A-lin and B-bid change only the exp_ret regressor, and the agent buys on p_up
(`AGENT_RULE`: p_up >= 0.65, best p_up first), so neither can change a live pick (and section 1
scored them as noise). C-curve adds 11 book-shape columns, so it changes p_up too; it was
deleted on 10-01 without being re-scored. Rebuilt to the blueprint from `bot_obs.kept` (the
`_d` changes are of the asks' 2-7 median *price*, the slope's are differences; the blueprint
does not say). Same walk-forward and replay as section 8, now 12 test days 10-16 21:21 ->
10-28 15:06 UTC (230 passes, 356,703 universe rows, base rate 9.5%), main vs main + C-curve on
the same training rows, two seed sets each; matched volume = mainA's 180 buys at 0.65.

| variant | AUC | rho (p_up) | replay at 0.65: bought, won, a trade, keys | at 0.7 | matched 180: bar, won, a trade, keys |
|---|---|---|---|---|---|
| mainA (the engine) | 0.780 | 0.271 | 180, 78.3%, +2.57%, 2.23 | 116, 79.3%, +2.78%, 1.20 | 0.650, 78.3%, +2.57%, 2.23 |
| mainB (seeds 3-5) | 0.779 | 0.270 | 169, 78.1%, +2.81%, 2.03 | 111, 80.2%, +3.10%, 1.25 | 0.645, 78.1%, +2.82%, 2.18 |
| curvA | **0.805** | **0.303** | 209, 75.1%, +1.98%, 2.15 | 130, 76.2%, +2.56%, 1.33 | 0.668, 76.2%, +2.17%, 1.98 |
| curvB | **0.804** | **0.304** | 213, 75.1%, +1.97%, 2.18 | 128, 78.1%, +2.53%, 1.50 | 0.671, 75.8%, +2.12%, 1.76 |

- AUC +0.025 and rho +0.03, on **every** one of the 12 days in both seed sets (seed noise on
  AUC ~0.002): the book shape is real signal for ranking the whole universe, as on 09-27.
- Where the agent buys it is worse: at the same 180 trades it wins 2-2.5 pt less and makes
  0.4-0.7 pt less a trade (+2.12 / +2.17% vs +2.57 / +2.82%; mainA vs mainB = 0.25 pt), 0.2-0.4
  keys less over 12 days. At the live 0.65 bar it buys ~16% more at a lower return, about the
  same keys. By day (matched bars) it is worse on 6 / 8 of 12 days; it wins the early days
  (10-17, 10-18: trained only on 10-04 -> 10-10) and loses most of 10-22 -> 10-26 (as much as
  -3.9 pt a trade on 10-22), the days with the most Azure training data. 142 of the 178 series
  mainA buys are bought by curvA too.

**Verdict: C-curve is not a pass.** It ranks the whole universe better but not the top: the
same split as 09-27 ("improves the ranking of the whole universe more than the top picks"),
now with enough data to see the top get worse. With this, all three A/B/C parts are re-scored
and none helps the live agent. A future try at book shape would have to be judged at the top
(e.g. a model trained only near the agent's bar), not by AUC.

    python c48.py build                       # ~1.5 min: the frame + C-curve, column summary
    python c48.py fit --since 2026-10-16      # 16.5 min (xgboost GPU), caches copied to $FV2_WORK
    python c48.py score                       # ~3 min (the replay at 0.65 / 0.7 + matched volume)

## 10. Rank by keys, dearer items, more capital (`keys.py`, 2026-10-30)

Section 7 put most of the ceiling's keys in dear items while the agent ranks by p_up (median buy
~0.28 key). Same predictions as section 9 (mainA / mainB = the engine's model, two seed sets, 12
days), only the buy rule changed, replayed **with a key budget** the way `agent/lib/limits.js`
does it (a pick whose entry would put the held keys over `max_keys_in_play` is refused, the next
tried; a position holds its keys entry -> exit). Fills assumed, as everywhere.

**Ranking by keys does nothing.** Rank by p_up (live) vs p_up x ask vs exp_ret x ask, at 10
keys, bar 0.65: mainA 1.66 / 1.52 / 1.81 keys, mainB 1.49 / 1.70 / 1.49. The order flips
between seed sets (A vs B on the same rule differs by 0.17): noise. Few passes have more
candidates than room, so the order rarely matters.

**Raising the price cap does nothing, or loses.** At 10 keys, max ask 2 / 4 / 8 gives identical
trades. With no budget it adds 2 trades, one a -0.99-key loss (a 2-4 key item at p_up >= 0.7,
-39%): keys 2.23 -> 1.37. The model almost never flags dear items: p_up never passes 0.47 above
4 keys (28k rows), and in the 2-4 key band only 13 series in 12 days reach 0.5 (47 rows, went
up 92-100%, +3.6-4.1% labelled, too few to act on; the one row >= 0.7 lost 39%). The ceiling's
dear-item keys are not ones this model can see. Keep max_trade_keys 2.

**Capital is what binds.** Live rule (p_up >= 0.65, ask < 2), budget swept:

| budget (keys) | 4 | 6 | 8 | 10 | 12 | 15 | 20 / none |
|---|---|---|---|---|---|---|---|
| mainA: bought, keys | 90, 0.68 | 119, 0.95 | 138, 1.23 | 153, 1.66 | 167, 1.95 | 175, 2.10 | 180, 2.23 |
| mainB: bought, keys | 90, 0.71 | 118, 1.03 | 136, 1.34 | 148, 1.49 | 158, 1.67 | 165, 2.01 | 169, 2.03 |
| keys made per key of budget | 0.17 / 0.18 | 0.16 / 0.17 | 0.15 / 0.17 | 0.17 / 0.15 | 0.16 / 0.14 | 0.14 / 0.13 | 0.12 / 0.11 |

Profit grows about linearly with the budget up to ~12-15 keys (~0.15 keys per key of budget in
11.7 days, ~1.3 % a day), win rate and per-trade return do not fall (76-79 %, +2.5-2.9 %), and
it saturates at ~19 keys held (the rule finds no more). Keys without the best 2 trades track the
total. So in the replay the next keys are worth as much as the first ones until ~15. The caveat
is the one the replay cannot test: fills (one unit per listing, every counterparty accepts) --
check the live trades against the replay on the same days before scaling.

    python keys.py         # ~10 min: the 72-rule grid on c48_preds.parquet (python c48.py fit first)

## Rerun (bare `python` 3.10; xgboost on the GPU; torch CPU)

Intermediates go to `$FV2_WORK` (default: this session's scratch folder). Copy
`backpack/engine_cache/*ub6024f80*` and `account_items.parquet` into `$FV2_WORK/cache_trading`
first, or the first run rebuilds them (~5 min); the app cache builds itself (~6 min).

```bash
python run_ab.py tune,fresh main,A-lin,B-bid,A-lin+B-bid        # ~20 min; frames built on first use
python run_ab.py app24,app12 main,A-lin,B-bid,A-lin+B-bid       # ~20 min
python run_ab.py fresh,tune main@seeds3-5,B-bid@seeds3-5        # the seed-noise check
python run_ab.py tune main+hl24,main+hl48,main+hl96,main+hl192  # recency weights
python run_gru.py tune --epochs 6 --seeds 2                     # ~7 min; fresh ~22 min
python blend_gru.py tune A-lin+B-bid__s3 e6s2 0,0.2,0.35,0.5    # pick w here
python blend_gru.py fresh A-lin+B-bid__s3 e6s2 0,0.35           # score it once
python combo.py app24,app12 main,B-bid                          # both DBs, ~25 min
python bar_scan.py fresh main__s3 0.005,0.01,0.015,0.02,0.03
python ceiling.py fresh        # model vs the perfect-foresight ceiling
python run_rank.py tune R-ndcg-abs,R-ndcg-pct,R-pair-raw,R-ndcg-abs-ask0.1   # needs tune__main__s3
python run_rank.py tune R-pair-raw-topk,R-pair-raw-mcw100,R-ndcg-abs-mcw100
```

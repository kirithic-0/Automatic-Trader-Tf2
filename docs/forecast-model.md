# Buy-level forecast — the Stage-5 model: measurements

> Split out of `data-model.md` on 2026-11-07, unchanged; "above" and "below" in the text may mean
> sections still in that file. **~50 KB: grep `^#` and read the section you need.**

## Buy-level forecast (the Stage-5 model, built 2026-09-25)

The question (user, 2026-09-25): for items whose sell order is only a little above the
buy orders, will the **buy-order price (median / average) go up**, so buying at the
ask now and selling when it rises pays? Code: `engine/forecast/`, screen
`python -m engine forecast [--backtest]`. The thresholds below were **chosen by the
agent** (the user said not to be asked) — each is a parameter; revisit them.

> **Current state (2026-09-26, after the user's review — read this first; the paragraphs
> below are the dated log of how it got here).** Buy at the fenced best ask of a book
> whose ask is ≤ 5% over the best bid and **at least 0.1 key**; sell into the best bid
> **that fills** (`bid1_fill` — any account but the two unstable ones) the first pass it
> is ≥ **ask +4%**, at that bid's price; else to it at 48 h, waiting ≤ 24 h for a buyer.
> Misread bids are blanked first. BUY = `exp_ret > +1%`; `p_up` answers the question as
> asked (the held buy-order median reaching ask +2%). **Backtest** (test days 09-20 07:23
> → 09-24 07:23, data to pass 189): AUC 0.720, top-10 +2.20%, **167 trades, 49.7% win,
> +0.47% per trade, +1.17 keys** — "The user's review, 2026-09-26" below (before the
> review: 511 trades, +1.70%, +1.53 keys, no minimum price, selling into any bid). The
> honest check is the **forward test** from `RECIPE_FROZEN_AT` = 2026-09-23 23:00 UTC
> (`forecast --backtest --since`, or the dashboard's Forecast tab): 3 passes so far. Seed
> noise ±0.13% top-10; compare variants at a matched number of trades. **Expect the
> forward test below the backtest**: the recipe is the best of ~5 serious variants tried
> on the same 4 days, and picking the best of 5 noisy estimates (sd ~0.2% per trade)
> flatters it by roughly +0.2%; a regime change on top of that is not in the numbers at
> all. The knobs were agent-chosen and accepted by the user on 2026-09-26. Open: the
> misread bids (a question to the user) and a per-account untrusted list (design pending).

**Definitions.**
- **Universe**: a two-sided book with `0 ≤ spread ≤ 5%`, `spread = (ask1 − bid1) / bid1`.
  Crossed books (ask below bid) are left out — why they exist is not in the KB.
- **Buy-order level** = `bid_med`, the median of the fenced bids (all of them on the ≤ 7-deep
  classifieds books this was built on; the top 7 valid on app books since 2026-10-17 —
  see the read rules) (new `BookRead` field; `ask_med`, `ask1_bot` = cheapest bot ask were added beside it — no rule
  change). **Held** = the lower of its value at a pass and at the series' next pass
  (≤ 3 h later), so a one-pass flicker never counts.
- **`up_48h`** (what `p_up` predicts) = the held level reaches **ask1 × 1.02** at some
  pass in (t, t+48 h] — at least half the buy orders then pay 2% over today's ask. NaN
  when the series is not observed to t+48 h.
- **`ret_48h`** (what `exp_ret` predicts) = the trade: buy at ask1; **sell into the best
  bid the first pass it is ≥ ask1 × 1.04 (+5% until late 2026-09-26), at that bid's own
  price** (a bot's buy order,
  one-pass overcut spikes included — the fill rule below); else sell to the best bid at
  the last pass ≤ t+48 h — and if there is no bid then, **hold up to 24 h more for the
  first buyer**, else mark at the last bid seen. Misread bids (a thin book whose median
  is > 3 × the ask) are blanked before anything reads them, and a bid above **3 × the
  entry ask** is never a sale (see "The misread guard" below). **BUY** = `exp_ret > +1%`.
  (Until 2026-09-26: sell at exactly ask1 × 1.05 once the best bid *held* there two
  passes, BUY above +0.5% — the table below was measured under that rule; the revision
  after it gives the current numbers.)
- Models: xgboost classifier (`p_up`) + pseudo-Huber regressor (`exp_ret`), 3 seeds,
  trained on rows with spread ≤ 10% (more data; measured better than ≤ 5%) over 132
  features: the 25 engine FEATURES, 43 buy-level features, 52 per-book bot aggregates
  and 12 bot churn sums. Retrained on every run (~1 min).

**The data it stands on** (pass 176, 2026-09-25 16:17 UTC): 440,504 Unique+Strange
snapshots, 172 passes since 2026-09-14; the full 2,870-series roster only since pass
50 (2026-09-18 14:01) — about **7 days**, the binding limit on everything below.
Spread over two-sided books: crossed 3.0%, **exactly zero 14.3%**, (0, 2%] 17.5%,
(2, 5%] 23.5%, (5, 10%] 22.3%, (10, 20%] 12.4%, > 20% 6.9% → the universe is 55% of
two-sided books. Zero-spread books are cheap items at scrap-quantised prices (median
bid1 0.052 keys vs 0.25 overall), the two sides almost never the same account
(0.02%). Per pass the level is unchanged 70.4% of the time (up 22.9%, down 6.7%);
bid1 75.4% (19.9 / 4.7); ask1 77.6% (up 5.3%, **down 17.1%** — the undercut race).
Key rate constant at 64.555 ref the whole period, so every move is a real ref move.
`up_48h` base rate by spread: zero 11.4%, (0, 2%] 8.0%, (2, 5%] 6.0%, (5, 10%] 3.7%
(crossed 35%); 7.4% over the universe's test days.

**Walk-forward backtest** (purged: each test day scored by models trained only on rows
whose label was fully known before it — t + 48 h plus the one pass (≤ 3 h) the held
check reads; 4 test days 2026-09-19 16:07 → 09-23 16:07 UTC):

| test day | train rows | base | AUC | prec@5 | prec@10 | prec@20 | ret top-10 | ret tightest-10 | ret all |
|---|---|---|---|---|---|---|---|---|---|
| 09-19→20 | 9,605 | 8.4% | 0.734 | 50.0% | 42.8% | 36.9% | +1.28% | −0.51% | −2.15% |
| 09-20→21 | 52,467 | 6.6% | 0.717 | 53.0% | 46.0% | 38.2% | +1.12% | −1.07% | −2.37% |
| 09-21→22 | 92,831 | 6.8% | 0.722 | 38.0% | 42.0% | 38.7% | +1.71% | −0.66% | −2.22% |
| 09-22→23 | 126,158 | 7.8% | 0.697 | 40.0% | 38.9% | 37.6% | +1.20% | −0.47% | −2.35% |
| **mean** | | **7.4%** | **0.717** | **45.3%** | **42.4%** | **37.9%** | **+1.33%** | −0.67% | −2.27% |

(A first run purged at t + 48 h only let a training label's held check read up to 3 h
into the test day; closing that took the mean AUC from 0.721 to 0.717 and the
portfolio from +0.70% to +0.62% per trade — the figures here are the clean ones.)

Buying every universe item loses 2.3% (the spread plus drift); buying the 10 tightest
spreads still loses 0.7%. **Portfolio** (each pass buy up to 10 items with
`exp_ret > +0.5%`, never two of one item at once, one unit each): **573 trades, 40%
win, +0.62% per trade, positive on all 5 days** (+0.18%, +0.73%, +0.68%, +0.56%,
+0.67%), +1.30 keys total. 21% hit the +5% take-profit, 36% end flat (zero-spread
items that did not move), 23% lose; median hold 47 h. The entry bar trades volume for
quality: `exp_ret > 0` → 727 trades, +0.35%; `> 1%` → 403, +0.88%; `> 2%` → 180,
+1.52% (all 5 days positive each). The picks skew cheap (median entry 0.065 keys; 73%
zero-spread, 23% Strange); every price bucket is positive, and the 43 trades above
1 key (+0.90%, 65% win) carry 59% of the keys profit. 95% of BUY entries are a bot's
sell listing (an instant buy) and 99% have a bot as the best bid.

**What made the trade pay** (same 5 days, one seed, k = 10, `exp_ret > 0.5%`): a
+2% take-profit on the held **median** (the first recipe) was break-even (+0.08% per
trade, 3/5 days; −0.12% and 1/5 days without an entry bar); the held **best bid** as
the exit trigger and a higher take-profit let winners run: +2% → +0.14% (3/5),
**+5% → +0.69% (5/5)**, +8% → +0.81% (5/5), +12% → +0.93% (4/5), never (sell to the
best bid at 48 h) → +1.45% (5/5). The default +5% is the conservative end of that
plateau — the edge is the entry bar plus letting a rise run, not a precise number.

**Revision 2026-09-26 — sell into the first pass, at the bid's own price** (chosen by the
agent in a one-shot session; data to pass 178, 2026-09-25 20:03 UTC; 4 test days
2026-09-19 19:59 → 09-23 19:59; GPU xgboost). Two things the first recipe got wrong:
- **It waited for the best bid to hold two passes before selling.** Under the fill rule
  a one-pass overcut spike is a buy order you can sell into; the user's own theory of
  price rises is "random spikes in overcutting". Selling on the first pass lifts the
  top-10 return from +1.44% to +1.62% (same credit, one seed set).
- **It credited exactly +5%, not the price sold at.** Cheap items move in whole scraps
  (0.11 ref ≈ 3.4% of a 0.05-key item), so the bid that crosses +5% is often +7% — you
  sell into the bid at its price, not at the threshold. Crediting the real bid: top-10
  +2.51% (two seed sets), mean take-profit sale **+7.2%**.
- Grid (unheld trigger, bid credit, two seed sets each; per trade at `exp_ret > 1%`):
  TP +3% → +1.43% (549 trades, 1.82 keys, mean hold 35 h); **+5% → +1.66% (479, 1.94
  keys, 40 h)**; +8% → +1.78% (388, 1.44, 44 h); +12% → +1.75% (334, 1.17, 46 h). A
  plateau again; +5% kept (most keys). Horizons 24 h / 36 h / 48 h were re-run with the
  one-pass trigger (threshold credit, TP 5–12% and none) on the same test days: 24 h
  earns more **per day held** (+1.2–2.1%/day vs +0.4–0.8%) but its top-10 fell to
  +0.2% / −0.1% on one test day; 48 h is the steadiest (every fold ≥ +1.2% top-10 at
  every TP) and kept.
- Also tried and rejected for `exp_ret` (top-10 return, TP 8% trigger): squared error
  +1.47%, pseudo-Huber slope 0.05 +1.65%, absolute error +1.51%, 30% quantile +0.92%,
  classifiers on ret > 0 / ≥ TP / ≥ +1% (+0.48% / −0.10% / +0.89%), XGBRanker pairwise
  +1.61% (but +0.24% on one day), lambdaMART-ndcg +0.69% — **the pseudo-Huber 0.02
  regressor stays** (+1.70%, every day ≥ +1.46%). Training band ≤ 5% / 10% / 20% / all
  and universe ≤ 2% / 5% / 10%: all within seed noise (±0.13% top-10), unchanged.
- **BUY bar raised to +1%**: same keys with fewer trades (each trade is a manual Steam
  trade), and exp_ret sits higher now that the take-profit credit is real.

Backtest under the current rule (`python -m engine forecast --backtest`):

| test day | train rows | base | AUC | prec@10 | ret top-10 | ret tightest-10 | ret all |
|---|---|---|---|---|---|---|---|
| 09-19→20 | 15,310 | 7.8% | 0.747 | 40.5% | +2.65% | −0.12% | −2.03% |
| 09-20→21 | 56,411 | 6.6% | 0.708 | 46.5% | +2.15% | −0.40% | −2.29% |
| 09-21→22 | 96,783 | 6.8% | 0.727 | 41.7% | +2.87% | −0.13% | −2.06% |
| 09-22→23 | 132,007 | 8.0% | 0.702 | 41.0% | +2.51% | −0.82% | −2.31% |
| **mean** | | **7.3%** | **0.721** | **42.4%** | **+2.54%** | −0.37% | −2.17% |

**Portfolio** (≤ 10 buys per pass at `exp_ret > +1%`, never two of one item): **429
trades, 48% win, +1.70% per trade, +1.75 keys, positive on all 5 days** (+2.73%,
+2.34%, +1.59%, +0.85%, +1.29%; the 09-23 day lost 0.08 keys on one pricier loser
despite +1.29% per trade). 29% hit the take-profit (mean sale +7.2%), 31% end flat,
20% lose; median hold 47 h. 378 distinct items; median entry 0.057 keys; 75%
zero-spread; 21% Strange; 94% of entries are a bot's sell listing. By entry price:
≤ 0.05 keys 202 trades +2.31%; 0.05–0.1 71, +0.59%; 0.1–0.3 77, +1.23%; 0.3–1 47,
+1.56%; **1–5 keys 29 trades, +1.92%, 72% win, 1.14 keys (65% of the profit)**; > 5 keys
3 trades, −0.83%. The bar trades volume for quality: `> 0` → 768 trades, +0.96%;
`> 0.5%` → 599, +1.37%, 1.85 keys; `> 2%` → 183, +2.66%, 0.84 keys (all 5/5 days).

Seed noise, measured the same day (four sets of 3 seeds, same data): top-10 return
±0.13%, portfolio per trade ±0.03%, keys ±0.15, AUC ±0.001 — differences smaller than
that are not findings.

**The misread guard (2026-09-26).** 40 of 8,302 take-profit hits (0.48%) in the
universe were triggered by a best bid **> 5 × the entry ask** — always > 5×, never
1.5–5× (the rest sit at a median 1.06×). Examples: Exquisite Rack (Unique), ask 0.029
keys, fenced bid1 **9.0 keys** at p163–164; All-Father (Unique) 10.0 keys at p165–166;
Texas Ten Gallon (Unique) 4.0 keys at p103–104. All thin books (3 valid bids, 2 of them
fenced): when two of three bids are the misread, the median *is* the misread and the fence
keeps it. The book rules are the user's and unchanged; the forecast instead treats a
level or bid above `MAX_JUMP` = 3 × the entry ask as not a price (no up, no sale). What
those bids are is **not in the KB** — worth a look in the dashboard's book audit.
*User decision 2026-09-26: keep the guard model-only* (the book rules keep these bids;
only the forecast ignores them). One source is named: DuckMann bids for spelled items
without the spelled flag (user); the fence rejects it in almost every book, but in thin
books it gets through — removing it would take the books with a best bid > 3× the ask
from 29 to 19. Another account, Лёха Дубина ([Steam ID removed]), posts flat 4-key bids on
Texas Ten Gallon / Killer Exclusive (8 passes) — cause not in the KB.

*Found by the first forward-test pass (2026-09-26, pass 180):* that guard sat only in
the labels, so a misread still reached the **features** — one 10-key bid made All-Father
(Unique)'s 7-day best-bid high "838% over the ask" for a week, and the reach features
turned such thin books into top picks. And those books often have **no bid at 48 h**,
which left the trade's return NaN — silently dropped from every mean: 51.5% of universe
rows whose 7-day high held a misread had no known return (0.9% overall), and 6 of the
484 backtest trades. Two fixes, both in `engine/forecast/`:
- `drop_misreads` blanks every bid read (bid1, bid5, bid_med, bid_disp, gap2,
  n_near_top, top_bot) of a pass whose buy-order median is > 3 × the ask (that pass's,
  else the series' last) **before** any feature or label is built — 29 book-passes in
  5 series on 2026-09-26, but they had poisoned thousands of rows' 7-day highs.
- **No bid at the horizon** → the position is held until the first buyer, up to
  `EXIT_WAIT_H` = 24 h (measured: 76% of such rows get a sane bid within 24 h, median
  wait 5.2 h, at −2.3% median); else it is marked at the last bid seen; still unknown
  only when the data ends inside the wait. The backtest purge follows each row's real
  close time (`close_h_48h`).
The honest backtest is lower: see "Corrected 2026-09-26" below.

**Feature groups tried, 2026-09-26** (agent; each added to the 132 features, the
regressor judged on the portfolio at a *matched number of trades* — the bar moves how
many trades a model takes, so per-trade means at one bar are not comparable — with a
9-seed ensemble; plus top-10 return and the classifier's AUC with three 3-seed sets):

| group | what | AUC | top-10 | per trade @300 / @450 / @600 trades | kept |
|---|---|---|---|---|---|
| — | the 132 features | 0.721 | +2.63% | +2.03% / +1.73% / +1.28% | |
| **reach** | best bid's 24/72/168 h high vs today's ask, the level's 7-day high vs the ask, share of the last 72 h's passes whose best bid was already ≥ ask +5%, hours since | **0.726** | **+2.78%** | **+2.41% / +1.86% / +1.50%** | **yes** |
| + reach2 | 24/72/168 h mean best bid vs ask, ask vs its 7-day low / 72 h mean, level vs its 7-day mean, count of ≥ ask +5% passes in 7 days | — | +2.81% | +2.64% / +1.96% / +1.48% | no (folds swing +2.4…+3.2%) |
| + cross-quality | the other quality's (Strange ↔ Unique) level moves, spread, range position | 0.725 | +3.05% | +2.47% / +1.82% / +1.32% | no (portfolio flat, prec@10 −2.7 pp) |
| gone regulars | accounts that quoted the book in ≥ 6 of the last 24 h's passes but are absent now: count, their old price vs today's level, hours gone (buy and sell side) | 0.726 | +2.56% | portfolio −0.14% per trade, fewer keys | no |
| leapfrogs | rank-1 bidder changed this pass / in 6 / 24 h (+ one-scrap changes) | 0.721 | +2.57% | = | no |
| provider waves | per provider type, the share of buy transitions raising / lowering this pass and over 6 / 24 h ("a general increase") | 0.721 | +2.60% | = | no |
| hour of day | UTC hour | 0.721 | +2.58% | = | no |

The reach features ask the question the trade asks — has the best bid been at the
take-profit lately? — and are the only group that lifts the portfolio at every volume
and every fold (worst test day +2.68% top-10 vs +2.23% without). The rank-1 bidder
changes in 55% of passes (ties at the top price reorder), which may be why leapfrogs
carry nothing. Seed noise for comparison: ±0.13% top-10.

Backtest with the reach features (`forecast --backtest`, 2026-09-26, data to pass 179,
test days 2026-09-19 20:59 → 09-23 20:59 — an hour later than the table above):

| test day | train rows | base | AUC | prec@10 | ret top-10 | ret tightest-10 | ret all |
|---|---|---|---|---|---|---|---|
| 09-19→20 | 17,199 | 7.6% | 0.760 | 38.4% | +2.85% | −0.21% | −2.06% |
| 09-20→21 | 58,428 | 6.7% | 0.713 | 58.5% | +2.49% | −0.93% | −2.28% |
| 09-21→22 | 96,783 | 6.8% | 0.732 | 33.3% | +2.99% | −0.45% | −2.07% |
| 09-22→23 | 133,980 | 8.1% | 0.704 | 42.4% | +2.86% | −0.59% | −2.32% |
| **mean** | | **7.3%** | **0.727** | **43.2%** | **+2.80%** | −0.55% | −2.18% |

Portfolio (`exp_ret > +1%`, ≤ 10 per pass): **484 trades, 46% win, +1.66% per trade,
+1.90 keys, every day positive** (+1.42%, +2.14%, +1.28%, +1.84%, +1.50%; keys
+0.02 / +0.29 / +0.84 / +0.37 / +0.38).

**Also tried on the reach model, 2026-09-26, none kept** (6 seeds each; per trade at
450 trades / keys, base = +1.71% / 1.96 keys, top-10 +2.73%):
- *Recency weights* on training rows (`0.5 ** (age / HL)`): HL 48 h +1.50%, 96 h
  +1.61%, 168 h +1.68% — the whole history is worth more than the recent part.
- *xgboost settings*: depth 3 +1.77%, depth 6 +1.61%, min_child_weight 50 +1.77% (1.62
  keys) / 200 +1.70%, 300 trees +1.72%, 800 trees +1.51%, 1,000 trees at lr 0.015
  +1.65%, colsample 0.5 +1.75%, subsample 0.6 +1.68%, lambda 20 +1.75%. Less
  regularisation (depth 6, 800 trees) overfits; more is a wash — defaults kept.
- *A take-profit per item* (one regressor per TP of 3 / 5 / 8 / 12% / none, buy with the
  best predicted): top-10 +2.91% vs +2.79%, but at bar 1% 618 trades +1.38% 1.53 keys vs
  484, +1.66%, 1.90 keys (≈ +1.77% at 484 trades, fewer keys) — the max of five
  predictions is optimistic, and it picks the +3% TP 40% of the time. Not worth five
  models.
- *A stop-loss* (sell into the best bid once it is ≤ entry −5 / −10 / −20%): per trade
  +1.33% / +1.49% / +1.49% at bar 1% (300 / 414 / 396 trades, 0.96 / 1.17 / 1.49 keys)
  vs +1.66% (484, 1.90 keys) — on step-like bot prices a dip is usually the entry
  signal, not a reason to leave.
- Losers vs winners (bar 1%, the 429 trades of the exit-rule backtest): the entry signal is the same (zero spread,
  bot ask, level ~6% under its 72 h high); losers carry a *higher* `p_up` (0.38 vs
  0.33, the volatile items) and a lower `exp_ret`: `exp_ret` 1–1.5% → 26% of trades
  lose, 2–3% → 11%, 3–5% → 8% — the bar is the lever, not a filter.

**Current backtest (take-profit +4%, 2026-09-26, data to pass 181, test days 09-19
22:59 → 09-23 22:59):** AUC 0.728, precision@10 40.2%, top-10 **+3.16%** (every day ≥
+3.02%; tightest-10 −0.51%, all −2.19%); portfolio **511 trades, 51.3% win, +1.70% per
trade, +1.53 keys, every day positive** (+3.80% / +1.82% / +1.32% / +2.09% / +1.53%);
2 trades still waiting for a buyer at the data's end. Cut into **8 half-day test blocks** (models retrained
every 12 h): the top-10 and the portfolio are positive in **8 of 8** (portfolio +1.29%
to +2.47% per trade per block, 27–87 trades each; top-10 +1.51% to +4.19%; AUC 0.70–0.75).

**Corrected 2026-09-26 (misreads blanked, no-bid exits held for a buyer; data to pass
180, test days 2026-09-19 21:59 → 09-23 21:59).** Every number above this paragraph that
was measured on 09-26 dropped the unknown-return trades; this is the honest backtest
(`forecast --backtest`):

| test day | train rows | base | AUC | prec@10 | ret top-10 | ret tightest-10 | ret all |
|---|---|---|---|---|---|---|---|
| 09-19→20 | 19,085 | 7.5% | 0.761 | 38.4% | +2.85% | −0.90% | −2.08% |
| 09-20→21 | 60,372 | 6.7% | 0.715 | 55.0% | +2.76% | −0.78% | −2.28% |
| 09-21→22 | 98,726 | 6.8% | 0.733 | 37.2% | +2.29% | −0.85% | −2.09% |
| 09-22→23 | 136,017 | 8.2% | 0.702 | 40.5% | +2.55% | −1.07% | −2.34% |
| **mean** | | **7.3%** | **0.728** | **42.8%** | **+2.61%** | −0.90% | −2.20% |

Portfolio (`exp_ret > +1%`, ≤ 10 per pass): **492 trades, 46.5% win, +1.52% per
trade, +1.73 keys, every day positive** (+1.16%, +2.31%, +0.80%, +1.52%, +1.28%). The
reach features re-checked on the corrected data (9-seed ensembles, per trade at 300 /
450 / 600 trades): without them +1.82 / +1.53 / +1.30% (1.05 / 1.40 / 1.35 keys), with
them **+2.12 / +1.59 / +1.34%** (1.63 / 1.63 / 1.53 keys), AUC 0.722 → 0.728, top-10
+2.64% → +2.72% with a steadier worst day (+2.52% vs +2.20%) — a smaller gain than first
measured (part of it was the dropped losers), still kept. The other 09-26 comparisons
(features, knobs) were relative, on the same flawed accounting for both sides; they are
not re-run.

**The exit rule re-checked on the corrected data** (9-seed ensembles; the reach
features recomputed for each take-profit; per trade and keys at 300 / 450 / 600 trades):

| exit | top-10 | @300 | @450 | @600 |
|---|---|---|---|---|
| 09-25 rule: +5% flat once the best bid held 2 passes (6 seeds) | +1.50% | +1.14%, 1.21 k | +0.67%, 0.91 k | +0.53%, 0.70 k |
| first pass, bid's price, TP +3% | +2.93% | +2.36%, 1.49 k | +1.67%, 1.51 k | +1.29%, 1.36 k |
| first pass, bid's price, TP +4% | **+3.20%** | +2.36%, 1.44 k | **+1.84%**, 1.48 k | **+1.54%**, 1.39 k |
| **first pass, bid's price, TP +5% (default)** | +2.72% | +2.12%, **1.63 k** | +1.59%, **1.63 k** | +1.34%, **1.53 k** |

The new exit is worth about +1% per trade over the old one on its own. Re-run one pass
later (test days to 09-23 22:59, 9 seeds): +5% → +2.17 / +1.54 / +1.37% (1.66 / 1.45 /
1.53 keys), **+4% → +2.37 / +1.84 / +1.60% (1.35 / 1.46 / 1.55 keys)**, +4% under 0.3
key and +5% above → +2.38 / +1.94 / +1.52% (1.46 / 1.60 / 1.51), the reverse → +2.29 /
+1.58 / +1.31% (1.80 / 1.48 / 1.43). The keys ranking **flipped with a one-hour shift of
the window** (it is carried by ~30 trades of 1–5-key items), while +4%'s lead per trade
held in both windows, on every test day's top-10, with 3 h shorter holds. **Default
changed to +4%** (agent, 2026-09-26): the same keys, more per trade, capital turns
faster. `--take-profit 0.05` if the user prefers the higher target on dear items.

**Horizon re-checked on the clean data** (TP +4%, 9 seeds, the same test days, per trade
at 300 / 450 / 600 trades): 24 h +2.10 / +1.44 / +1.21%, 36 h +2.48 / +1.77 / +1.30%,
**48 h +2.37 / +1.84 / +1.60%**; top-10 +2.32% / +2.45% / **+3.21%** (worst test day
+1.93% / +1.45% / **+2.97%**). At the default bar 24 h takes fewer trades (295, +2.13%,
held 18 h) and earns **+2.89% per day held** vs +1.15% for 48 h (511 trades, held 35 h) —
if capital, not trades, is the constraint, `--horizon 24` turns it 2.5× faster. 48 h
stays the default: the best per trade and the steadiest.

Rejected feature groups re-run on the clean data (TP +4%, 9 seeds, per trade / keys at
300 / 450 / 600 trades; base +2.37 / +1.84 / +1.60%, 1.35 / 1.46 / 1.55 keys, AUC
0.728): gone regulars +2.51 / +1.85 / +1.49% (AUC 0.732), cross-quality +2.43 / +1.86 /
+1.47%, leapfrogs +2.34 / +1.85 / +1.56% — each wins one volume and loses another:
still noise, still out.

A **two-part expected return** (P(take-profit) × E[return | take-profit] + (1 − P) ×
E[return | none], each an xgboost model — the step before a survival model that could
also learn from the censored last 48 h): its take-profit classifier alone is good (AUC
0.742, base rate 5.8%), but the product ranks far worse than the direct regressor (top-10
+1.67% vs +3.23%, 6 seeds). Dropped, and with it the survival-model fork. Ranking by
the mean of three regressors trained on the +3 / +4 / +5% trades (and trading at +4%):
per trade +2.36 / +1.77 / +1.54% vs +2.39 / +1.82 / +1.54%, keys 1.55 / 1.44 / 1.60 vs
1.35 / 1.32 / 1.34 at 300 / 450 / 600 trades — a little more keys for three times the
training; not kept. An **item track record** (point-in-time: the mean trade return and take-profit
rate of the series' earlier rows whose labels had closed by t — median 32 such rows):
+2.44 / +1.79 / +1.58% vs +2.35 / +1.84 / +1.60%, AUC 0.7276 vs 0.7281 — noise, not
kept (the reach features already carry the item's recent history).

**Who the trades face** (corrected backtest, bar 1%, 3 seeds; the rank-1 account on
the book at entry / at the take-profit exit, provider from the registry as of
2026-09-26): we **buy from operator fleets 64%** of the time (tf2autobot 15%, unknown
provider 11%, gladiator 6%, humans 4%) and **sell into** unknown-provider bots 36%,
fleets 19%, tf2autobot 17%, gladiator 16%, humans 12%. The exits are concentrated:
134 take-profits sold into 56 accounts, **the top 5 took 48%** — two `unknown:repricing`
bots ([Steam ID removed]: 27, [Steam ID removed]: 19), a `tf2autobot:repricing` bot (7),
the `hex` fleet (6), `ctradetf` (5). Never the same account on both legs. For Stage 6
this is the fill rule's weak spot: whether those few bots take the item at their posted
price (stock limits, holds) is **not in the KB**. *Answered for the top two (user,
2026-09-26): their trades are unstable — see "Untrusted accounts"; the rest fill.*

**Liquidity features** (2026-09-26, after the forward test picked items whose bids had
been gone for hours): the share of the prior 24 / 72 h's passes that had a best bid / an
ask. Noise at every volume (+2.19 / +1.60 / +1.31% vs +2.17 / +1.54 / +1.37%, 9 seeds),
not kept — and intermittently-bid items are not worse on average (universe rows with
bids in ≤ 50% of the last 72 h's passes: −1.35% vs −2.21% for always-bid items; 27% of
them have no bid at the horizon and wait for a buyer).

**The user's review, 2026-09-26** ("go with recommended"). Kept as the agent set them:
take-profit **+4%**, BUY bar **exp_ret > +1%**, horizon **48 h**, universe **spread ≤ 5%**.
Changed: a **minimum price of 0.1 key** for a BUY and for the backtest portfolio
(`forecast.MIN_PRICE`; the models still train on every row) — the user's constraint is
the number of manual trades, and ≥ 0.1 key kept 91% of the keys with 40% of the trades
("Where the keys come from"). And the fill rule's exception: **never sell into the two
unstable accounts** (`EXIT_ON = "bid1_fill"`: the take-profit, the horizon exit and the
reach features all read the best bid that fills). Re-run (3 seeds; the test window moved
with the data, so the old window is shown too):

| exit read | test window | AUC | top-10 | portfolio, no minimum | portfolio, ask ≥ 0.1 key |
|---|---|---|---|---|---|
| bid1 (before) | 09-19 23:00 → 09-23 23:00 | 0.727 | +3.21% | 515 trades, +1.68%, +1.43 keys | 216 trades, +0.66%, +1.32 keys |
| **bid1_fill** | 09-19 23:00 → 09-23 23:00 | 0.725 | +2.27% | 447 trades, +1.35%, +1.40 keys | 215 trades, +0.87%, +1.40 keys |
| bid1 (before) | 09-20 07:23 → 09-24 07:23 | 0.721 | +2.95% | 421 trades, +1.71%, +1.23 keys | 168 trades, +0.90%, +1.11 keys |
| **bid1_fill** | 09-20 07:23 → 09-24 07:23 | 0.720 | +2.20% | 372 trades, +1.03%, +1.23 keys | **167 trades, +0.47%, +1.17 keys** |

Two readings. The unstable bots were carrying **cheap-item percentage wins** (top-10 −0.9
to −0.75 points, per trade −0.3 to −0.7 points with no minimum) but **few keys** (keys
within ±0.15 noise); above 0.1 key they barely mattered. And the per-trade figures move
by ±0.3 points when the window shifts by 8 hours — the day added (09-24, right after the
scrape gap) lost on 8 trades. **Current recipe, current window: 167 trades in 4 days
(~42 a day), 49.7% win, +0.47% per trade, +1.17 keys; keys positive on 4 of 5 days**
(09-20 +0.16, 09-21 +0.58, 09-22 +0.22, 09-23 +0.29, 09-24 −0.09).
First **forward test** (passes 155–157, 09-24 05:18–07:23 UTC — all that is labelled
since `RECIPE_FROZEN_AT` as of pass 189): AUC 0.752, top-10 +1.38% vs −2.64% for the
whole universe; portfolio 10 trades, −0.99% (−0.07 keys) — three passes are noise; a
full day needs data to ~09-27 07:00 UTC. The freeze date stays: the two changes are the
user's rules, not choices tuned on the test days.

**Forward test, second read** (2026-09-26 evening, data to pass 195; entries 09-24
05:18 → 14:18 UTC — ~10 passes, the first 6 h after the freeze are the scrape gap): AUC
0.763, precision@5 / @10 80% / 64% vs a 7.6% base, top-10 +1.31% vs −2.80% for the whole
universe; `p_up` well calibrated on these rows (said 0.34 → 31%, 0.55 → 55%). Portfolio
(bar 1%, ask ≥ 0.1): 29 trades, 48% win, −0.22% per trade, +0.09 keys. The ranking holds
out of sample; the per-trade edge at the portfolio bar does not yet (29 trades — noise).
The backtest on the same data (test days 09-20 14:18 → 09-24 14:18): AUC 0.722, top-10
+2.37%, 193 trades, 54.9% win, +0.71%, +1.47 keys; its last day (09-24) −0.55% per trade.

**Forward test, third read** (2026-10-01, data to pass 278 at 10-01 02:31 UTC; labelled
entries 09-23 23:00 → 09-29 02:16, `main` recipe, `forecast --backtest --since
2026-09-23T23:00`): AUC 0.761 (0.733–0.770 per day), precision@10 64.1% vs a 10.5% base,
top-10 +2.63% vs −2.36% for the whole universe. Portfolio (bar 1%, ask ≥ 0.1): **175
trades, 68.6% win, +1.99% per trade, +2.05 keys**, every day positive (09-24 +0.12%, then
+2.17 / +2.91 / +2.84 / +2.12%; 09-28/29 thin — 16 and 6 trades — around the scrape
stop). Read with care: the base rate rose day by day (7.5% → 12.7%), so this window had
more rising books than the tuning days, and `p_up` is now *under*-confident (said 0.44 →
54%, 0.54 → 65%); fills are still the instant-fill assumption.

**Forward test, fourth read** (2026-10-24, `trading_app.db` data to pass 313 at 10-24
13:06 UTC; `dashboard --forecast`, 339,660 labelled rows): 113 labelled passes since the
freeze, AUC 0.747, top-10 +2.47% vs −2.64% for the whole universe; portfolio (bar 1%,
ask ≥ 0.1) **299 trades, 66.9% win, +1.70% per trade, +3.30 keys** — level with the third
read (a different db, so not a strict match). **More rows did not help:** in the same
build's walk-forward, test day 10-18 (167,666 train rows) scored AUC 0.784, prec@10 83.1%,
top-10 +3.65%, and 10-21 (288,782 rows) AUC 0.756, 63.0%, +3.71% — day-to-day noise, no
trend. The backtest portfolio (+2.18% per trade, 72.9% win, 144 trades over 4 days) is far
above September's (+0.71%, 54.9%), but the base rate is 11.8–13.0% now vs 7.5% then: a
more rising market, not a better model. `p_up` is under-confident at the low end (said 4%
→ 7%, 55% → 64%), calibrated at 69%.

**Frozen vs retrained** (2026-10-26, data labelled to 10-24 15:06; seed sets 0-2 and 3-5):
one model fit on every label closed by 10-19 00:00 (167,666 rows -- with the 10-10 → 10-16
collection gap that is data to ~10-10, so 9-14 days stale over the test) scored 10-19 →
10-24 unchanged, vs the daily walk-forward on the same rows. **Ranking holds:** AUC 0.753 /
0.752 frozen vs 0.750 / 0.750, prec@10 the same. **exp_ret goes stale:** top-10 return
+2.17% / +2.14% vs +2.72% / +2.70%, behind on every day after the freeze (−0.3, −0.6, −0.5,
−0.8 pt on 10-21 → 10-24) in both seed sets; ~30% fewer picks clear the 1% bar (151 / 150
trades vs 215 / 218), better per trade (+1.96 / +1.64% vs +1.24 / +1.50%) but fewer keys
(3.43 / 2.72 vs 3.69 / 3.76). 10-20 has no test block. A freeze at 10-12 trained on the
same rows (the gap), so freshness under ~9 days is not measured. → the live model is
rebuilt from scratch daily (code-base.md, "The day's saved model").

**`p_up` at the top, and the agent's rule on stored books** (2026-10-26, `trading_app.db`
labelled to 10-24 15:06; the daily walk-forward since 10-14 → 8 test days 10-16 → 10-24, 134
passes, 201,816 out-of-sample universe rows, base rate 11.3%, AUC 0.73–0.79 a day).
**`p_up` is honest at the top:** said 0.55 → 54% went up, 0.65 → 60%, 0.74 → 69%, 0.83 →
84% (63 rows); above 0.6 it is slightly over-confident. On one live pass (p364, 10-26) the
best `p_up` of 1,590 items was 0.70 and the median 0.06: a 0.8 bar would rarely trade.
**The replay** (`python -m engine replay`, `engine/replay.py`): each pass's picks traded on
the stored books by the agent's own listing rules -- buy from the cheapest bot + instant,
trusted, fenced seller with a trade URL, skip it when dearer than the pass's `ask1`; sell
into the best such bid the first pass it is >= entry x 1.04, else from 48 h, waiting up to
24 h more. Fills assumed; at most 10 a pass; a series held until it exits:

| rule | bought | a day | won | a trade | keys | peak keys in play |
|---|---|---|---|---|---|---|
| exp_ret > 1%, ask >= 0.1 (the model's rule) | 340 | 44 | 65% | +1.4% | +4.33 | 141 |
| p_up >= 0.5, 0.1 <= ask < 1 | 267 | 35 | 64% | +0.9% | +1.13 | – |
| p_up >= 0.6, 0.1 <= ask < 1 | 130 | 17 | 79% | +2.3% | +1.32 | 14 |
| **p_up >= 0.7, 0.1 <= ask < 1 (`AGENT_RULE`)** | **61** | **8** | **92%** | **+4.2%** | **+0.90** | **8** |
| p_up >= 0.7 and exp_ret > 1%, < 1 key | 57 | 7 | 93% | +4.5% | +0.89 | 8 |

At 0.7 every day was positive (+0.4% to +6.1% a trade), the worst trade −12.9%, 77% took
the profit (median hold 18 h), none was stuck without a buyer, 9 picks skipped as dearer
than `ask1`. The replay's returns match the labels' (sold into `bid1_fill`) to 0.1 pt: the
tradable-only reads cost nothing here. **Read with care:** 61 trades over 8 days of one
market (base rate 11%, September's 7.5%); fills, and the minutes between a pass and an
offer, are untested; trade URLs are today's table (a little lookahead). The user chose
`p_up >= 0.7`, fixed for good (`forecast.AGENT_RULE`): fewer, surer trades.

**Using the picks — the trade the backtest measures.** After a pass lands: `python -m
engine forecast --buys-only` (or the dashboard's Forecast tab, bp.tf link per pick).
For each BUY: buy one unit from the fenced best ask (`ask`, usually a bot's instant
listing); then sell into the first buy order at or above `sell at` (ask + 4%) — at that
order's price, which is often a scrap higher; if none comes within 48 h, sell to the
best buy order then, and if there is none, to the first one that appears (the backtest
waits up to 24 h). A take-profit comes a median 20 h after entry; 28% of trades get one.

`p_up` calibration re-checked the same day: Platt scaling fitted on the earlier test
days' out-of-fold predictions (nested — each day calibrated only on the days before
it) lowers the Brier score 0.0639 → 0.0629 (−1.6%) and over-corrects the middle (said
0.34 → 43% happened); isotonic is worse on log-loss. The day-to-day swing of the base
rate (6.6–8.1%) is larger than the calibration error, so `p_up` stays raw.

**Bot-feature knobs** (2026-09-26, 6 seeds, per trade at 300 / 450 / 600 trades; the
defaults 48 h half-life, k = 20 give — / +1.71% / —): half-life 24 h +2.16 / +1.67 /
+1.26%, 72 h +2.22 / +1.71 / +1.28%; shrinkage k = 5 +2.20 / +1.62 / +1.25%, k = 50
+2.11 / +1.70 / +1.34%; the 48 h rates plus 24 h and 72 h copies +2.07 / +1.63 /
+1.19%; **without the five pricedb features** +2.29 / +1.70 / +1.26%. All inside the
noise: the bot aggregates' value is in *who* bids, not in how their rates are
smoothed, and pricedb adds nothing measurable (kept — harmless, and NaN-tolerant if
the pricedb leg stops). Defaults unchanged.

**What the models look at** (2026-09-26, mean |SHAP| on the last 3 days' universe, one
model fitted on all labelled rows). `exp_ret`: `spread` 21% (at a zero spread a miss
costs nothing), `spr_med` 9.5% (the ask over the buy-order median), `b1max24_over_ask`
7.7%, `askmed_over_ask1` 5.9% (one cheap seller under the rest), `b1max72_over_ask`
3.4%, `log_lvl` 2.4%, `medmax168_over_ask` 2.2%, then a long tail; by group engine 31%
/ level 35% / reach 16% / bot 19%. `p_up`: `askmed_over_ask1` 7.4%, `log_lvl` 6.3%,
`b1max72_over_ask` 6.1%, `bid1` 5.5%, `b1max24_over_ask` 3.8%, `bot_s_n_lower_24h`
3.4% (sellers cutting lately); by group 17 / 40 / 14 / 28%. In words: **buy a
zero-spread book whose best bid was higher than today's ask in the last day or three,
with a cheap seller below the rest** — a dip the bots have not yet bid back up.
Gating BUY on `p_up` (≥ 0.05 / 0.10 / 0.20) or blending it into the score (`exp_ret +
λ·p_up`) never beat `exp_ret` alone at a matched number of trades.
Ask-side microstructure (gap from the best to the 2nd ask, hours the rank-1 seller has
sat at that price, a seller new to the book in 24 h): top-10 +2.78% vs +2.70%, per trade
+2.27 / +1.68 / +1.21% vs +2.18 / +1.66 / +1.26% at 300 / 450 / 600 trades (9 seeds) —
noise, not kept. The model looks saturated on features at 7 days of history.
"Leader raised just now" (the handoff's idea: the summed / max `lead_b` of the bidders
who raised this pass, and its 6 h sum): AUC 0.7275 vs 0.7276, +2.18 / +1.70 / +1.28% vs
+2.18 / +1.66 / +1.26% — noise, not kept.

**Features re-measured after the user's review** (2026-09-26 evening, data to pass 190,
`bid1_fill` exits, test days 09-20 07:23 → 09-24 07:23). The bot layer covers **every
account** that lists, registered or not: 1,568 accounts in all history, 1,142 in the last
48 passes (530 registered bots; the rest form the `nonbot` group); 64 of the 138
features are bot aggregates. Mean |SHAP| by group — `exp_ret`: level 36%, engine 29%,
bot 18%, reach 17%; `p_up`: level 41%, **bot 27%**, engine 18%, reach 13%. Top of
`exp_ret`: `spread` 18.6%, `spr_med` 9.5%, `b1max24_over_ask` 9.5%, `askmed_over_ask1`
6.2%, `b1max72_over_ask` 3.8%; the first bot feature is `bot_b_x_prem` 1.9% (the most
overbidding account on the book). **Rank correlation with the realised trade return**
(193,952 universe rows): `spread` −0.38, `spr_med` −0.36, `b1max24_over_ask` +0.36,
`b1max72_over_ask` +0.30, `medmax168_over_ask` +0.27, `askmed_over_ask1` +0.26,
`h_since_b1_tp` −0.17; the strongest bot features `bot_s_top_cut1` +0.16 and
`bot_s_top_prem` +0.16 (the rank-1 *seller* undercutting by one scrap / sitting under the
median). **Ablation, 6 seeds:** without the 64 bot features AUC 0.715 vs 0.720, top-10
+2.19% vs +2.17%; portfolio (bar 1%, ask ≥ 0.1) +0.89% / 171 trades / +2.69 keys vs
+0.58% / 164 / +1.23 keys — but one 16.5-key Battle Balaclava (Strange) trade is +1.34
keys of that gap (without each side's top 3 trades: +1.09 vs +0.72 keys), and at a
matched number of trades the lead flips (~100: +1.60% with vs +1.20% without; ~250:
+0.23% vs +0.33%). So the bot features help `p_up` a little and are **noise for the trade
model** now — the 09-25 "biggest add" predates the reach features, which carry much of the
same signal. Kept (harmless, and `p_up` uses them). Keys totals rest on a handful of
1–17-key trades: judge variants per trade at matched volume.

**More data is the lever** (2026-09-26, 6 seeds): training on a random 50% / 75% / 100%
of the series (whole histories) gives AUC 0.704 / 0.707 / 0.728 and +1.60 / +1.64 /
+1.71% per trade at 450 trades (1.48 / 1.76 / 1.96 keys) — still climbing at 100%.
Training that ends an extra 24 h before each test day drops to AUC 0.690 and +1.30%,
mostly because the first test day is left with 3.6k training rows (top-10 +0.40% that
day). So the model should keep improving as the scraper adds days; a survival model
that also learns from the last 48 h of rows (censored today) would add ~35% more rows
— worth ~+0.05% per trade by this curve, not yet worth its complexity.

**What a trade looks like** (reach model, bar 1%, 484 trades): **28% take profit —
median 20 h after entry** (10% within 3 h, 57% within 24 h, 90% within 41 h), selling
at **+7.25%** on average (47% of them at ≥ +7% — the scrap step); the other 72% are
sold to the best bid at 48 h: 45% of those flat, 28% down, 25% up but short of +5%,
mean −0.58%.

**Where the keys come from** (reach model, bar 1%, 6 seeds, 484 trades, +2.00 keys):
ranking the buys by expected keys (`exp_ret × ask1`) instead of `exp_ret` changes
almost nothing (487 trades, +2.04 keys) — fewer than 10 items clear the bar in most
passes, so the order rarely matters. A **minimum price** does matter if the number of
trades is the constraint (each is a manual Steam trade): `ask ≥ 0.1 key` keeps **91% of
the keys with 40% of the trades** (193 trades, +1.23%, 59% win, +1.82 keys); `≥ 0.3`
keeps 82% with 22% (106 trades, +1.65 keys); `≥ 1 key` 36 trades, +1.15 keys, one
losing day. At bar 2% and `≥ 1 key`: 11 trades, +5.07%, 82% win (+1.22 keys) — too few
to trust. Which constraint binds (capital, trades, time) is the user's, not in the KB;
`forecast --min-price K` already filters the picks.

**Is there "a general increase"?** (the user's second theory of rises, measured
2026-09-26 over the full roster since pass 50): the median series' buy-order level is
exactly where it was on 09-18 (index change 0.0% every day); the **mean** has drifted up
+0.3% (09-18) → +1.6% (09-24) → +1.0% (09-25). Each day 27–34% of series sit > 0.5%
above their level 24 h earlier and 20–22% below — ups beat downs about 3 : 2 — except
09-25 (27% vs 26%). So rises are item-level events, not a tide; the tide is a slight
upward lean in the tail.

**Ablation** (final policy; AUC / prec@10 / top-10 return / portfolio per trade):
engine FEATURES alone 0.686 / 37.6% / +0.99% / +0.51% (4/5 days); + buy-level
features 0.709 / 36.3% / +1.33% / +0.47% (4/5); **+ bot features 0.717 / 42.4% /
+1.33% / +0.62% (5/5)**; + TimesFM-3 0.716 / 43.6% / +1.27% / +0.64% (5/5). What the
model mostly leans on (gain, SHAP): the ask or level **below its recent 24–72 h high**
(`ask_vs_max24`, `lvl_vs_max72`, `bid_vs_24h_high`), a **cheap seller under the rest**
(`askmed_over_ask1`), a zero spread — a recovery after a dip — with the bot aggregates
adding who is bidding.

**`p_up` is over-confident above ~0.3** (test days: said 0.35 → 24% happened, 0.54 →
38%, 0.67 → 45%; below 0.2 it is fair). An isotonic fix fitted on the last training
day made it worse (Brier 0.0670 vs 0.0650), so it is reported raw and the backtest
prints the reliability line. BUY uses `exp_ret`, which calibration does not touch.

**TimesFM-3** (Google, 330M, released 2026-08; weights non-commercial, ADR 0004):
zero-shot forecasts of the hourly as-of level (context 256 h, horizon 48 h, log
scale; 308k forecasts in 380 s on the RTX 4070 Ti). Its median forecast is **flat**
on these step-like bot prices (AUC 0.50 alone); upper quantiles restated against the
ask reach AUC 0.63 (mostly the spread again); as model features, no consistent gain
(table above). The code was removed on 2026-09-26 (user's call).

**Fill rule (user decision 2026-09-26):** a trade always fills instantly — you can buy
from any sell order inside the sell fence and sell into any buy order inside the buy
fence, at its price. The backtest already assumed exactly this (entry at the fenced
ask1, exit at the fenced bid1 or the take-profit reached on it), so no number changes.
Whether a bot's buy order really takes your item (stock limits, bot offline, trade
holds) stays not in the KB — the rule stands in for it. For reference, ask1 is a bot
in 83% of books and 95% of BUY entries. Trading costs: none assumed (not in the KB).
Profits are per one unit: +1.30 keys over 573 trades on
items with a median price of 0.065 keys — small in absolute terms. Everything rests
on 7 days.
*Refined by the user later on 2026-09-26:* the level-1 accounts in "Untrusted accounts" are
the exception — a sale into their bid is never counted (`bid1_fill`); every other order
still fills instantly.

### Feature drift against the live model's training data (measured 2026-10-30)

From `engine panel`'s stats cache (`engine/stats.py`: PSI over fixed decile bins, missing a
bin of its own; trading_app.db, 447 passes from 2026-10-04). The last 24 h of passes (to
13:06 UTC) against every pass up to the live model's last training label (2026-10-27 17:06
UTC), on the rows the model learns from (spread ≤ 10%): of the 138 `FORECAST_FEATURES`,
**21 drifted** (PSI ≥ 0.25), 17 moving (0.1–0.25), 100 stable.
- **Most of the worst is missingness, not values.** 10 of the 21 drifted features were
  missing at least 5 points more often in the training rows than now: the 72 h changes
  (`lvl_chg_72h`, `b1_chg_72h`, `askmed_chg_72h`, `ask1_chg_72h`, PSI 1.96–3.19) were missing
  in 36% of training rows and in 0–0.3% now; the 24 h ones (`bid_chg_24h` and its kin,
  PSI ~0.9) in 12% vs 0%. The cause is the history, not the market: a lag feature is
  missing for the first 24 / 72 h of collection and after every collector gap. The live
  model has seen these features missing far more often than it now does.
- `market_up_share` (2.14) and `mkt_lvl_up_share` (1.92) carry one value per pass for every
  item, so a day of them is ~24 distinct values: their PSI swings far more than an
  item-level feature's.
- The largest value drift: `bot_s_x_raise` (1.48; the max raise rate among the sellers' accounts).
- Not checked yet: whether any of this costs the model accuracy. Drift is a flag to look,
  not a loss.

The scrape itself, the same 24 h against the 7 days before: all 27 `SCRAPE_COLS` stable.
Over the whole history, the per-day heatmap's biggest day is **2026-10-16** (worst column
PSI 0.33), smaller ones 10-06, 10-10 (0.18) and 10-17 (0.12); their cause is not looked
into.

### What the picks are: a cheap ask on the bid, the other sellers ~11% above (measured 2026-10-31)

The user saw the lowest ask jump at the "bought" mark on most trade charts (panel Books tab)
and asked whether the model finds one cheap seller under the rest. Measured on the 31 live
buys (agent.db + the book the agent checked the price on, `offers.watch_id`, and the next
stored book) and on honest scores: the walk-forward recipe, 8 test days 10-21 15:06 ->
10-29 14:06 UTC, 192 passes, 308,492 universe rows, 540 rows passing AGENT_RULE (134 items),
SHAP = xgboost `pred_contribs` of the p_up classifier averaged over the 3 seeds.

- **The jump on the chart is our own buy.** The listing bought was the cheapest fenced ask in
  31 of 31 buys, gone from the next book in 28; in 22 the next lowest ask was the old
  second-cheapest. At the buy: spread (ask1 / bid1) median 0.5%, exactly 0 in 11; the median
  of the top-7 asks 11.1% over ask1; the second ask 4.4% over (0 in 9 -- 2-3 sellers at the
  same low price; one lone seller in 21 of 31).
- **The 5% universe does not select it; the model does.** 59% of two-sided books pass the
  spread rule, and in them `ask_med / ask1` is median 0.7%, >= 5% in 9.7%. Of the picks,
  85.7% have >= 5% (median 12.8%, spread median 0.5%).
- **The gap predicts the label.** Universe up rate by `ask_med / ask1`: 5.5% (<= 1%), 9.6%
  (1-3%), 13.3% (3-6%), 17.5% (6-10%), 30.6% (10-15%), 38.8% (15-25%), 31.2% (> 25%). The
  label is relative to today's ask (the level reaching ask x 1.02), so an ask far under the
  item's other sellers and its recent highs is easier to clear. Alone it is no trade: the
  540 biggest-gap rows (same price band, no model) are up 30.9%, ret -0.86%; the model's
  540 picks up 65.4%, ret +1.20% (base up rate 8.8%). AUC alone: `ask_med/ask1` 0.660,
  ask5/ask1 0.677, `b1max72_over_ask` 0.679, -spread 0.613; p_up 0.780.
- **SHAP, the picks' log-odds over an average universe row (+3.72):** `askmed_over_ask1`
  +1.12 (30%); bid/level highs over today's ask +0.95 (25%: `medmax168_over_ask` +0.43,
  `b1max168_over_ask` +0.36); ask moves +0.52 (14%: `ask_vs_max72`, `askmed_chg_6h/24h` --
  the other sellers rose lately, the cheapest ask is under its 72 h high); bot features +0.45
  (12%); price level +0.33 (9%); spread / pricedb +0.17; bid-level momentum +0.03. In the
  live buys `askmed_over_ask1` is in the top 3 drivers of 30 of 31, `medmax168_over_ask` 24,
  `b1max168_over_ask` 23.
- **Not one column.** Retrained without `askmed_over_ask1`, `askbot_over_ask1`,
  `ask1_is_bot`: AUC 0.776 (vs 0.780), 512 rows >= 0.65, up 67.8%, ret +1.77%, median gap
  13.2%; 346 of the 540 picks shared. The other ask-relative columns carry the same signal.
- **What happens after (honest picks).** The cheapest ask is >= 1% higher at the next pass in
  19-27% (the cheap seller usually stays a while). When the best bid reaches entry +4%
  (median 18-23 h later), the buy-order level is at entry +3.9% and the best bid only 0.7-0.8%
  over it: 83.5% of hits are the whole level up, 24.2% one bidder >= 2% out in front; the
  cheapest ask is then 11-15% over entry -- about where the other sellers were. Over all
  big-gap universe rows (>= 6%), the cheap seller gone within 6 h goes with a level change
  at 24 h of mean +1.33% vs -0.50% while it stays (medians 0): a weak link, not "it sells
  out, then bids follow". Why a seller lists at the bid price is not in the KB.
- **Two kinds of pick, same result.** A: level at its 72 h high, the best bid never at the
  take-profit in 72 h, gap median 16.3% (201 rows); B: level 10% under its 72 h high, the best
  bid at the take-profit in 53% of the last 72 h's passes, gap 10.7% (339 rows). Take-profit
  hit 54.7% vs 50.1%, ret +1.1% vs +1.2%, 5th percentile -7.0% vs -7.7%, < -10% in 4.0% vs
  4.7%. The live agent's three bad trades (Wrap-a-Khamon, Heavy Heating, Socked and Loaded)
  were all B; the 540 do not confirm B as worse.
- **The model against simple rules (2026-11-01).** Same 8 test days, the agent's price band,
  in every pass the same number of picks as the model's (540 in 185 passes):

  | ranker | up rate | take-profit hit | ret a trade | < -10% | keys (one unit each) |
  |---|---|---|---|---|---|
  | the model's p_up (138 columns, honest) | 65.4% | 51.9% | **+1.2%** | 4.4% | **+4.08** |
  | the cheap-seller gap alone | 36.7% | 26.1% | -0.8% | 6.5% | -2.34 |
  | the 7-day level high / ask alone | 54.4% | 43.3% | -0.5% | 10.0% | +0.43 |
  | both, rank-averaged | 52.8% | 40.2% | -0.7% | 8.1% | +0.68 |
  | logistic on the 2, fit in-sample | 52.2% | 39.8% | -0.9% | 11.1% | -1.21 |
  | logistic on gap, both highs, spread, fit in-sample | 54.8% | 42.8% | -0.8% | 10.2% | -0.36 |

  The model is ahead on 6-8 of the 8 days against each (+2.0 to +2.7 points a day). Even a
  simple rule fit on the test days themselves (an unfair head start) loses money. The pattern
  is simple to state but not to trade: the model's other columns decide which cheap asks get
  bought back up, and halve the share of big losses.


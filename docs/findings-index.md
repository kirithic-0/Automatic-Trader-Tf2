# Findings index: what's been tried, and the verdict

One line per finding, written 2026-11-07. **Check here before testing an idea.** A verdict is only as good
as its date. "Archive" = grep that heading in `docs/history/memory-archive.md`.

## The model

| question | verdict | detail |
|---|---|---|
| Does the model beat simple rules? (11-01) | **Yes.** Same 540 picks: +1.2% a trade, +4.08 keys; every simple rule -0.5 to -0.9%; ahead on 6-8 of 8 days | `forecast-model.md` |
| Other learners: LightGBM, MLP, ridge, Huber, GAM | Each loses to xgboost alone; more seeds only cut noise | archive `path-a-learner` |
| Item identity encodings | Hurt (-2.1, -0.4 pt). Don't add | archive `path-a-learner` |
| A-lin, B-bid, C-curve stack (09-27) | **Done, none helps.** Deleted 10-01. C-curve: AUC +0.025, top picks -0.4 to -0.7 pt (10-30). Whole-universe AUC gains don't reach the top picks | `research/forecast_v2/README.md`, `docs/extra/forecast-abc-stack.md` |
| GRU on price history | **Ruled out** (10-27): blends lose 0.6-1.5 pt a trade at matched volume | `research/forecast_v2/README.md` §8 |
| Ranking objective (XGBRanker) | Ruled out: ranks worse than the regressor | `research/forecast_v2/README.md` |
| Training on `trading.db` + app rows | AUC up, but `exp_ret`'s level shifts and worse trades clear the bar | `research/forecast_v2/README.md` |
| Bot identity, contagion, market state as features | Noise for the trade model; where orders sit matters, who they are doesn't | archive `path-c-book-shape` |
| Predicting each bot's moves | Predictable (AUC 0.85-0.90), but fails the trade kill test | `research/bot_moves/README.md` |
| Seller's cost basis as features | Inside seed noise on ~67 trades; not a win or a loss | `research/seller_cost/README.md` |
| Steam Market history (history.tf) | Win rate +5 pt, profit flat at matched volume: cuts risk, adds no profit | `research/scm_history/README.md` |
| How long a new series needs | ~72 h; under that the model abstains rather than misfires. Cosmetics only | `research/cold_start/README.md` |
| Why the picks look like they do | Cheapest ask on the bid, others ~11% above; hits are the whole bid level rising (83.5%) | `forecast-model.md` "What the picks are" |
| Acting one pass late | Kills most of the edge (+0.68% → +0.04%) | archive `path-c-book-shape` |

## The trade

| question | verdict | detail |
|---|---|---|
| Exit rule | +4% / 48 h earned the most of 12 combinations; shorter = a forced sale into a dip | `research/exit_sweep/README.md` |
| Dearer items, ranking by keys | Nothing (10-30). `max_trade_keys` stays 2; p_up never > 0.47 above 4 keys | `research/forecast_v2/README.md` §10 |
| More capital | Roughly linear to 12-15 keys, saturates ~19. Fills assumed | `research/forecast_v2/README.md` §10 |
| Lower bar: p_up 0.65 vs 0.7 | +40-50% keys/day, wins 82%; in-sample. 0.60 keeps tripping the 75% stop | `research/exit_sweep/README.md` |
| RL for the agent | Advised against: one-unit taker, ~10 honest days of data | archive `stage6-taker-agent` |
| Open: longer than the 30 s bot wait; rounding take-profit to the scrap tick | Untested | archive `stage6-taker-agent` |

## The book and the fence

| question | verdict | detail |
|---|---|---|
| Why a median-ratio fence, not IQR | 50.2% of books are point-mass clusters where IQR = 0 breaks Tukey | `data-model.md` "Reading the valid book" |
| High fliers on Unique/Strange | Rival-provider bots at a stable offset, not spell contamination | `data-model.md`, archive `buy-order-outlier-fence` |
| Is a provider one behaviour? | No: gladiator 74%, tf2autobot 68%. Group by operator fleet, else provider + pricing mode | `bots.md` |
| Why a seller lists at the bid price | Not in the KB | |

## The collector

How books were fetched from the site is not in this repository.

| question | verdict |
|---|---|
| Page-size parameter on old classifieds | None exists |
| Is the 1,300-1,400 books/min limit per IP? | No, it's the endpoint's: more IPs don't help |

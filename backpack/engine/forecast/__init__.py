"""Stage 5 -- will an item's buy orders rise above what it costs to buy one now?

The question (user, 2026-09-25): for items whose cheapest sell order sits only a little
above the buy orders, predict whether the buy-order level -- the median of the fenced
bids -- will rise, so that buying at the ask now and selling into the buy orders later
pays. Price rises come from bot behaviour (overcut spikes, groups repricing up), so the
features include who is bidding (engine.bot_features), not just the book.

    frame = forecast_frame(db)             # panel + features + labels, one row per snapshot
    report = walk_forward(frame)           # purged day-by-day backtest vs baselines
    trades = simulate(report["preds"])     # non-overlapping portfolio over the test days
    picks = predict_latest(frame)          # the latest pass's tight-spread items, ranked
    picks = predict_latest(frame, model_dir=d)   # ... by the day's saved model (rebuilt at 24 h)

Definitions (the defaults below; each is a parameter):

- **universe** -- a two-sided book with 0 <= (ask1 - bid1) / bid1 <= MAX_SPREAD: the ask
  is only a little above the bids. Crossed books (ask below bid, ~3% of books) are out:
  why they exist is not in the KB.
- **level** -- `bid_med`, the median of all fenced bids (engine.book), *held*: the lower of
  its value at a pass and at the series' next pass (<= HOLD_GAP_H later), so a level that
  lasts one pass only never counts.
- **up_<H>h** -- 1 when the held level reaches ask1 * (1 + TARGET) at some pass in
  (t, t+H]: at that point at least half the buy orders pay TARGET over today's ask.
  NaN when the series is not observed up to t+H (censored).
- **ret_<H>h** -- the trade the model is for: buy at ask1 now; sell into the best bid
  that fills (bid1_fill: the best fenced bid from an account not in
  engine.book.UNSTABLE_FILLS) the first pass it is at least ask1 * (1 + TAKE_PROFIT), at
  that bid's price -- a bot's buy order, one-pass overcut spikes included (the fill rule:
  an order inside its fence fills at its price) -- else sell to it at the last pass <= t+H;
  no bid there: hold up to EXIT_WAIT_H more for the first buyer, else mark at the last
  bid seen. `exit_h_<H>h` = hours to that exit; `close_h_<H>h` = hours until the row's
  labels are known (the backtest's purge). NaN when censored. Misread bids -- a thin
  book whose median is above MAX_JUMP x the ask (a 9-key bid on a 0.03-key hat) -- are
  blanked before anything reads them (drop_misreads), and a bid above MAX_JUMP x the
  entry ask is never a sale.
  Measured (docs/forecast-model.md): a +2% take-profit on the median was break-even
  (2026-09-25); +5% on the best bid held two passes made every test day pay; selling
  into the first pass at the bid's own price paid more again, best at +4% (2026-09-26).

Two models (xgboost, trained on labelled rows with spread <= TRAIN_MAX_SPREAD):

- `p_up` -- classifier for up_<H>h: the prediction asked for;
- `exp_ret` -- regressor (pseudo-Huber) for ret_<H>h: ranks trades by expected return,
  which prices in what a miss costs (the spread, and the drift down). A pick is a
  **buy** when exp_ret > MIN_EXP_RET and the ask is at least MIN_PRICE keys.

Every feature is point-in-time (only passes at or before t); the backtest trains on rows
whose label window closed before the test day starts (purged), so it never peeks.

The modules, in the order the data flows; import from `engine.forecast`, as before:

    config    the numbers: recipe, buy rule, model settings
    labels    what the model learns
    features  what the model sees
    frame     forecast_frame: both on one table, and the universe
    models    xgboost: fit, predict, drivers, the saved model
    live      pick (the one buy rule) and predict_latest
    backtest  walk_forward and simulate
"""
from engine.bot_features import BOOK_BOT_FEATURES, load_book_bot_features  # noqa: F401
from engine.events import STEP_PCT, label_events, observed_until, series_axis  # noqa: F401
from engine.features import FEATURES, build_features  # noqa: F401
from engine.forecast.backtest import _ap, _auc, _topk_mean, simulate, test_blocks, walk_forward  # noqa: F401
from engine.forecast.config import (AGENT_RULE, CLF_PARAMS, EXIT_ON, EXIT_WAIT_H, HOLD_GAP_H, HORIZON,  # noqa: F401
                                    LEVEL, LIVE_SEEDS, MAX_JUMP, MAX_NEW_BUYS, MAX_SPREAD, MIN_EXP_RET,
                                    MIN_PRICE, MODEL_FILE, MODEL_MAX_AGE_H, RECIPE_FROZEN_AT, REG_PARAMS,
                                    RET_FLOOR, SEEDS, TAKE_PROFIT, TARGET, TRAIN_MAX_SPREAD)
from engine.forecast.features import (CHURN_FEATURES, FEATURE_DOCS, FORECAST_FEATURES, LEVEL_FEATURES,  # noqa: F401
                                      REACH_FEATURES, _lag_change, _prev_change, _window_reduce, _window_sum,
                                      add_bot_features, add_level_features, add_reach_features)
from engine.forecast.frame import BID_READS, drop_misreads, feature_list, forecast_frame, universe  # noqa: F401
from engine.forecast.labels import (_end_exit, _first_reach, _held, close_col, exit_col, label_forecast,  # noqa: F401
                                    ret_col, up_col)
from engine.forecast.live import pick, predict_latest, whole_pass  # noqa: F401
from engine.forecast.models import (_DEVICE, _fit, _x, _xgb, device, drivers, fit_models, live_models,  # noqa: F401
                                    model_key, predict)
from engine.panel import DEFAULT_DB, SERIES, load_panel  # noqa: F401

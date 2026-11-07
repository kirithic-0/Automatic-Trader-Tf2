"""The forecast's numbers: the trade recipe, the agent's buy rule, the model settings."""

HORIZON = 48.0           # hours the position may be held
TARGET = 0.02            # up_<H>h: the held buy-order median this far over the entry ask
TAKE_PROFIT = 0.04       # ret_<H>h: sell the first pass the best bid is this far over the ask
EXIT_ON = "bid1_fill"    # ... the book read that triggers the take-profit and every exit: the best
                         #     bid whose trades go through (user, 2026-09-26; was bid1)
MAX_JUMP = 3.0           # a level / bid above this x the entry ask is a misread, not a price
EXIT_WAIT_H = 24.0       # no bid at the horizon: hold up to this long for a buyer to appear
RECIPE_FROZEN_AT = "2026-09-23T23:00"   # UTC: the last label the current recipe was tuned on --
                                        # every later day is a forward test (walk_forward since=)
MIN_EXP_RET = 0.01       # a pick is a buy when its expected return is above this
MIN_PRICE = 0.1          # ... and its ask is at least this many keys (user, 2026-09-26: trades
                         #     are the constraint -- 91% of the keys with 40% of the trades)
MAX_NEW_BUYS = 10        # at most this many new buys a pass (simulate's k; the live picker's too)
# The live agent's buy rule: p_up >= 0.65 alone, no exp_ret bar, 0.1 <= ask < 2 keys, best
# p_up first (user, 2026-10-30; was p_up >= 0.7 and ask < 1 key from 2026-10-26, when the
# account held metal only -- it now holds keys too, and the agent's key<->metal rebalance
# keeps both, agent/lib/convert.js). The replay of the 0.7 / 1-key rule on stored books
# (engine replay, data-model.md "The agent's rule on stored books"): 10-16 -> 10-24, 61
# trades, 92% won, +4.2% a trade; the new rule is not replayed yet.
AGENT_RULE = dict(min_exp_ret=None, min_p_up=0.65, min_price=MIN_PRICE, max_price=2.0, rank="p_up")
MAX_SPREAD = 0.05        # universe: the ask at most this far above the best bid
TRAIN_MAX_SPREAD = 0.10  # the models learn from a wider band (more data, measured better)
HOLD_GAP_H = 3.0         # a held level needs the next pass within this many hours
LEVEL = "bid_med"

CLF_PARAMS = dict(n_estimators=500, max_depth=4, learning_rate=0.03, subsample=0.8,
                  colsample_bytree=0.7, min_child_weight=100, reg_lambda=5.0,
                  tree_method="hist", eval_metric="logloss")
REG_PARAMS = dict(n_estimators=500, max_depth=4, learning_rate=0.03, subsample=0.8,
                  colsample_bytree=0.7, min_child_weight=100, reg_lambda=5.0,
                  tree_method="hist", objective="reg:pseudohubererror", huber_slope=0.02)
SEEDS = (0, 1, 2)
LIVE_SEEDS = tuple(range(9))   # predict_latest: 9 seeds halve the seed noise -- 2 of ~41 BUY flags
                               # flip between runs vs 5 with 3 seeds (57 s vs 19 s on the GPU)
RET_FLOOR = -0.2         # regression target floor: one crashed item must not steer the fit
MODEL_MAX_AGE_H = 24.0   # predict_latest with a model_dir: the saved live model is used until it is
                         # this old, then a new one is fit from scratch (user, 2026-10-26: a frozen
                         # model ranks as well, but its exp_ret falls behind -- top-10 -0.3..-0.8 pt
                         # a day -- so it is rebuilt daily, not on every pass)
MODEL_FILE = "live_model.pkl"

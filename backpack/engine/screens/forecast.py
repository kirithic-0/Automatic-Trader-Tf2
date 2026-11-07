"""Tight-spread items whose buy orders should rise above today's ask (the Stage-5 model).

For every item in the latest pass whose best ask is at most --max-spread above its best
bid: p_up = the chance its buy-order median (held two passes) reaches the ask + --target
within --horizon hours; exp_ret = the expected return of buying at the ask now and
selling into the best bid the first pass it is at least the ask + --take-profit (at that
bid's price), else to the best bid at the horizon; a bid from an account whose trades do not
go through (engine.book.UNSTABLE_FILLS) is never sold into. BUY = exp_ret above --min-exp-ret
and the ask at least --min-price keys. Both models train on the whole
labelled history each run. --backtest replays the same recipe day by day out of sample
(purged walk-forward) and simulates a non-overlapping portfolio.

    python -m engine forecast                 # the latest pass, best exp_ret first
    python -m engine forecast --backtest      # how the recipe did on the last days
    python -m engine forecast --backtest --since 2026-09-23T23:00   # ... since it was frozen
"""
import sys

import pandas as pd

from engine import forecast as fc
from engine.panel import cache_dir_for
from engine.screens._common import fit, parser, qualities


def _pct(x, spec="+6.2%"):
    return "-" if x != x else format(x, spec)


def _frame(args):
    frame = fc.forecast_frame(args.db, horizons=(args.horizon,), target=args.target,
                              take_profit=args.take_profit, use_cache=not args.no_cache)
    return frame[frame.quality.isin(qualities(args))]


def show_latest(frame, args):
    picks = fc.predict_latest(frame, horizon=args.horizon, max_spread=args.max_spread,
                              target=args.target, take_profit=args.take_profit,
                              min_exp_ret=args.min_exp_ret, min_price=args.min_price,
                              model_dir=cache_dir_for(args.db), retrain=args.retrain)
    if picks.empty:
        print("Nothing in the latest pass fits the universe (or no labelled history yet).")
        return
    n_all, n_buy = len(picks), int(picks.buy.sum())
    picks = picks[picks.ask1 >= args.min_price]
    if args.buys_only:
        picks = picks[picks.buy]
    t = frame.t.max()
    print(f"Latest pass p{frame.pass_id[frame.t.idxmax()]} ({t:%Y-%m-%d %H:%M} UTC): {n_all} items "
          f"with the ask 0-{args.max_spread:.0%} over the best bid, {n_buy} BUY "
          f"(exp_ret > {args.min_exp_ret:+.1%}, ask >= {args.min_price:g} keys).")
    print(f"p_up = P(buy-order median reaches ask +{args.target:.0%} within {args.horizon:g}h); "
          f"base rate {picks.attrs.get('base_rate', float('nan')):.1%} over "
          f"{picks.attrs.get('n_train', 0):,} labelled rows; the day's model, built "
          f"{(picks.attrs.get('trained_at') or '?')[:16]} UTC (--retrain to rebuild it now).")
    print(f"exp_ret = expected return: buy at ask1, sell into the best bid that fills (never an "
          f"unstable account's) the first pass it is at "
          f"least 'sell at' (+{args.take_profit:.0%}), else to the best bid at {args.horizon:g}h. "
          f"Prices in keys. --backtest for the track record.\n")
    hdr = (f"{'':3} {'item (quality)':36} {'p_up':>5} {'exp_ret':>7} {'bid1':>8} {'buy med':>8} "
           f"{'ask1':>8} {'spread':>6} {'sell at':>8}  why (exp_ret drivers)")
    print(hdr)
    print("-" * len(hdr))
    for r in picks.head(args.top).itertuples():
        print(f"{'BUY' if r.buy else '':3} {fit(r.label, 36):36} {r.p_up:5.2f} {_pct(r.exp_ret, '+7.2%')} "
              f"{r.bid1:8.4f} {r.bid_med:8.4f} {r.ask1:8.4f} {r.spread:6.1%} {r.sell_at:8.4f}  "
              f"{r.drivers}")


def show_backtest(frame, args):
    rep = fc.walk_forward(frame, horizon=args.horizon, n_folds=args.folds,
                          max_spread=args.max_spread, since=args.since)
    f = rep["folds"]
    if f.empty:
        print("Not enough labelled history for a walk-forward backtest"
              + (f" since {args.since}." if args.since else "."))
        return
    print(f"Walk-forward, purged: each test day is scored by models trained only on rows whose "
          f"{args.horizon:g}h label closed before it."
          + (f" Forward test: every labelled day since {args.since}." if args.since else "") + "\n")
    print(f"{'test day (UTC)':26} {'train':>7} {'test':>6} {'base':>6} {'AUC':>5} "
          f"{'prec@5':>6} {'prec@10':>7} {'prec@20':>7} {'ret@10':>7} {'tight@10':>8} {'all':>7}")

    def metrics(r):
        return (f"{r['base']:6.1%} {r['auc']:5.3f} {r['prec@5']:6.1%} {r['prec@10']:7.1%} "
                f"{r['prec@20']:7.1%} {_pct(r['ret@10'], '+7.2%')} "
                f"{_pct(r['ret_spread@10'], '+8.2%')} {_pct(r['ret_all'], '+7.2%')}")

    for _, r in f.iterrows():
        print(f"{r.test_start:%m-%d %H:%M} -> {r.test_end:%m-%d %H:%M}  {r.n_train:7,.0f} "
              f"{r.n_test:6,.0f} {metrics(r)}")
    print(f"{'mean':26} {'':7} {'':6} {metrics(f.mean(numeric_only=True))}")
    print("\nprec@k = share of the k highest-p_up items per pass that went up; ret@10 = mean trade "
          "return of the 10 highest-exp_ret items per pass; tight@10 = the 10 tightest spreads "
          "instead; all = every universe item.")
    p = rep["preds"]
    up = fc.up_col(args.horizon)
    rel = (p.groupby(pd.cut(p.p_up, [0, .1, .2, .3, .4, .5, .6, 1.0]), observed=True)
             .agg(n=(up, "size"), said=("p_up", "mean"), happened=(up, "mean")))
    print("\nIs p_up a fair probability? (test days)  " + "  ".join(
        f"{r.said:.2f}->{r.happened:.2f} (n={r.n:,})" for r in rel.itertuples()))
    tr = fc.simulate(rep["preds"], horizon=args.horizon, k=args.sim_k, min_exp_ret=args.sim_min,
                     min_price=args.min_price)
    if tr.empty:
        return
    print(f"\nPortfolio: each pass buy up to {args.sim_k} items with exp_ret > {args.sim_min:+.1%} "
          f"and an ask >= {args.min_price:g} keys, "
          f"never two of one item at once; sell into the best bid the first pass it is "
          f"+{args.take_profit:.0%} or more over the ask, else to the best bid at {args.horizon:g}h.")
    tr["day"] = tr.t.dt.strftime("%m-%d")
    by = tr.groupby("day").agg(trades=("ret", "size"), win=("ret", lambda s: (s > 0).mean()),
                               mean_ret=("ret", "mean"), keys=("profit_keys", "sum"))
    for d, r in by.iterrows():
        print(f"  {d}: {r.trades:4.0f} trades  win {r.win:5.1%}  mean {r.mean_ret:+.2%}  "
              f"profit {r['keys']:+.3f} keys")
    print(f"  all : {len(tr):4d} trades  win {(tr.ret > 0).mean():5.1%}  mean {tr.ret.mean():+.2%}  "
          f"profit {tr.profit_keys.sum():+.3f} keys (one unit per trade)")
    unknown = int(tr.ret.isna().sum())
    if unknown:
        print(f"  ({unknown} trade{'s' if unknown > 1 else ''} with no outcome yet -- no buyer seen and the "
              f"data ends inside the {fc.EXIT_WAIT_H:g}h wait -- left out of the means)")


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--horizon", type=float, default=fc.HORIZON, help="hours held (default 48)")
    ap.add_argument("--target", type=float, default=fc.TARGET,
                    help="p_up: the buy-order median this far over the ask (default 0.02)")
    ap.add_argument("--take-profit", type=float, default=fc.TAKE_PROFIT, dest="take_profit",
                    help="exp_ret: sell into the best bid once it is this far over the ask (default 0.04)")
    ap.add_argument("--max-spread", type=float, default=fc.MAX_SPREAD, dest="max_spread",
                    help="universe: ask at most this far above the best bid (default 0.05)")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-price", type=float, default=fc.MIN_PRICE, dest="min_price",
                    help="BUY (and the portfolio) only when the ask is at least this many keys; "
                         "cheaper items are hidden (default 0.1)")
    ap.add_argument("--min-exp-ret", type=float, default=fc.MIN_EXP_RET, dest="min_exp_ret",
                    help="BUY when the expected return is above this (default 0.01)")
    ap.add_argument("--buys-only", action="store_true", dest="buys_only")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--retrain", action="store_true",
                    help="rebuild the day's saved model now (else at 24 h old)")
    ap.add_argument("--folds", type=int, default=4, help="backtest test days (default 4)")
    ap.add_argument("--since", default=None,
                    help="backtest every labelled day since this UTC time instead: a forward test "
                         f"(the current recipe was frozen at {fc.RECIPE_FROZEN_AT})")
    ap.add_argument("--sim-k", type=int, default=10, dest="sim_k")
    ap.add_argument("--sim-min", type=float, default=fc.MIN_EXP_RET, dest="sim_min",
                    help="portfolio: only buy when exp_ret is above this (default 0.01)")
    args = ap.parse_args(argv)
    frame = _frame(args)
    if args.backtest:
        show_backtest(frame, args)
    else:
        show_latest(frame, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

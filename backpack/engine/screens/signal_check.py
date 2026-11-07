"""Which features precede a step-up: per-feature lift, plus a time-split backtest.

For every feature: the step-within-H rate in its top and bottom --frac of rows,
over the base rate (lift). Rows whose H-window is not fully observed are left
out. The backtest picks features (lift >= --min-lift) on the first --train of
history only, scores the rest with ratchet-watch's score, and reports
precision@k per pass vs the base rate -- the benchmark Stage 5 must beat.

    python -m engine signal-check --horizon 24
"""
from engine import build_frame, label_col
from engine.events import STEP_PCT
from engine.screens._common import parser, qualities
from engine.signals import FRAC, MIN_LIFT, backtest, feature_lifts, select_signals


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--horizon", type=float, default=24.0, help="H in hours (default 24)")
    ap.add_argument("--pct", type=float, default=STEP_PCT)
    ap.add_argument("--frac", type=float, default=FRAC, help="tail size (default 0.10)")
    ap.add_argument("--min-lift", type=float, default=MIN_LIFT, dest="min_lift")
    ap.add_argument("--train", type=float, default=0.7,
                    help="share of passes the backtest picks features on (default 0.7)")
    ap.add_argument("--no-backtest", action="store_true")
    ap.add_argument("--new-high", action="store_true", dest="new_high",
                    help="count only steps that beat the prior-24h high bid")
    args = ap.parse_args(argv)
    qs = qualities(args)

    lab = label_col(args.horizon, args.new_high)
    f = build_frame(args.db, pct=args.pct, horizons=(args.horizon,),
                    use_cache=not args.no_cache)
    f = f[f.quality.isin(qs)]
    known = f[f[lab].notna()]

    lifts, base = feature_lifts(known, lab, args.frac)
    chosen = {s.feature: s.side for s in
              select_signals(known, lab, args.frac, args.min_lift)}
    kind = "new-high step" if args.new_high else "step"
    print(f"step = top-1 fenced bid +{args.pct:.0%} held next pass"
          f"{' AND above its prior-24h high' if args.new_high else ''}; "
          f"label = a {kind} within {args.horizon:g}h")
    print(f"{len(known):,} labelled snapshots over {known.pass_id.nunique()} passes; "
          f"base rate {base:.2%}\n")
    hdr = (f"{'feature':18} {'cover':>6}   {'top-tail rate':>13} {'lift':>5}   "
           f"{'bottom-tail rate':>16} {'lift':>5}   chosen")
    print(hdr)
    print("-" * len(hdr))
    lifts["best"] = lifts[["high_lift", "low_lift"]].max(axis=1)
    for r in lifts.sort_values("best", ascending=False, na_position="last").itertuples():
        def cell(rate, lift):
            return (f"{'-':>13} {'-':>5}" if rate != rate
                    else f"{rate:13.2%} {lift:5.2f}")
        mark = {"high": "<- high", "low": "<- low"}.get(chosen.get(r.feature), "")
        print(f"{r.feature:18} {r.coverage:6.0%}   {cell(r.high_rate, r.high_lift)}   "
              f"{cell(r.low_rate, r.low_lift):>22}   {mark}")

    if args.no_backtest:
        return
    bt = backtest(f, lab, args.horizon, args.train, frac=args.frac, min_lift=args.min_lift)
    print("\nBacktest (features picked on early passes, scored on later ones)")
    if bt is None:
        print("  not enough labelled history to split")
        return
    print(f"  split at {bt['split']:%Y-%m-%d %H:%M} UTC; {bt['n_test_passes']} test passes; "
          f"test base rate {bt['base']:.2%}")
    print(f"  picked on train: "
          f"{', '.join(f'{s.feature}({s.side})' for s in bt['signals']) or 'none'}")
    if not bt["precision"]:
        print("  no feature cleared the bar on train -- nothing to score")
        return
    for k, (p, lift) in bt["precision"].items():
        print(f"  precision@{k:<3} {p:6.2%}   ({lift:.2f}x base)")

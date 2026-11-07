"""Items in the latest pass that look like they are about to step up.

Features are picked by lift over the full labelled history (as signal-check
does), then every item in the latest pass is scored by the average within-pass
rank of those features. `drivers` lists which picked features this item sits in
the tail of. Not a trained model; see signal-check's backtest for how well the
score has ranked in the past.

    python -m engine ratchet-watch --top 25 --horizon 24
"""
from engine import build_frame, label_col
from engine.events import STEP_PCT
from engine.panel import latest_pass
from engine.screens._common import fit, parser, qualities
from engine.signals import FRAC, MIN_LIFT, fired, score, select_signals


def _fmt(x, spec):
    return "-" if x != x else format(x, spec)


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--horizon", type=float, default=24.0, help="H in hours (default 24)")
    ap.add_argument("--pct", type=float, default=STEP_PCT)
    ap.add_argument("--frac", type=float, default=FRAC)
    ap.add_argument("--min-lift", type=float, default=MIN_LIFT, dest="min_lift")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--new-high", action="store_true", dest="new_high",
                    help="count only steps that beat the prior-24h high bid")
    args = ap.parse_args(argv)
    qs = qualities(args)

    lab = label_col(args.horizon, args.new_high)
    f = build_frame(args.db, pct=args.pct, horizons=(args.horizon,),
                    use_cache=not args.no_cache)
    f = f[f.quality.isin(qs)]
    known = f[f[lab].notna()]
    sigs = select_signals(known, lab, args.frac, args.min_lift)
    if not sigs:
        print(f"No feature clears lift {args.min_lift:g} for a step within "
              f"{args.horizon:g}h yet -- nothing to rank on. Run signal-check.")
        return

    now = f[f.pass_id == latest_pass(f)].copy()
    now["score"] = score(now, sigs)
    now = now.sort_values("score", ascending=False).head(args.top)

    print(f"Latest pass p{now.pass_id.iloc[0]} ({now.t.iloc[0]:%Y-%m-%d %H:%M} UTC). "
          f"Base rate of a {'new-high ' if args.new_high else ''}step within "
          f"{args.horizon:g}h: {known[lab].mean():.2%}")
    print("Scored on: " + ", ".join(f"{s.feature}({s.side}, x{s.lift:.2f})" for s in sigs))
    print()
    hdr = (f"{'item (quality/variant)':40} {'score':>5} {'bid1':>8} {'ask1':>8} "
           f"{'spread':>7} {'since step':>10}  drivers")
    print(hdr)
    print("-" * len(hdr))
    for _, r in now.iterrows():
        since = "-" if r.hours_since_step != r.hours_since_step else f"{r.hours_since_step:.0f}h"
        print(f"{fit(r.label, 40):40} {r.score:5.2f} {_fmt(r.bid1, '8.4f')} "
              f"{_fmt(r.ask1, '8.4f')} {_fmt(r.spread, '7.1%')} {since:>10}  "
              f"{', '.join(fired(r, sigs))}")

"""Log of detected bid step-ups (>= --pct top-1 rise that holds the next pass).

    python -m engine events --hours 24
    python -m engine events --item "steel jaw" --hours 0     # full history
"""
import pandas as pd

from engine import build_frame
from engine.events import STEP_PCT, event_col
from engine.screens._common import fit, parser, qualities


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--pct", type=float, default=STEP_PCT,
                    help=f"step bar as a fraction (default {STEP_PCT})")
    ap.add_argument("--hours", type=float, default=24.0,
                    help="look back this many hours from the last pass (0 = all)")
    ap.add_argument("--item", default="", help="case-insensitive substring filter")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--new-high", action="store_true", dest="new_high",
                    help="count only steps that beat the prior-24h high bid")
    args = ap.parse_args(argv)
    qs = qualities(args)

    f = build_frame(args.db, pct=args.pct, horizons=(), use_cache=not args.no_cache)
    f = f[f.quality.isin(qs)]
    ev = f[f[event_col(args.new_high)]]
    if args.hours > 0:
        ev = ev[ev.t >= f.t.max() - pd.Timedelta(hours=args.hours)]
    if args.item:
        ev = ev[ev.label.str.contains(args.item, case=False, regex=False)]
    ev = ev.sort_values(["t", "r_bid1"], ascending=[False, False])

    span = "all history" if args.hours <= 0 else f"last {args.hours:g}h"
    kind = "new-high step(s)" if args.new_high else "step(s)"
    print(f"{len(ev)} {kind} (top-1 fenced bid +{args.pct:.0%}, held next pass), {span}"
          f"{' matching ' + repr(args.item) if args.item else ''}:\n")
    if ev.empty:
        return
    hdr = (f"{'pass time':16}  {'item (quality/variant)':44} {'bid before':>10} "
           f"{'after':>9} {'rise':>7}  new high")
    print(hdr)
    print("-" * len(hdr))
    for r in ev.head(args.limit).itertuples():
        before = r.bid1 / (1 + r.r_bid1)
        print(f"{r.t:%Y-%m-%d %H:%M}  {fit(r.label, 44):44} {before:10.4f} "
              f"{r.bid1:9.4f} {r.r_bid1:+6.1%}  {'yes' if r.new_high else ''}")
    if len(ev) > args.limit:
        print(f"... {len(ev) - args.limit} more (--limit)")

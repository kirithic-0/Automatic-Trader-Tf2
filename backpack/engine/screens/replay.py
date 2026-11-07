"""The agent's rules replayed on the stored books: the paper test (engine.replay).

Scores every labelled test day out of sample (the purged walk-forward, a model trained only
on what was known before each day), picks pass by pass with the agent's rule, and trades
each pick on the collector's stored books by the agent's listing rules: buy from the
cheapest tradable seller (skip if dearer than ask1), sell into the best tradable bid at
the take-profit or from the 48 h horizon. Fills are assumed.

    python -m engine replay                        # the last 8 labelled days, AGENT_RULE
    python -m engine replay --min-p-up 0.6         # another bar
    python -m engine replay --rule model           # the model's exp_ret rule, any price
    python -m engine replay --since 2026-10-16     # from a date
    python -m engine replay --preds p.parquet      # reuse (or save) the predictions: the
                                                   #   walk-forward takes a few minutes
    python -m engine replay --csv trades.csv       # every pick with its outcome
"""
import os
import sqlite3
import sys

import pandas as pd

from engine import forecast as fc
from engine import replay as rp
from engine.screens._common import fit, parser, qualities


def predictions(args):
    if args.preds and os.path.exists(args.preds):
        return pd.read_parquet(args.preds)
    frame = fc.forecast_frame(args.db, use_cache=not args.no_cache)
    frame = frame[frame.quality.isin(qualities(args))]
    since = args.since
    if since is None:
        last = frame.loc[frame[fc.up_col(fc.HORIZON)].notna(), "t"].max()
        since = (last - pd.Timedelta(days=args.days)).floor("h")
    preds = fc.walk_forward(frame, since=since)["preds"]
    if args.preds:
        preds.to_parquet(args.preds, index=False)
    return preds


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--rule", choices=("agent", "model"), default="agent")
    ap.add_argument("--min-p-up", type=float, default=fc.AGENT_RULE["min_p_up"])
    ap.add_argument("--max-price", type=float, default=fc.AGENT_RULE["max_price"])
    ap.add_argument("--min-exp-ret", type=float, default=fc.MIN_EXP_RET, help="--rule model only")
    ap.add_argument("--min-price", type=float, default=fc.MIN_PRICE)
    ap.add_argument("--k", type=int, default=fc.MAX_NEW_BUYS)
    ap.add_argument("--since", help="first test day (UTC); default --days before the last label")
    ap.add_argument("--days", type=float, default=8.0)
    ap.add_argument("--preds", help="predictions parquet: read if it exists, else written")
    ap.add_argument("--csv", help="write every pick with its outcome here")
    args = ap.parse_args(argv)

    preds = predictions(args)
    preds = preds[preds[fc.up_col(fc.HORIZON)].notna()]
    if preds.empty:
        print("No labelled out-of-sample rows to replay.")
        return 1
    rule = (dict(fc.AGENT_RULE, min_p_up=args.min_p_up, max_price=args.max_price, min_price=args.min_price)
            if args.rule == "agent" else dict(min_exp_ret=args.min_exp_ret, min_price=args.min_price))
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    trades = rp.replay(preds, con, rule, args.k)
    if args.csv:
        trades.to_csv(args.csv, index=False)
    days = (preds.t.max() - preds.t.min()).total_seconds() / 86400
    s = rp.summary(trades, days)

    print(f"Replay {preds.t.min():%Y-%m-%d %H:%M} -> {preds.t.max():%m-%d %H:%M} UTC ({days:.1f} days, "
          f"{preds.pass_id.nunique()} passes), rule {rule}, at most {args.k} a pass. Fills assumed.\n")
    if not s["bought"]:
        print(f"{s['picks']} picks, nothing bought.")
        return 0
    print(f"bought {s['bought']} ({s['per_day']:.1f} a day), sold {s['sold']}, stuck {s['stuck']}; "
          f"skipped {s['skipped'] or 0}")
    if s["sold"]:
        print(f"won {s['win']:.1%}, {s['ret']:+.2%} a trade (worst {s['worst']:+.1%}), {s['keys']:+.3f} keys; "
              f"{s['take_profit']:.0%} at the take-profit, held {s['hold_h']:.0f} h median, "
              f"peak {s['peak_keys']:.2f} keys in play")
        sold = trades[trades.status.isin(rp.SOLD)]
        print("by day: " + ", ".join(f"{d:%m-%d} {len(g)} {g.ret.mean():+.1%}"
                                    for d, g in sold.groupby(sold.t.dt.floor("D"))))
        print("\nworst five:")
        for r in sold.nsmallest(5, "ret").itertuples():
            print(f"  {r.t:%m-%d %H:%M}  {fit(r.label, 40):40} {r.entry:.4f} -> {r.exit:.4f} {r.ret:+.1%}  {r.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

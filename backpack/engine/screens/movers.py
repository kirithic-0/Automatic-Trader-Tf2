"""Items whose fenced top-5 bid rose within a % band, first record -> now.

Per series, compares the first pass with a bid to the most recent one, and keeps
only tight books: the top-5 fenced bids' spread (max-min)/median must be
<= --maxspread at both ends. (The pre-engine bid_movers.py averaged the UNFENCED
top 5, so one 50-key flier could read as a giant mover; this reads the fenced bid.)

    python -m engine movers --lo 10 --hi 30 --trace 5
"""
from engine.panel import SERIES, load_panel
from engine.screens._common import fit, parser, qualities


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--lo", type=float, default=10.0, help="lower %% bound")
    ap.add_argument("--hi", type=float, default=30.0, help="upper %% bound")
    ap.add_argument("--maxspread", type=float, default=0.15,
                    help="max (max-min)/median of the top-5 at BOTH ends (default 0.15)")
    ap.add_argument("--trace", type=int, default=0, metavar="K",
                    help="also print pass-by-pass bid for the top K movers")
    args = ap.parse_args(argv)
    qs = qualities(args)

    p = load_panel(args.db, use_cache=not args.no_cache)
    p = p[p.quality.isin(qs) & p.bid5.notna()]

    rows, noisy = [], 0
    for _, s in p.groupby(SERIES, sort=False):
        if len(s) < 2:
            continue
        a, b = s.iloc[0], s.iloc[-1]
        if a.bid5 <= 0:
            continue
        pct = (b.bid5 - a.bid5) / a.bid5 * 100.0
        if not (args.lo <= pct <= args.hi):
            continue
        if max(a.bid_disp, b.bid_disp) > args.maxspread:
            noisy += 1
            continue
        rows.append((pct, a, b, s))
    rows.sort(key=lambda r: r[0], reverse=True)

    band = f"{args.lo:g}-{args.hi:g}%"
    if not rows:
        print(f"No items with a {band} fenced top-5 bid increase.")
        return
    print(f"Items with a {band} rise in fenced top-5 bid (first record -> now),")
    print(f"tight books only (top-5 spread <= {args.maxspread:.0%} of median at both ends):\n")
    hdr = (f"{'item (quality/variant)':44} {'first':>9} {'now':>9} "
           f"{'change':>9} {'spread':>7}   window")
    print(hdr)
    print("-" * len(hdr))
    for pct, a, b, _ in rows:
        print(f"{fit(a.label, 44):44} {a.bid5:9.3f} {b.bid5:9.3f} {pct:+8.1f}% "
              f"{max(a.bid_disp, b.bid_disp):6.1%}   "
              f"p{a.pass_id} {a.t:%Y-%m-%d %H:%M} -> p{b.pass_id} {b.t:%Y-%m-%d %H:%M}")
    print(f"\n{len(rows)} item(s) in the {band} band with a tight book "
          f"({noisy} in-band item(s) dropped as too noisy).")

    for pct, a, _, s in rows[:args.trace]:
        print(f"\n{a.label}  {pct:+.1f}%")
        for r in s.itertuples():
            d = (r.bid5 - a.bid5) / a.bid5 * 100.0
            bar = "#" * int(round(d / 2)) if d > 0 else ""
            print(f"    p{r.pass_id:<4} {r.t:%Y-%m-%d %H:%M}  {r.bid5:8.3f}  {d:+6.1f}%  "
                  f"spread {r.bid_disp:5.1%}  {bar}")

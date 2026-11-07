"""Top items by fenced top-5 bid % rise, first record -> now (no band).

    python -m engine gainers --top 25 --minpct 5
"""
from engine.panel import SERIES, load_panel
from engine.screens._common import fit, parser, qualities, quality_names


def rank_gainers(p, minpct=0.0):
    """[(pct, first_row, last_row)] by fenced top-5 bid rise, biggest first."""
    p = p[p.bid5.notna()]
    rows = []
    for _, s in p.groupby(SERIES, sort=False):
        if len(s) < 2:
            continue
        a, b = s.iloc[0], s.iloc[-1]
        if a.bid5 <= 0:
            continue
        pct = (b.bid5 - a.bid5) / a.bid5 * 100.0
        if pct >= minpct:
            rows.append((pct, a, b))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--top", type=int, default=25, help="how many gainers to show")
    ap.add_argument("--minpct", type=float, default=0.0,
                    help="only show rises above this %% (default 0 = any gain)")
    args = ap.parse_args(argv)
    qs = qualities(args)

    p = load_panel(args.db, use_cache=not args.no_cache)
    rows = rank_gainers(p[p.quality.isin(qs)], args.minpct)[:args.top]

    if not rows:
        print(f"No {quality_names(qs)} items with a fenced-bid rise.")
        return
    print(f"Top {len(rows)} {quality_names(qs)} gainers by fenced top-5 bid "
          f"(first record -> most recent):\n")
    hdr = (f"{'item (quality/variant)':44} {'first':>9} {'now':>9} "
           f"{'change':>9}   window")
    print(hdr)
    print("-" * len(hdr))
    for pct, a, b in rows:
        print(f"{fit(a.label, 44):44} {a.bid5:9.3f} {b.bid5:9.3f} {pct:+8.1f}%   "
              f"p{a.pass_id} {a.t:%Y-%m-%d %H:%M} -> p{b.pass_id} {b.t:%Y-%m-%d %H:%M}")

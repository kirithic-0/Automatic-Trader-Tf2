"""Tight-gap, bid-led climb: low bid-ask gap, bid rising, ask following it up.

Per series, over passes where both a fenced top-5 bid and ask exist:
  1. low gap   -- median relative spread (ask5-bid5)/bid5 <= --maxspread;
  2. bid climbs -- first->now rise >= --minrise and OLS slope > 0;
  3. ask follows -- same for the ask, and the bid's pass-over-pass changes lead
     the ask's (best positive lag-correlation, lag 0..--maxlag).
Ranked by  co_rise * (0.4 + 0.6*tight) * (0.5 + 0.5*lead),  all parts printed.
Slopes are over wall-clock hours (the pre-engine script used pass index).

    python -m engine climbers --maxspread 0.08 --trace 5
"""
from types import SimpleNamespace

from engine.book import median
from engine.panel import SERIES, load_panel
from engine.screens._common import fit, parser, qualities, quality_names


def _slope(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def _pearson(a, b):
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va ** 0.5 * vb ** 0.5)


def best_lead(bids, asks, max_lag):
    """(lag, corr) of the best positive correlation of an ask change L passes
    after a bid change, L in 0..max_lag; (None, None) if nothing computes."""
    db = [bids[i + 1] - bids[i] for i in range(len(bids) - 1)]
    da = [asks[i + 1] - asks[i] for i in range(len(asks) - 1)]
    best = (None, None)
    for lag in range(max_lag + 1):
        if len(db) - lag < 2:
            break
        r = _pearson(db[:len(db) - lag], da[lag:])
        if r is not None and r > 0 and (best[1] is None or r > best[1]):
            best = (lag, r)
    return best


def analyze(s, args):
    """The trend record for one series' rows (both legs present), or None."""
    if len(s) < args.min_points:
        return None
    ts = ((s.t - s.t.iloc[0]).dt.total_seconds() / 3600.0).tolist()
    bids, asks = s.bid5.tolist(), s.ask5.tolist()
    if bids[0] <= 0 or asks[0] <= 0:
        return None
    bid_rise = (bids[-1] - bids[0]) / bids[0]
    ask_rise = (asks[-1] - asks[0]) / asks[0]
    if bid_rise < args.minrise or ask_rise < args.minrise:
        return None
    sb, sa = _slope(ts, bids), _slope(ts, asks)
    if sb is None or sa is None or sb <= 0 or sa <= 0:
        return None
    med_spread = median([(a - b) / b for b, a in zip(bids, asks) if b > 0])
    if med_spread is None or med_spread > args.maxspread:
        return None
    lead_l, lead_r = best_lead(bids, asks, args.maxlag)
    if lead_r is None:
        lead = 0.5
    else:
        lead = min(1.0, lead_r)
        if lead_l and lead_l >= 1:
            lead = min(1.0, lead * 1.15)
    tight = 1 - min(max(med_spread, 0.0) / args.maxspread, 1.0)
    co_rise = min(bid_rise, ask_rise)
    return {"score": co_rise * (0.4 + 0.6 * tight) * (0.5 + 0.5 * lead) * 100.0,
            "bid_rise": bid_rise, "ask_rise": ask_rise, "med_spread": med_spread,
            "lead_l": lead_l, "lead_r": lead_r,
            "bid_last": bids[-1], "ask_last": asks[-1]}


def rank_climbers(p, maxspread=0.12, minrise=0.03, min_points=5, maxlag=3):
    """[(record, series_rows)] passing the three gates, best score first."""
    opts = SimpleNamespace(maxspread=maxspread, minrise=minrise,
                           min_points=min_points, maxlag=maxlag)
    p = p[p.bid5.notna() & p.ask5.notna()]
    rows = []
    for _, s in p.groupby(SERIES, sort=False):
        rec = analyze(s, opts)
        if rec is not None:
            rows.append((rec, s))
    rows.sort(key=lambda r: r[0]["score"], reverse=True)
    return rows


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--maxspread", type=float, default=0.12,
                    help="max median relative gap (ask-bid)/bid (default 0.12)")
    ap.add_argument("--minrise", type=float, default=0.03,
                    help="both legs must climb at least this fraction (default 0.03)")
    ap.add_argument("--min-points", type=int, default=5, dest="min_points",
                    help="min passes with BOTH a bid and an ask (default 5)")
    ap.add_argument("--maxlag", type=int, default=3,
                    help="max lead in passes for bid-leads-ask (default 3)")
    ap.add_argument("--emit-skus", action="store_true",
                    help="print only the matching item labels")
    ap.add_argument("--trace", type=int, default=0, metavar="K",
                    help="also print pass-by-pass bid/ask/gap for the top K")
    args = ap.parse_args(argv)
    qs = qualities(args)

    p = load_panel(args.db, use_cache=not args.no_cache)
    rows = rank_climbers(p[p.quality.isin(qs)], args.maxspread, args.minrise,
                         args.min_points, args.maxlag)[:args.top]

    if args.emit_skus:
        for _, s in rows:
            print(s.label.iloc[0])
        return
    if not rows:
        print(f"No {quality_names(qs)} items match the tight-gap bid-led climb "
              f"(maxspread={args.maxspread:.0%}, minrise={args.minrise:.0%}).")
        return

    print(f"Top {len(rows)} {quality_names(qs)} items -- tight gap + bid-led climb:")
    print(f"gap = median (ask-bid)/bid <= {args.maxspread:.0%}, both legs up >= "
          f"{args.minrise:.0%}, fenced top-5 bid/ask.\n")
    hdr = (f"{'item (quality/variant)':40} {'score':>6} {'gap':>6} {'bid+':>7} "
           f"{'ask+':>7} {'lead':>5} {'r':>5} {'bid now':>8} {'ask now':>8}")
    print(hdr)
    print("-" * len(hdr))
    for rec, s in rows:
        if rec["lead_r"] is None:
            lead, rs = "n/a", "  -"
        else:
            lead = "co" if rec["lead_l"] == 0 else f"+{rec['lead_l']}p"
            rs = f"{rec['lead_r']:.2f}"
        print(f"{fit(s.label.iloc[0], 40):40} {rec['score']:6.1f} {rec['med_spread']:5.1%} "
              f"{rec['bid_rise']:+6.1%} {rec['ask_rise']:+6.1%} {lead:>5} {rs:>5} "
              f"{rec['bid_last']:8.3f} {rec['ask_last']:8.3f}")
    print("\nColumns: gap = median relative bid/ask spread; bid+/ask+ = first->now "
          "rise; lead = passes the bid leads the ask (co = same pass), r = its correlation.")

    for rec, s in rows[:args.trace]:
        print(f"\n{s.label.iloc[0]}  score {rec['score']:.1f}  gap {rec['med_spread']:.1%}")
        for r in s.itertuples():
            gap = (r.ask5 - r.bid5) / r.bid5 if r.bid5 else 0.0
            print(f"    {r.t:%Y-%m-%d %H:%M}  bid {r.bid5:8.3f}  ask {r.ask5:8.3f}  "
                  f"gap {gap:+6.1%}")

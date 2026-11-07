"""Candidates for the untrusted-account list: accounts whose high buy orders get past the fence.

Lists every account already on engine.book.UNTRUSTED, then the measured candidates with a
suggested level (1 = never a fill, 2 = out of the book) -- see engine/untrusted.py for the
rule. A bid the fence already rejects needs no entry, so only bids that got past it count.
Nothing is added by this screen: an account goes on the list when the user confirms it
(add `"<steamId>": <level>,` to UNTRUSTED in engine/book.py; the caches re-read by themselves).

    python -m engine untrusted
    python -m engine untrusted --passes 96 --min-pass 0.7 --min-ratio 1.04
"""
from engine import untrusted as ut
from engine.bot_groups import load_bot_obs
from engine.bots import account_stats
from engine.screens._common import fit, parser


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--passes", type=int, default=ut.PASSES, help="window: the last N passes (default 48)")
    ap.add_argument("--min-pass", type=float, default=ut.MIN_PASS, dest="min_pass",
                    help="candidate: gets past the fence at least this often (default 0.5)")
    ap.add_argument("--min-ratio", type=float, default=ut.MIN_RATIO, dest="min_ratio",
                    help="... with a median overbid on those bids of at least this (default 1.03)")
    ap.add_argument("--min-items", type=int, default=ut.MIN_ITEMS, dest="min_items",
                    help="... across at least this many items (default 20)")
    ap.add_argument("--level2-ratio", type=float, default=ut.LEVEL2_RATIO, dest="level2_ratio",
                    help="suggest level 2 at or above this overbid, else 1 (default 1.10)")
    args = ap.parse_args(argv)

    obs = load_bot_obs(args.db, use_cache=not args.no_cache)
    c = ut.candidates(obs, args.passes, min_pass=args.min_pass, min_ratio=args.min_ratio,
                      min_items=args.min_items, level2_ratio=args.level2_ratio)
    names = account_stats(args.db, use_cache=not args.no_cache).set_index("steamId").listing_name
    c["name"] = c.steamId.map(names).fillna("")
    print(f"Buy orders over the last {args.passes} passes, in books where a median forms. 'past' = the "
          f"share the fence kept; overbid = bid / the book's median bid, on the kept bids only.")
    print(f"Candidate: past >= {args.min_pass:.0%}, overbid >= {args.min_ratio:g}, across >= "
          f"{args.min_items} items. Suggested level: 2 (out of the book) at >= {args.level2_ratio:g}, "
          f"else 1 (never a fill).")
    print()
    hdr = (f"{'steamId':18} {'name':28} {'now':>3} {'sugg':>4} {'past':>5} {'overbid':>7} {'>=min':>6} "
           f"{'items':>5} {'books':>6} {'top':>5} {'passes':>6}")
    for title, part in (("On the list", c[c.level_now > 0]), ("Candidates (not listed)", c[c.level_now == 0])):
        print(f"{title}: {len(part)}")
        if len(part):
            print(hdr)
            print("-" * len(hdr))
        for r in part.itertuples():
            print(f"{r.steamId!s:18} {fit(r.name, 28):28} {r.level_now:3d} {r.suggested:4d} "
                  f"{r.pass_rate:5.0%} {r.overbid:7.3f} {r.share_over:6.0%} {r.n_items:5d} {r.n_books:6d} "
                  f"{r.top_share:5.0%} {r.passes_seen:6d}")
        print()
    print("'>=min' = share of its kept bids at >= the candidate ratio; 'top' = share of its kept bids "
          "that are the best kept bid (bid1). To confirm one: add \"<steamId>\": <level>, to UNTRUSTED "
          "in engine/book.py.")


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

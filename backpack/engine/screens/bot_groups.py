"""Behaviour groups: operator fleets, and provider x pricing mode for everything else.

Prints the groups with their size, the pricing-mode split per provider, and the
fleet check -- how often two bots of the same provider, co-listed on the same book
side in the same pass, quote the same price, against the cross-provider baseline.
An operator fleet far below ~90% is not pricing as one (add it to NOT_ONE_OPERATOR
in engine/bot_groups.py). See engine/bot_groups.py for the rules.

    python -m engine bot-groups --window-h 168 --members gladiator:static
"""
import pandas as pd

from engine.bot_groups import behaviour_groups, fleet_check, load_bot_obs
from engine.screens._common import fit, parser


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--window-h", type=float, default=168,
                    help="pricing-mode window in hours before the latest pass (0 = all history)")
    ap.add_argument("--members", metavar="GROUP", help="also list the bots in this group")
    ap.add_argument("--check-passes", type=int, default=48, help="passes for the fleet check")
    args = ap.parse_args(argv)

    obs = load_bot_obs(args.db, use_cache=not args.no_cache)
    g = behaviour_groups(args.db, obs=obs, window_h=args.window_h or None)
    if not len(g):
        print("registry empty -- run `python bot_registry.py sync` first")
        return 1

    print(f"{len(g)} registered bots -> {g.group.nunique()} behaviour groups "
          f"(pricing mode over {'all history' if not args.window_h else f'{args.window_h:g} h'})\n")
    sizes = g.group.value_counts()
    print("  " + "\n  ".join(f"{n:4}  {grp}" for grp, n in sizes.items()))

    print("\npricing mode by provider (operator fleets pooled):")
    ct = pd.crosstab(g.provider.where(g.fleet.isna(), "(operator fleets)"), g.pricing_mode)
    print("  " + ct.to_string().replace("\n", "\n  "))

    fc = fleet_check(obs, g, last_passes=args.check_passes)
    print(f"\nfleet check, last {args.check_passes} passes -- same price when co-listed "
          f"(baseline, different providers: {fc.attrs['baseline']:.1%} over "
          f"{fc.attrs['baseline_pairs']:,} pairs)")
    for r in fc.itertuples():
        kind = "fleet" if isinstance(r.fleet, str) else "split"
        flag = "  <-- fleet not pricing as one" if kind == "fleet" and r.same_price < 0.9 else ""
        print(f"  {r.provider:17} {kind:5} bots {r.bots:4}  pairs {r.pairs:9,}  "
              f"same {r.same_price:6.1%}{flag}")

    if args.members:
        m = g[g.group == args.members].sort_values("n_book_moves", ascending=False)
        print(f"\n{args.members}: {len(m)} bot(s)")
        for r in m.itertuples():
            print(f"  {r.steamId}  {fit(r.name or '', 30):30} reprice {r.reprice_rate:6.2%}  "
                  f"book moves {r.n_book_moves:5.0f}")
    return 0

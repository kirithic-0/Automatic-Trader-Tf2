"""The bot registry: each bot-badged account, its provider and the evidence for it.

Busiest accounts first. `provider_source` says why: manual, roster:<group>,
group+badge:TF2Autobot, conflict:<a>+<b>, or none. Mark one by hand with
`python bot_registry.py mark <steamId> <provider>` (collection side).

    python -m engine bots --unknown --limit 40
"""
from engine.bots import account_stats, registry
from engine.screens._common import fit, parser


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--unknown", action="store_true", help="only provider == unknown")
    ap.add_argument("--provider", help="only this provider")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args(argv)

    reg = registry(args.db)
    if not len(reg):
        print("registry empty -- run `python bot_registry.py sync` first")
        return 1
    counts = reg.provider.value_counts()
    print("providers: " + ", ".join(f"{k} {v}" for k, v in counts.items())
          + f"  (total {len(reg)})")
    if args.unknown:
        reg = reg[reg.provider == "unknown"]
    if args.provider:
        reg = reg[reg.provider == args.provider]
    reg = (reg.merge(account_stats(args.db, use_cache=not args.no_cache), on="steamId", how="left")
              .sort_values("n_listings", ascending=False))

    print(f"\n{len(reg)} account(s), busiest first\n")
    print(f"{'steamId':17}  {'provider':10} {'source':24} {'name':22} {'lists':>6} "
          f"{'badge':>5} {'items':>5}  groups | summary")
    for r in reg.head(args.limit).itertuples():
        name = r.name if isinstance(r.name, str) else (r.listing_name or "")
        print(f"{r.steamId:17}  {r.provider:10} {fit(r.provider_source, 24):24} "
              f"{fit(name, 22):22} {r.n_listings:6.0f} {r.badge_share:5.2f} {r.n_items:5.0f}  "
              f"{fit(','.join(r.groups), 50)} | {fit(r.summary or '', 50)}")
    return 0

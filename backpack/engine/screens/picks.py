"""The agent's buy list for the latest pass (Stage 6, step 3.0: the picker).

The live agent's buy rule (engine.forecast.AGENT_RULE, user 2026-10-30; was 0.7 / 1 key):
of the latest whole pass's universe, the --k items with the highest p_up at or above
--min-p-up (0.65), the ask at least --min-price (0.1) and under --max-price (2) keys,
skipping every (item, quality, variant) already held. `--rule model` is the model's own
rule instead (exp_ret above --min-exp-ret, best exp_ret first: the backtest's).

    python -m engine picks                          # the table
    python -m engine picks --ledger                 # skip what the agent holds (agent.db)
    python -m engine picks --held held.json         # ... or a hand-written list
    python -m engine picks --json picks.json        # the agent's input (Node reads it)
    python -m engine picks --retrain                # rebuild the day's model now
    python -m engine picks --rule model             # the model's exp_ret rule instead

The model is the day's saved one (engine_cache*/live_model.pkl), fit from scratch once it is
24 h old (forecast.MODEL_MAX_AGE_H), not on every call.

--ledger reads what is held from the agent's position ledger (backpack/agent.db, written
by agent/lib/ledger.js: positions opening / open / closing); --held is a JSON list of
{"item", "quality", "variant"}. Both may be given. A pass older than --max-age minutes (hourly passes; the collector or the PC
sync is behind) is reported as stale and picks nothing.
"""
import json
import os
import sqlite3
import sys

import pandas as pd

from engine import forecast as fc
from engine.panel import cache_dir_for
from engine.screens._common import fit, parser, qualities

PICK_COLUMNS = ["item", "quality", "variant", "label", "ask1", "bid1", "bid_med", "spread",
                "p_up", "exp_ret", "sell_at", "drivers"]


def load_held(path):
    if not path:
        return set()
    with open(path, encoding="utf-8") as f:
        return {(h["item"], int(h["quality"]), int(h.get("variant", 0))) for h in json.load(f)}


LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "agent.db")
HELD_STATES = ("opening", "open", "closing")    # = agent/lib/ledger.js HELD


def ledger_held(path=LEDGER):
    """The (item, quality, variant) series the agent holds, from its ledger; none if the
    ledger does not exist yet."""
    if not os.path.exists(path):
        return set()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        q = "SELECT item, quality, variant FROM positions WHERE state IN (%s)" % ",".join("?" * len(HELD_STATES))
        return {(i, int(qu), int(v)) for i, qu, v in con.execute(q, HELD_STATES)}
    finally:
        con.close()


def build(scored, held=(), k=fc.MAX_NEW_BUYS, rule=None, max_age_min=90.0, now=None):
    """{pass, age, stale, the rule, counts, picks} from predict_latest's output: the
    agent's input, JSON-ready. `rule` = fc.pick's bar arguments (default AGENT_RULE)."""
    rule = dict(fc.AGENT_RULE if rule is None else rule)
    now = now or pd.Timestamp.now(tz="UTC")
    out = {"generated_at": now.isoformat(),
           "rule": {"k": k, **rule, "max_spread": fc.MAX_SPREAD, "take_profit": fc.TAKE_PROFIT,
                    "horizon_h": fc.HORIZON},
           "n_held": len(held), "picks": []}
    if scored.empty or "exp_ret" not in scored:
        out.update(pass_id=None, stale=True, reason="no universe or no labelled history")
        return out
    t = scored.t.iloc[0]
    age = (now - t).total_seconds() / 60
    out.update(pass_id=int(scored.pass_id.iloc[0]), pass_t=t.isoformat(), pass_age_min=round(age, 1),
               n_universe=len(scored), n_over_bar=len(fc.pick(scored, (), len(scored), **rule)),
               base_rate=scored.attrs.get("base_rate"), n_train=scored.attrs.get("n_train"),
               model_trained_at=scored.attrs.get("trained_at"))
    if age > max_age_min:
        out.update(stale=True, reason=f"latest whole pass is {age:.0f} min old (> {max_age_min:g})")
        return out
    picked = fc.pick(scored, held, k, **rule)
    rows = picked[[c for c in PICK_COLUMNS if c in picked]].to_dict("records")
    out.update(stale=False, picks=[{c: _plain(v) for c, v in r.items()} for r in rows])
    return out


def _plain(v):
    """numpy scalars to Python; NaN to None (JSON has no NaN: Node's JSON.parse rejects it)."""
    v = v.item() if hasattr(v, "item") else v
    return None if isinstance(v, float) and v != v else v


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--held", help="JSON list of held {item, quality, variant}")
    ap.add_argument("--ledger", nargs="?", const=LEDGER, help=f"the agent's ledger (default {LEDGER})")
    ap.add_argument("--json", help="write the agent's input here")
    ap.add_argument("--k", type=int, default=fc.MAX_NEW_BUYS)
    ap.add_argument("--rule", choices=("agent", "model"), default="agent",
                    help="agent: p_up bar, under --max-price (default); model: the exp_ret bar")
    ap.add_argument("--min-p-up", type=float, default=fc.AGENT_RULE["min_p_up"])
    ap.add_argument("--max-price", type=float, default=fc.AGENT_RULE["max_price"])
    ap.add_argument("--min-exp-ret", type=float, default=fc.MIN_EXP_RET, help="--rule model only")
    ap.add_argument("--min-price", type=float, default=fc.MIN_PRICE)
    ap.add_argument("--max-age", type=float, default=90.0, help="minutes (default 90)")
    ap.add_argument("--retrain", action="store_true", help="rebuild the day's model now")
    args = ap.parse_args(argv)

    frame = fc.forecast_frame(args.db, use_cache=not args.no_cache)
    frame = frame[frame.quality.isin(qualities(args))]
    scored = fc.predict_latest(frame, min_exp_ret=args.min_exp_ret, min_price=args.min_price,
                               model_dir=cache_dir_for(args.db), retrain=args.retrain)
    held = load_held(args.held) | (ledger_held(args.ledger) if args.ledger else set())
    rule = (dict(fc.AGENT_RULE, min_p_up=args.min_p_up, max_price=args.max_price, min_price=args.min_price)
            if args.rule == "agent" else dict(min_exp_ret=args.min_exp_ret, min_price=args.min_price))
    out = build(scored, held, args.k, rule, args.max_age)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)

    if out.get("pass_id") is None:
        print(f"No picks: {out['reason']}.")
        return 1
    print(f"Pass p{out['pass_id']} ({out['pass_t'][:16]} UTC, {out['pass_age_min']:.0f} min old): "
          f"{out['n_universe']} items in the universe, {out['n_over_bar']} over the bar ("
          + (f"p_up >= {args.min_p_up:g}, {args.min_price:g} <= ask < {args.max_price:g} keys"
             if args.rule == "agent" else f"exp_ret > {args.min_exp_ret:+.1%}, ask >= {args.min_price:g} keys")
          + "), "
          f"{out['n_held']} held. Model built {(out.get('model_trained_at') or '?')[:16]} UTC.")
    if out["stale"]:
        print(f"STALE: {out['reason']}. Nothing picked (pull the VM's data: sync/pull.py).")
        return 1
    print(f"Buy {len(out['picks'])} (at most {args.k} a pass, never a series already held):\n")
    hdr = f"{'item (quality)':40} {'exp_ret':>7} {'p_up':>5} {'ask1':>8} {'sell at':>8}"
    print(hdr)
    print("-" * len(hdr))
    for p in out["picks"]:
        print(f"{fit(p['label'], 40):40} {p['exp_ret']:+7.2%} {p['p_up']:5.2f} {p['ask1']:8.4f} "
              f"{p['sell_at']:8.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

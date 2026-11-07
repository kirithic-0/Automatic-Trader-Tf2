# Does Steam Community Market history help the trade model? (2026-10-27)

Research mode. Idea (user, 2026-10-27): backfill each item with its SCM price graph and see
whether it adds value to the model. Source choice is in `research/steam_market_sources`:
Steam's own listing page has the full history but is rate-limited (~100 pages / 5 min);
history.tf serves ~90 days daily with no limit seen. The user judged 90 days enough, since it
predates the bp.tf collection (`trading_app.db` from 2026-10-02).

## Data (`fetch.py`, `key_steam_inr.json`)

- history.tf `/api/analysis/scm/item`, one call per item at ~2-3 req/s, no errors:
  **1,449 of our 2,881 series** (pass 382; Unique = bare name, Strange = `Strange ` +
  name, else with `The `), plus the Key. 106k item-days, 2026-07-15 -> 10-27, daily
  `avg_price` (USD) and `num_sold`. Output `scm_htf.jsonl` (6.8 MB).
- **history.tf's Key series stopped on 2026-10-21** (`last_updated` 10-21 04:01). Later days
  come from Steam's own Key listing page (`key_steam_inr.json`, INR, logged out), divided
  by the INR/USD ratio over the 90 shared days (median 94.95, sd 0.21). The stitched Key on
  10-27 is $2.173; Steam's `priceoverview` said $2.17.

## Features (`features.py`)

Seven columns. Prices are converted to keys with the Key's SCM price that day. A row at time
t sees only days before t's UTC date:
`scm_prem_1d`, `scm_prem_7d` (SCM price / bp.tf ask1 - 1), `scm_mom_7d`, `scm_mom_30d`,
`scm_vol_7d`, `scm_vol_chg`, `scm_age_d`. Known on 49% of frame rows and **63% of the test
universe**.

## Test (`test.py`): the seller_cost yardstick, 4 paired seed sets

The engine's walk-forward on trading_app.db, 24 h test days 10-16 21:21 -> 10-25 10:06 (9
days, 233,358 universe rows, base rate 10.8%). xgboost base (138 features) vs base + 7 SCM,
seed sets A-D, both fitted on the same training rows each day. Scored by the replay (fills
assumed) at the agent's `p_up >= 0.7` and at matched volume (~69 bought).

| scm - base | by set A / B / C / D | mean | t | positive |
|---|---|---|---|---|
| AUC | +0.002 / +0.003 / +0.002 / +0.002 | +0.002 | 27 | 4/4 |
| at 0.7: bought | 69->57, 72->60, 76->63, 80->57 | -15 trades | | 0/4 more |
| at 0.7: win rate | +4.6 / +8.6 / +5.2 / +3.2 pt | **+5.4 pt** | 4.7 | 4/4 |
| at 0.7: a trade | +0.52 / +0.51 / +0.66 / +0.12 pt | **+0.45 pt** | 3.9 | 4/4 |
| at 0.7: keys | -0.07 / -0.03 / +0.05 / -0.29 | -0.08 | -1.1 | 1/4 |
| matched: win rate | +1.4 / +8.5 / +5.7 / +1.4 pt | +4.3 pt | | 4/4 |
| matched: a trade | -0.23 / +0.33 / +0.30 / -0.09 pt | **+0.08 pt** | 0.6 | 2/4 |
| matched: keys | -0.07 / +0.06 / +0.07 / +0.01 | +0.02 | 0.5 | 3/4 |

Base alone: +3.75% a trade at 0.7 (sd 0.13 across sets), 86.6% won.

The model leans on SCM: **13.6% of p_up's gain**. `scm_prem_7d` and `scm_prem_1d` are
#7 and #8 of 145 features, and the momentum and volume columns are #11-#13.

## Verdict: a safer model, not a more profitable one (yet)

- At the agent's bar the SCM model **buys ~20% fewer items and wins more often**: +5.4 pt
  win rate and +0.45 pt a trade, 4/4 sets, both well above seed noise. **Total keys do not
  rise** (-0.08), because it trades less.
- **At the same number of trades it still loses less** (win +4.3 pt, 4/4), but **return per
  trade is unchanged** (+0.08 pt, t 0.6). The SCM columns steer away from losers and also
  away from the bigger winners. What changes is risk, not profit.
- AUC +0.002 in every set: small but real, like path C's book-shape result.

For the live agent the win-rate gain is worth more than the replay counts. The replay
assumes fills, and real losers are stuck stock (the 10-27 run: 6 held, 0 sold). Still,
the agent's goal is profit, and on 9 days and ~69 trades profit does not move.

What would settle it: (1) recheck in ~2 weeks on new days, with the same `test.py` and a
fresh `fetch.py` (history.tf keeps 90 days rolling); (2) score with real sell-through,
not assumed fills, where the fewer-losers effect should show; (3) the other half of the
items: 1,432 series have no SCM data on history.tf, and Steam's own page could fill the
names that exist there.

    python fetch.py                    # ~10 min -> scm_htf.jsonl
    python test.py fit && python test.py score    # ~19 + ~12 min (GPU)

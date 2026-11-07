# ADR 0004 — The Stage-5 buy-level forecast: two models, retrained per run

- **Status:** Accepted (by the agent, 2026-09-25 — the user asked for a one-shot build
  without questions; every threshold here is a parameter and theirs to change)
- **Date:** 2026-09-25
- **Scope:** new `backpack/engine/forecast/`, `engine/bot_features.py`,
  `engine/tsfm.py`, `backpack/tsfm_forecast.py`; `BookRead` read-outs (the two TimesFM
  files were removed 2026-09-26 — see Amendments)

## Context

The user's question: for items whose sell order sits only a little above the buy
orders, will the buy-order price (median / average) go up — so that buying now and
selling when it rises pays? They expected bot behaviour (overcut spikes, groups
repricing up) to drive the rises, and suggested TimesFM 3.0 (released 2026-08).

Constraints: ~7 days of full-roster history (hourly passes, 2,870 series); ADR 0003 —
`trading.db` stays facts-only, derived numbers live in the disposable cache; the
scraper's Python (3.10) must not be disturbed.

## Decision

1. **Two targets, two models.** `p_up` (xgboost classifier) answers the question as
   asked: does the *held median of the fenced bids* reach the entry ask + 2% within
   48 h. `exp_ret` (xgboost pseudo-Huber regressor) prices the trade — buy at the ask,
   take profit at +5% once the held best bid is there, else sell to the best bid at
   48 h — and decides BUY (`exp_ret > 0.5%`). Measured: ranking by `p_up` alone and
   taking profit at +2% on the median left the portfolio at break-even, because a miss
   costs the spread and the drift; the return model with a +5% take-profit on the best
   bid made every test day positive (data-model.md, "Buy-level forecast").
2. **Retrain on every run, store no model.** Training takes ~1 min on the full
   history; a stored model would go stale as passes arrive and would be one more
   derived artefact to invalidate. The backtest is the same code path on earlier cuts.
3. **Point-in-time everything, purged walk-forward.** Features use only passes at or
   before t; the bot shrinkage group is as of the last midnight before t
   (`bot_groups.groups_asof`); a train row enters a fold only when its 48 h label
   closed before the test day.
4. **Bot behaviour as decayed, group-shrunk rates, aggregated per book**
   (`engine/bot_features.py`) — the handoff's "dynamic part", computed on read and
   cached like the panel, never stored in `trading.db`.
5. **TimesFM out of process, optional, off by default.** *(Reversed 2026-09-26 by the
   user: TimesFM is removed from the code — see Amendments. Kept here as the record of
   what was measured.)* TimesFM-3 needs torch +
   `timesfm` 3.x (and wants the GPU); it runs in its own `.venv-tsfm` through
   `backpack/tsfm_forecast.py`, results cached by snapshot. Measured to add no
   consistent skill (its median forecast is flat on these step-like prices), so the
   default model does not use it. **The TimesFM-3 pretrained weights are licensed for
   non-commercial, non-production use only** (TimesFM 2.5 and earlier weights are
   Apache-2.0); using them to drive real trades is the user's licence call.

## Consequences

- `python -m engine forecast` is the Stage-5 surface; `--backtest` re-measures it as
  history grows (the numbers in data-model.md are dated and rest on 7 days).
- `BookRead` gained `bid_med`, `ask_med`, `ask1_bot`; the panel cache rebuilds when its
  columns change (no `RULES_VERSION` bump — the rules did not change).
- `p_up` is over-confident above ~0.3; it is shown raw with the backtest's reliability
  line rather than recalibrated on one day of data.
- Fills: by user decision (2026-09-26) any order inside its side's fence fills
  instantly at its price, both when buying (fenced asks) and selling (fenced bids) —
  what the backtest already assumed. Trading costs: none assumed (not in the KB).
- Rejected: a stored/pickled model (staleness), recalibrating `p_up` with isotonic
  regression on the last training day (Brier got worse), TimesFM in the engine's own
  Python (dependency clash with the scraper's environment).

## Amendments

- **2026-09-26 (agent, one-shot; measurements in data-model.md → "Revision
  2026-09-26"):** the trade now sells into the best bid the **first pass** it is ≥ ask
  +5%, **at that bid's price** (was: exactly +5% once the best bid held two passes), and
  a level or bid above `MAX_JUMP` = 3 × the entry ask is a thin-book misread that never
  counts. BUY bar +0.5% → **+1%**. Backtest portfolio +0.63% → **+1.70% per trade**
  (429 trades, 5/5 days). The regression objective (pseudo-Huber 0.02) was re-tested
  against squared/absolute/quantile losses, classifiers and XGBRanker and kept.
  xgboost trains on the GPU when one answers (`forecast.device()`), which also keeps
  the training off the CPU a running scraper needs. TimesFM is dropped from further
  work by the user (the optional `--tsfm` path stays, unmaintained).
- **2026-09-26, user review:** the agent's knobs accepted (take-profit +4%, BUY bar +1%,
  48 h, spread ≤ 5%); a 0.1-key minimum price added; two accounts whose trades do not go
  through (`engine.book.UNSTABLE_FILLS`) are never an exit (`bid1_fill`). The branch was
  merged into `main`.
- **2026-09-26, user:** decision 5 reversed — the TimesFM path (`engine/tsfm.py`,
  `backpack/tsfm_forecast.py`, `--tsfm`, `.venv-tsfm`) is removed from the code.
- **2026-09-26, later the same day (agent):** the first forward-test pass exposed that
  the misread guard sat only in the labels — a misread still reached the features — and
  that a trade with no bid at the horizon was dropped from every mean. Now
  `drop_misreads` blanks those bid reads before any feature or label, and a no-bid exit
  waits up to 24 h for a buyer (else it is marked at the last bid); the purge follows each
  row's close time. Honest backtest: **+1.52% per trade** (492 trades, 5/5 days).
  Then the take-profit moved from +5% to **+4%** (more per trade in both windows tried,
  keys a wash): **+1.70% per trade** (511 trades, 5/5 days).
- **2026-09-26, user — the untrusted list replaces a flat set of unstable fills.**
  `engine.book.UNTRUSTED` maps steamId → level: **1** = stays in the book and the fence
  but is never a fill (`bid1_fill` skips it); **2** = also dropped from the book before
  the median. `UNSTABLE_FILLS` (named in the user-review amendment above) is now derived
  from it (`frozenset(UNTRUSTED)`), not a separate list. User rule: the list holds ONLY
  accounts that get past the median fence — an account the fence already rejects needs
  no entry. `python -m engine untrusted` suggests candidates from fence-kept bids only;
  an account goes on the list only when the user confirms it. Today it holds the two
  level-1 accounts from the review. Post-review backtest (09-20 07:23 → 09-24 07:23):
  **167 trades, +0.47% per trade, +1.17 keys** — the two unstable accounts had carried
  cheap-item % wins, not keys. Rules and candidates: data-model.md, "Untrusted accounts";
  numbers: data-model.md, "Buy-level forecast".

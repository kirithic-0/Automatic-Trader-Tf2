# ADR 0003 — An analysis engine owns the read rules; trading.db stays facts-only

- **Status:** Accepted
- **Date:** 2026-09-24
- **Scope:** new `backpack/engine/`; retires `backpack/analysis/`

## Context

The read side of `trading.db` had grown as four report scripts in
`backpack/analysis/`. The read rules (flag filter, steamId dedupe, the
median-ratio buy fence, the trust-fenced ask) lived *inside two of the reports*
(`bid_movers.py`, `bid_gainers.py`); the other two imported from them, the tuned
fence parameters were copied into three CLIs, and every script re-implemented the
same load loop with three different pass clocks. The rule had already drifted:
`bid_movers` reported an unfenced top-5 bid, so a single 50-key bid on a
0.02-key hat read as a mover. This is the locality failure ADR 0001 fixed on the
scrape side, recurring on the read side.

Stage 4 (which bids will step up) needs more than reports: an event definition,
forward labels, per-snapshot features, and a way to tell which features matter.

## Decision

A new package, **`backpack/engine/`**, is the read side of `trading.db`:

- **The engine owns the read rules** — `engine/book.py` (`read_book`) is their
  only implementation. The old scripts were ported onto the engine as screens and
  deleted, not imported from.
- **`trading.db` is the seam and stays facts-only.** The engine opens it
  read-only and never writes derived numbers back (the 2026-09-14 removal of the
  stored derived layer stands). Speed comes from a **disposable cache**,
  `backpack/engine_cache/panel_v<RULES_VERSION>.parquet`, appended by snapshot id
  and rebuilt on a rules-version bump or a reset db.
- **Not a scrape layer.** Layers A/B/C each know only the layer below; the engine
  imports none of them.
- **Scope: Unique (6) + Strange (11) only**, where the fences were tuned.
- **numpy + pandas (+ pyarrow for the cache)** are dependencies of the engine.
- Pipeline: `load_panel` → `label_events` (the step event and `ev_<H>h` labels,
  defined in `data-model.md`) → `build_features` → `signals` (lift, score,
  backtest) → screens (`python -m engine <screen>`).
- **Feature selection is by lift, not a trained model.** A feature is used when
  its top or bottom 10% has a step rate ≥ 1.5× base; the ratchet-watch score is
  the average within-pass rank of those features. A time-split backtest reports
  precision@k — the benchmark Stage 5 must beat.

## Consequences

- One place to retune a fence; `RULES_VERSION` invalidates the cache.
- `movers` output changed (fenced bid). `gainers`/`climbers` match the old
  scripts; `timeseries` no longer includes Unusuals.
- The event definition is a parameterised rule (`pct`, `hold`, `max_gap_h`, H),
  so revisiting it (e.g. requiring a new high — 63% of steps are recoveries,
  measured 2026-09-24) is a change in `events.py`, not a rewrite.

A future architecture review should not re-propose storing derived prices in
`trading.db`, or putting read rules back inside report scripts.

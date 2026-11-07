# Codebase modules — the analysis engine

> The read side: the analysis engine over `trading_app.db` (or the frozen `trading.db`), its
> interface, and its tests.
> The collector that writes `trading_app.db` (`collector/`: the `ScrapeQueue`, the pass loop,
> `book_codec`'s writer side) and the gRPC sync (`sync/`) are in the same repository; their
> module docstrings are their documentation. ADR `0003` is the engine's decision record.

`backpack/book_codec.py` is a copy of the collector's (the reader half is all this
repo uses: `Codec.from_con(con).decode(value)` for every `trading_app.db` book). Keep
the two in step. The engine wraps it as `engine.panel.book_decoder(con)`.

## The analysis engine — the read side of the db

**Package:** `backpack/engine/`  ·  **ADR:** `adr/0003-analysis-engine.md`

Not a fourth layer: Layers A/B/C each know only the one below, and the engine
knows none of them. **The DB is the seam** — collection writes it, the engine
reads it (read-only), and neither imports the other. **Which db (2026-10-17):** the
engine's default is `trading_app.db` (`engine.panel.DEFAULT_DB` = `APP_DB`; the replica the
collector syncs here), `--db trading` (or a path) the frozen classifieds history to
2026-10-01. Never mixed. `resolve_db` takes the aliases `app` / `trading`;
`cache_dir_for(db)` gives each db its own cache folder (`engine_cache/` for trading.db,
`engine_cache_app/` for the app db) — every `cache_dir=None` default resolves through it.
Every reader of `book_json` (panel, bot_groups, bots, the dashboard) decodes through
`book_decoder(con)` — zstd BLOBs and plain JSON alike; `book._parse` itself still refuses a
BLOB. Scope: Unique (6) + Strange (11) only, where the fences are tuned.

**The universe gate (2026-10-31).** The collector watches more goods than the engine trades
(data-model.md, "Which good"). Every reader goes through one gate in `engine/panel.py`:
- `universe_sql(con)`: Unique/Strange + `admitted_sql`. Used by panel, bot_groups and the
  dashboard audit.
- `admitted_sql(con)`: the plain good + `admitted = 1` in the roster, with no quality scope.
  Used by bots, whose listing counts include trading.db's Unusuals.
- `plain_sql(con, table)`: the five kind columns at their defaults, NULL-safe. Every read
  of one series by (item, quality) appends it: `latest_book` (the agent's exits), replay,
  `book_at`, `history_at`, `history`.

On a db without the kind columns or a roster (trading.db, or the replica before the
roster syncs) each part is `''`, so it reads as it always did. Counting 1.4M rows through
the gate costs ~7 s (it reads every row's kind columns), against ~0.1 s for the
index-only quality count. So the panel cache keeps a stamp beside it
(`panel_v<KEY>.parquet.stamp.json`: that count, the gate, a hash of the admitted roster).
The full gated count runs only when the stamp moves, i.e. on a reset db or a changed
admitted set. `stats._raw` filters by the panel's own ids in pandas, not through the gate.
`history(item, q, kind=...)` takes a kind code `craftable.elevated.australium.crate.target`
(`history.PLAIN_CODE` = `1.0.0.0.0`). The Books tab names every roster good that way, and
any good not admitted is marked "collected only".

**Every good, kinds apart: `scope="all"` (2026-11-01, for the per-group backtest).** The
series key stays `(item, quality, variant)`. `engine.kinds` packs a non-plain good's five
kind columns into a **negative** `variant` (`variant_of`, `kind_of`): Non-Craftable -1,
Strange-elevated -2, Australium -4, crate series `n` -8n, Strangifier target `d` -8192d,
summed. The plain good packs to 0, and Unusual effect ids are positive, so no series moves.
The code exists only in memory. A db read of one series pins the kind with
`kind_sql(con, variant)` (= `plain_sql` at 0). `kinds.as_good(book, variant)` clears the
`strange` flag on a Strange-elevated good's listings. All 365 listings of those books are
`strange` (pass 499), so read rule 1 would otherwise drop the good itself.
- `load_panel(scope="all")` adds `_read_extra`: every snapshot outside `universe_sql`, each
  labelled with its kind ("Hat (Unique, Non-Craftable)", "... (Unique, #10)"). It has its
  own cache, `panel_v<KEY>_extra.parquet`. Every load checks it by counting the rows outside
  the gate, a full scan of ~10 s, so admitting a good rebuilds it.
- `load_bot_obs(scope=...)`: the observations carry `variant` (0 on the admitted cache,
  which is untouched). With "all", a second cache `bot_obs_v<KEY>_extra.parquet` is stamped
  with the gate.
- `bot_groups.book_key(obs)` names a book as item, quality and variant. `listing_moves`,
  `book_bot_features`, `transitions`, `fleet_check` and `forecast.add_bot_features` key by it.
  `load_book_bot_features(scope="all")` caches as `bot_book_..._all.parquet`. The accounts'
  rates then count every book they quote.
- `forecast_frame(scope=...)` passes the scope on. `walk_forward(train_mask=)` trains only
  on the masked rows and still scores every row.
- `replay.Books.after` reads a negative variant's books by `kind_sql` and `as_good`.

The admitted goods inside `scope="all"` read exactly as the agent reads them. Three rules
make sure of it:
- **Order.** The admitted series come first, in the admitted panel's order (an `extra`
  column; `listing_moves` orders its books the same way). The engine's windows run on one
  float axis, `code × 1e7 + hours` (`events.series_axis`), and the passes start on an exact
  hourly cadence. So whether a row exactly 24 h back is inside a window depends on the float
  rounding of its series code. Interleaving the new series moved 43 `bid1_max24` values,
  for example: Air Raider's 24 h window held 23 rows at code 26 and 24 at code 46.
- **The market.** `market_up_share` and `mkt_lvl_up_share` are the shares among the
  admitted goods, broadcast to every row of the pass: the context the live model has.
- **Account rates.** The admitted books' bot features use rates from the admitted books
  alone. The other books use rates from every book, as if all were admitted.

Checked on 2026-11-01 (pass 501). Before 10-31 20:00, the admitted rows of the two scopes
are equal on every forecast feature and label (1,366,557 rows; rtol 1e-9). After it, only
some bot rate features differ (prem, cut1, nobs: 10-39k of 41,673 rows). The cause is the
live bot-observation cache, which holds 96 listings of non-admitted goods from pass 489
(handoff W, "Found on the way"). The agent and every screen keep the default
`scope="admitted"`. The bot book features recomputed with the new keys equal the old cache
exactly (1,404,924 rows). `tests/test_groups.py` covers it.

```
trading.db ──> panel ──> events ──> features ──> signals ──> screens
 (facts)      book.py    step +     ~24 per      lift,       movers, gainers, climbers,
              rules,     ev_<H>h    snapshot,    score,      timeseries, events,
              cache      labels     no look-     backtest    signal-check, ratchet-watch
                                    ahead
```

### Interface

```python
from engine import load_panel, label_events, build_features, read_book
from engine.book import explain_book

e = explain_book(book_json)         # every raw buy/sell listing + its verdict (kept / bid1 /
                                    #   top5, or dropped: flag, null price, duplicate account,
                                    #   no median, fence too high/low + the numbers) -- same
                                    #   valid_*/fence_* calls as read_book, so it cannot drift
r = read_book(book_json)            # BookRead: bid1, bid5, ask1, ask5, gap2, n_near_top,
                                    #   bid_disp, n_buy/n_sell, fenced-out counts, bot/premium/
                                    #   instant shares, top_bot, bid_med/ask_med (median of the
                                    #   fenced side), ask1_bot, bid1_fill (best fenced bid
                                    #   not from an UNTRUSTED account) -- NaN = not computable
panel = load_panel(db)              # one row per Unique/Strange snapshot: pass_id, t (UTC pass
                                    #   start), item/quality/variant, label, BookRead fields,
                                    #   pdb_buy/pdb_sell; sorted by series then t
frame = label_events(panel, pct=0.02, horizons=(24,))   # + r_bid1, bid1_max24, step, new_high,
                                    #   ev_24h / nh_24h labels (NaN = censored)
frame = build_features(frame)       # + engine.FEATURES
```

- **`engine/book.py` is the only home of the read rules** (`data-model.md` has
  the rule text). Bump `RULES_VERSION` when a rule or parameter changes. Version 4
  (2026-10-17): `fence_median(vals, depth=FENCE_DEPTH)` — the fence median and
  `bid_med` / `ask_med` read the top `FENCE_DEPTH` = 7 valid listings a side (the depth
  the fence was tuned on); `fence_buys` / `fence_sells` take `depth=None` for the old
  all-listings median (the dashboard's comparison). `explain_book` reports both medians
  (`median`, `median_all`, `depth`) and marks each listing `in_depth`.
- **`UNTRUSTED`** (`engine/book.py`, user 2026-09-26) — `{steamId: level}`, only the
  accounts judged untrustworthy: level 1 = never a fill (skipped by `bid1_fill`; derived
  set `UNSTABLE_FILLS`), level 2 = out of the book (dropped at rule 1; `DROPPED`).
  Editing it changes `READ_KEY` (rules version + a checksum of the list), which keys the
  panel, bot_obs and bot_book caches, so they all re-read.
- **`engine/untrusted.py`** — `overbids(obs)` (per account: pass rate through the fence;
  on the kept bids: median bid / book median, items, top share) and `candidates(obs)`
  (past the fence ≥ 50%, kept overbid ≥ 1.03, ≥ 20 items; suggested level 2 at ≥ 1.10,
  else 1). Screen: `python -m engine untrusted [--passes N] [--min-pass X] [--min-ratio X]
  [--min-items N] [--level2-ratio X]`. It only proposes; the
  user confirms and the account is added to `UNTRUSTED` by hand.
- **Collection gaps** (`engine.events.observed_until`, 2026-10-17): every label's "last
  seen" is capped at the end of its run of passes (runs break where no pass ran for
  > `SEGMENT_GAP_H` = 6 h), so `label_events` and `forecast.label_forecast` censor a window
  that reaches into a collector outage instead of reading it as "never reached".
- **Cache:** per-snapshot book reads go to `<cache_dir_for(db)>/panel_v<READ_KEY>.parquet`
  (gitignored, disposable). New snapshots are appended by id; if the db was
  reset under it (row count mismatch) or `BookRead` gained a field (a cached column
  is missing) it rebuilds. Cold load ~23 s, warm ~2.4 s (2026-09-24, 366k rows);
  warm 8.2 s, `build_frame` 5.6 s more, `forecast_frame` 72 s (2026-10-30, trading_app.db,
  1.27M rows, 445 passes). Every cache parquet is written through `panel.write_cache`
  (a temp file, then `os.replace`; skipped when another process holds the file open), so
  the agent's picks and `engine panel` never read half a file.
  Nothing derived is ever written to `trading.db`.
- **`engine/stats.py`** (2026-10-30) -- the per-pass stats cache behind `engine panel`:
  each pass summarised once (per column: rows, missing, mean, p10..p90, counts in fixed
  decile bins) into `stats_<key>_{cols,passes}.parquet` + `_meta.json` in the cache
  folder, two groups: `scrape` (`SCRAPE_COLS`: the BookRead fields + pricedb pairing + the
  raw row's listing counts and key rate, every Unique/Strange book) and `features`
  (`FORECAST_FEATURES` on `universe(TRAIN_MAX_SPREAD)` rows). `refresh_scrape(db)` reads the
  panel (~8 s) and also rebuilds `passes`, one row per pass: books, minutes, new / gone
  series, listings summed, capped share (`CAP` = 30), and the market's top-buy-order moves
  -- `up` / `down` share (`FLAT` = 0.5%), `idx_log` = mean of each item's `log(1 + r_bid1)`
  clipped at `IDX_CLIP` = 0.25, chained into `idx` (100 at the first pass), steps, new
  highs. `refresh_features(db, panel)` builds the forecast frame (~72 s). A pass is
  re-summarised only when its row count changed. Drift = PSI over the fixed bins (missing
  a bin of its own): `drift_table(st, group, now, ref)` and `daily_psi(st, group)` (each
  day vs the 7 before); `PSI_WATCH` 0.1, `PSI_DRIFT` 0.25. The key hashes `STATS_VERSION`,
  `READ_KEY` and the column lists; the bin edges (deciles at the first build) stay fixed
  for the life of the cache.
- **Screens** are thin views, one module each with `main(argv)`:
  `python -m engine <screen>` from `backpack/` (or `python backpack/engine <screen>`).

| screen | what |
|---|---|
| `panel` | **the live control panel** (2026-10-30): `python -m engine panel [--port 8770] [--open] [--check 60]` (or `agent panel`), 127.0.0.1 only. An **Agent** tab -- running / mode / last and next cycle, the kill switch, the win-rate stop, results vs the backtest, held items against their take-profit and horizon, realised profit over time, offers by how they ended, every bot (accept rate, median seconds to accept), limits, recent sales, changes by hand, the log's tail -- with **buttons** that run the agent's own commands (`ACTIONS`: kill switch on/off, resume buying, run a cycle now / restart / shut down after the cycle, sell a held item next cycle, its take-profit or horizon, set a limit, start the agent): each asks first, needs the page's per-start token and a local Host (`allowed`), and is appended to `backpack/logs/panel_actions.log`; `/api/agent` runs `agent/panel.js` (cached 5 s). A **Books** tab (2026-10-31): what each held item would fetch now (the newest best tradable bid against what was paid, the unrealised total) with a chart per item since its buy -- tradable bid, lowest ask, paid / take-profit lines, the horizon -- a **Held now / Sold** switch (2026-10-31: every sale as a card, newest first, 12 at a time: paid -> sold, exit, hours held, the buyer, its chart from 6 h before the buy to 12 h after the sale, and its sell-now bid today vs the price sold at) -- and, for any item (search by name + quality, or a held / sold chip), its price over time from every stored book (passes + the agent's watch books) where a click opens the **full order book at that point**: each listing kept or dropped by the engine's rules and whether the agent could trade with it; `/api/item` (engine history, ~0.2-0.5 s) and `/api/book` (`history.book`, ~0.06 s) read one item on demand -- the per-item book view without building the dashboard's whole frame. Item names link to it from the Agent tab. Overview tiles, an **Economy** tab (buy-order index, average move per pass, items up / down, median spread, listings, key rate, steps, bot at the top bid), **Scrape drift** (books / minutes / new-gone / capped per pass, a PSI heatmap per column per day, the last 24 h vs the 7 days before, a p10-p90 band per column) and **Feature drift** (the same over the model's 138 inputs, against the live model's training data -- `label_end` in `live_model.pkl` -- or the 7 days before). Opens at once from `engine/stats.py`'s cache; a thread checks the db every `--check` s and summarises new books (scrape ~15 s, then features ~1.5 min once no new book came for 5 min and the newest pass is whole, or 30 min on -- so it never builds its forecast frame while the agent builds the same one for its picks); the page polls `/api/state` and redraws itself. Template `engine/screens/panel.html`; launch config `engine-panel` |
| `dashboard` | **one interactive HTML page over all of it** (`--open` to launch): watchlist, signals + backtest, events, movers, climbers, book audit, item explorer with chart + every feature; an any-step / new-high toggle. `--serve` makes it live: click any chart point for that pass's full buy/sell book, every listing marked kept or dropped and why. `--forecast` adds a **Forecast** tab (the Stage-5 picks + track record; trains the models, ~1–3 min more) |
| `movers` | fenced top-5 bid rose within a % band, tight books only |
| `gainers` | top fenced top-5 bid % rises, first record → now |
| `climbers` | tight gap + bid-led climb (both legs rise, bid leads ask) |
| `timeseries` | one HTML viewer: fenced bid/ask + pricedb per item |
| `history` | one item's every stored book over time (hourly passes + the agent's 10-min watch books; `--old` adds trading.db): top buy order, lowest sell, best tradable bid and who; a loose name resolves (2026-10-30). The agent's `agent history <item>` prints it and charts it. Each point names its book (`src`, `id`); `book(src, id)` explains that one book (`explain_book` + the agent's verdict per listing, `engine.listings`), `items()` lists every (item, quality) -- the panel's Books tab (2026-10-31) |
| `events` | log of detected steps (filter by item, lookback) |
| `signal-check` | per-feature lift for a step within H + time-split precision@k backtest |
| `ratchet-watch` | latest pass ranked by the features that clear the lift bar, with drivers |
| `forecast` | **the Stage-5 model**: latest pass's tight-spread items with `p_up` (buy-order median reaches ask +2% within 48 h) and `exp_ret` (the trade's expected return), BUY flags and SHAP drivers; `--backtest` = purged walk-forward + portfolio (see "The buy-level forecast" below) |
| `exits` | **the Stage-6 exit rule** (3.7): every open position in the agent's ledger -- HOLD / SELL and why (take-profit, horizon, overdue) from its newest stored book, or `--target` / `--ssh`: a book fetched now (`engine.exits`) |
| `recheck` | **the Stage-6 pre-send check** (3.5): fetch one book from the VM now and decide buy / sell again (`engine.recheck`); `--key-book`: the key's own book for the agent's key<->metal rebalance (`recheck_key`); exit 0 go, 1 no, 2 fetch failed |
| `listings` | **the Stage-6 listing filter** (3.1): one item's newest stored book, every listing tradable or skipped with why (`engine.listings`) |
| `picks` | **the Stage-6 picker** (3.0): the buys the agent makes this pass -- `predict_latest` + `pick` with `forecast.AGENT_RULE` (`p_up >= 0.65`, 0.1 <= ask < 2 keys since 2026-10-30, best `p_up` first; `--rule model` = the exp_ret rule), <= 10, skip held, stale guard, `--json` for the Node agent |
| `replay` | **the paper test** (2026-10-26): the out-of-sample walk-forward picked pass by pass with the agent's rule and traded on the stored books by the agent's listing rules (`engine.replay`); `--min-p-up`, `--rule model`, `--preds` to reuse the predictions, `--csv` every pick |
| `groups` | **the per-group backtest** (2026-11-01, `engine.groups`): the replay on every roster group alone (non-admitted goods included, each kind its own series), beside the admitted goods over the same window; one row per group with goods, rows, base rate, AUC, bought / sold / stuck, win rate, return a trade, keys, `thin`; `--since`, `--train admitted\|all`, `--preds`, `--csv`, `--trades` |

`events`, `signal-check` and `ratchet-watch` take `--new-high` to use the new-high
event (a step above the prior-24h high) instead of any step. `dashboard` writes
`engine_dashboard.html` (~15 MB, self-contained, gitignored); its template is
`engine/screens/dashboard.html` and its data comes from `dashboard.build()`.
`dashboard --serve [--port 8765]` serves the same page from a stdlib HTTP server
bound to **127.0.0.1 only**, plus `GET /api/book?item=&q=&v=&pass=` →
`book_at()` = the snapshot's provenance (scraped_at, page depth, key rate) +
`explain_book`, and `GET /api/history?item=&q=&v=` → `history_at()` = every stored
listing of the series at every pass with its verdict letter, plus each pass's fence
medians (top 7 and over all) — the item explorer's listing dots and fence-median lines
(toggles: `bid1_fill`, `bid_med`/`ask_med`, fence medians + the trust-0 buy cap, every
listing). The db stays read-only; restart it to pick up new passes. The Book audit tab
(static too) tallies every verdict over the latest pass and, per book, the top-7 vs
all-listings medians and how many verdicts the depth changes (sortable). A launch
config for the app's preview pane is `.claude/launch.json` (`engine-dashboard`).

`signals.py` picks a feature when its top or bottom 10% has a step rate ≥ 1.5×
the base rate; the score is the average within-pass percentile rank of the picked
features. **Not a trained model** — it is the transparent baseline a Stage-5
model has to beat; the backtest (pick on the early 70% of passes, score the rest)
says by how much it beats random.

Ported from the retired `backpack/analysis/` scripts (2026-09-24): gainers and
climbers match the old output (climbers now slopes over wall-clock hours, same
picks); **movers now reads the fenced bid** (the old one averaged the unfenced
top 5, so a single 50-key flier read as a mover); timeseries drops Unusuals.

### The bot registry (static part, 2026-09-24)

Two halves on either side of the `trading.db` seam — neither imports the other:

- **`backpack/bot_registry.py`** (collection, writes): `sync [--refresh-profiles]`
  registers newly badged accounts (incremental scan from the last watermark, books
  decoded through `book_codec` in Python since 2026-10-04 — it was a SQL `json_each`
  scan; 2.7 s over 23k books), pulls the `GROUPS` rosters, fetches
  profile XML for accounts without one (1/s, stops on a 429). `mark <steamId>
  <provider> [--note] [--new]`. Tables in `data-model.md` → "Bot registry".
- **`engine/bots.py`** (read): `registry(db)` → one row per registered account with
  `provider, provider_source, conflict, is_bot, groups, name, member_since, …`;
  `GROUP_RULE` (group → provider, roster-enough) is the rule and lives only here —
  keep its keys in step with `bot_registry.GROUPS`. `account_stats(db)` →
  `n_listings, badge_share, n_items, listing_name` per steamId, cached
  incrementally in `engine_cache/account_items.parquet`.
- Screen: `python -m engine bots [--unknown] [--provider P] [--limit N]`.

### Behaviour groups (2026-09-25)

**`engine/bot_groups.py`** (read) — which bots can be treated as one behaviour:

- `load_bot_obs(db)` → one row per (steamId, item, quality, side, pass) of every
  account's raw **valid** listing (read rules 1–3, pre-fence), Unique+Strange:
  `price, median, rank, n_valid, gap_ref` (ref gap to the next listing — exact from
  the ref fields when both share a key count) + `t`. Cached incrementally in
  `engine_cache/bot_obs_v<READ_KEY>.parquet` (`kept` = the listing got past the fence;
  5.4M rows, 31 MB; cold 36 s,
  warm ~3 s). The base table every bot metric is a groupby over.
- `pricing_modes(obs, as_of=None, window_h=168, min_moves=30, static_below=0.01)`
  → per account, point-in-time: `static` (reprices <1% of transitions over ≥30 book
  moves), `repricing`, or `thin` (too few book moves).
- `behaviour_groups(db)` → per registered bot: `fleet` (the provider if it is one
  operator — not in `SERVICE_PROVIDERS = {gladiator, tf2autobot, unknown,
  other_bot}` nor `NOT_ONE_OPERATOR = {tf2syntax}`), `pricing_mode`, and
  **`group`** = the fleet, else `provider:pricing_mode` (e.g. `gladiator:static`).
- `fleet_check(obs, groups)` → per provider, the share of co-listed pairs quoting
  the same price, plus the cross-provider baseline (`.attrs`).
- `groups_asof(times, db, obs)` → `behaviour_groups` at each as-of time from data up
  to then (one row per as_of × bot) — the point-in-time group for backtests.
  `pricing_modes(..., tr=transitions(obs))` reuses one transitions table across many
  as-of times (a transition counts when both its ends are inside the window).
- Screen: `python -m engine bot-groups [--window-h H] [--members GROUP]`.

### Per-bot behaviour features (the dynamic part, 2026-09-25)

**`engine/bot_features.py`** (read) — measurements in `data-model.md` → "Per-bot
behaviour features":

```python
moves = listing_moves(load_bot_obs(db))   # + trans / raise / lower / new / top / cut1 / prem,
                                          #   lead + lead_f (known at t_lead = t + LEAD_H)
acct = account_features(moves, db=db)     # per (steamId, pass_id): group + ACCOUNT_FEATURES,
                                          #   decayed (HALF_LIFE_H=48) and shrunk (K_SHRINK=20)
book = book_bot_features(moves, acct)     # per (item, quality, pass_id): BOOK_BOT_FEATURES
book = load_book_bot_features(db)         # the same, cached in engine_cache/bot_book_v<READ_KEY>_h_k.parquet
```

- `decayed_sums(events, queries, key, cols, half_life_h, t0)` — the one decay
  primitive: per key, `Σ value · 0.5^((t_query − t_event)/h)` over events at or before
  each query (cumulative sums + `merge_asof`, no per-row loop).
- Point-in-time throughout: a pass's features use that pass and earlier ones; a lead
  counts only after its 6 h follow-up; the shrinkage group is `groups_asof` at the last
  midnight before the pass (computed at every UTC midnight of the data).
- The book cache is recomputed whole when new snapshots arrive (~20 s; the decay makes
  every later value depend on them).

### The buy-level forecast (Stage 5, 2026-09-25)

**`engine/forecast/`** (one module until 2026-11-07; import from `engine.forecast`, as before): `config` the numbers, `labels` what the model learns, `features` what it sees, `frame` both on one table + the universe, `models` xgboost + the saved model, `live` `pick` + `predict_latest`, `backtest` `walk_forward` + `simulate`. To swap a function at runtime, set it on the module that calls it (`fc.frame.load_panel`, `fc.features.load_book_bot_features`, `fc.live.fit_models`). Definitions, measurements and the backtest are in
`data-model.md` → "Buy-level forecast"; the decisions in `adr/0004-buy-level-forecast.md`.

```python
frame = forecast_frame(db)            # load_panel + drop_misreads + label_events + build_features
                                      #   + add_level_features + add_reach_features
                                      #   + add_bot_features + label_forecast
rep = walk_forward(frame)             # {"folds": per test day AUC / prec@k / ret@k / baselines,
                                      #  "preds": the test rows with p_up, exp_ret}
rep = walk_forward(frame, since=t0)   # a forward test: every labelled day since t0 (test_blocks)
trades = simulate(rep["preds"])       # non-overlapping portfolio, one row per trade
picks = predict_latest(frame)         # latest whole pass's universe: p_up, exp_ret, buy, up_at,
                                      #   sell_at, drivers (SHAP) -- best exp_ret first
picks = predict_latest(frame, model_dir=cache_dir_for(db))   # ... by the day's saved model
buys = pick(picks, held)              # the buys: <= MAX_NEW_BUYS (10), over the bar, >= MIN_PRICE,
                                      #   no series in `held` or twice -- simulate buys by it too
```

- `pick(scored, held, k)` (2026-10-19, Stage 6 step 3.0) — the one buy rule: `simulate`
  calls it each pass (held = positions not exited yet), the live picker (`picks` screen)
  with the agent's held series. `predict_latest`'s `buy` flags every row over the bar;
  `pick` is what gets traded. Moving the rule out of `simulate` changed no backtest
  trade (checked on two planted sets, four settings).
- Screen `python -m engine picks [--ledger] [--held held.json] [--json out.json] [--max-age 90] [--retrain]` —
  the agent's input: `predict_latest` + `pick`, as JSON (pass id/time/age, the rule, counts,
  the picks; NaN → null for Node). A pass older than `--max-age` minutes (passes are
  hourly) is `stale` and picks nothing. `--held` = a JSON list of `{item, quality, variant}`;
  `--ledger` reads the held series from the agent's ledger (`backpack/agent.db`, 3.6).
- `engine.exits` (2026-10-19, Stage 6 step 3.7) — `decide(position, book, now)` →
  `{action: hold|sell, why: take_profit|horizon|overdue|..., min_keys, listing,
  hours_to_horizon}`: the label's exit (take-profit before `horizon_at`, the best bid from
  it, wait `EXIT_WAIT_H` for a buyer) on tradable bids, against the price paid; past the
  wait it keeps selling (`overdue`). Built on `recheck_sell`.
- `engine.recheck` (2026-10-19, Stage 6 step 3.5) — decide again on a fresh book (the
  book server's answer) right before an offer: `recheck_buy(pick, book)` (<= `MAX_AGE_S`
  120 s old, still in the universe, cheapest tradable sell <= the pick's ask1),
  `recheck_sell(position, book, min_keys)` (best tradable bid >= min_keys, <= MAX_JUMP x
  entry). Returns `{ok, reason, listing, candidates, watch_id, scraped_at, age_s}`;
  `candidates` (2026-10-26) = the listings to try in turn when a bot does not answer: buys,
  every tradable sell <= ask1; take-profit sales, every bid >= min_keys; horizon sales (min
  0), every bid within `HORIZON_RANGE` (2%) of the best. `engine.exits` passes them on.
  `recheck_key(book)` (2026-10-30) — the key's own book for the agent's key<->metal
  rebalance: `buy` = tradable sells priced in metal only, at most `MAX_KEY_GAP_REF` (0.33
  ref) over the best bid or the cheapest sell, whichever is higher; `sell` = tradable bids at
  most 0.33 ref under either, whichever is lower (2026-11-01, user: the window reaches past
  the cheapest listing itself, and the agent tries every bot in it, not `max_tries`);
  `ask_ref`, `bid_ref`, `key_ref` (their mid). The 2026-10-30 14:38 key book: bid 64.66, the
  usual sell 64.77 ref (1 scrap apart), two sells at 64.11 under the bid; 2026-11-01 12:41 it
  was crossed (63.66 ask / 64.11 bid) and the 63.66 sellers ignored three cycles of offers.
- `engine.replay` (2026-10-26) — the paper test: `replay(preds, con, rule, k)` picks each
  pass of out-of-sample predictions (`walk_forward`'s `preds`) with `forecast.pick` (default
  `AGENT_RULE`) among the series not held, and `trade(books, row)` trades each pick on the
  stored books (`Books`: one series' snapshots from the pick's pass on, decoded and kept):
  buy the cheapest `tradable_sells` unless dearer than ask1, sell into the best
  `tradable_buys` at the take-profit or from the horizon, `stuck` without a bid by horizon +
  wait. `summary(trades, days)` = bought / won / per trade / keys / peak keys in play.
- `engine.groups` (2026-11-01): the per-group backtest behind `engine groups`.
  - `roster_groups(con)` gives each roster good a group: type × quality × Strange-elevated ×
    craftable × Australium. Crates and Strangifiers are their own type. `NEVER_ADMIT` (the
    key) is marked `currency`.
  - `backtest(frame, con, groups, since, train)`:
    - takes a `forecast_frame(scope="all")`;
    - runs one `walk_forward` from `since`. The default is `default_since`, 72 h after the
      first non-admitted book. It trains on the admitted goods (`train="all"`: every group);
    - then runs `replay` on each group's out-of-sample rows alone, plus once on the admitted
      goods together (the `BASELINE` row);
    - returns {table, trades, preds, since}. A group with fewer than `MIN_SOLD` (10) sold
      trades is marked `thin`.
- `engine.listings` (2026-10-19, Stage 6 step 3.1) — which listings of a book the agent may
  trade with: `tradable_sells(book, trade_urls)` (cheapest first, with `asset_id` from
  `440_<assetid>`), `tradable_buys` (highest first), `explain` (every listing + `reason`,
  None = tradable), `trade_urls(con, ids)`. Checks: `engine.book` rules 1-4 (called, not
  copied), then `bot` + `instant`, not `UNTRUSTED` (any level), a trade URL, an asset id.

- `whole_pass(frame)` — the pass `predict_latest` scores: the newest with ≥ 90% of the
  usual rows per pass, so a pass the scraper is still writing (a pass takes 5–15 min,
  `passes.ended_at`) is passed over for the one before it.
- `since` / `forecast --backtest --since <UTC time>` — test days are consecutive
  blocks from that time to the last label: the honest check of a recipe frozen then
  (every choice made up to then was tuned on the days before it).
  `RECIPE_FROZEN_AT` records the current recipe's freeze (2026-09-23 23:00 UTC); the
  dashboard's Forecast tab shows the forward test from it (`dashboard._forward`).

- Labels: `up_<H>h` (held `bid_med` reaches ask1·(1+TARGET)), `ret_<H>h` and
  `exit_h_<H>h` (sold into `bid1` at its own price the first pass it is ≥
  ask1·(1+TAKE_PROFIT), else the best bid at the horizon — no bid there: held up to
  `EXIT_WAIT_H` (24 h) for the first buyer, else marked at the last bid seen;
  `_end_exit`), `close_h_<H>h` (hours until the row's labels are known — `walk_forward`
  purges on it). `drop_misreads(panel)` blanks the bid reads (`BID_READS`) of a pass whose
  buy-order median is > `MAX_JUMP` (3) × the ask before any feature or label is built,
  and a bid above 3 × the entry ask is never a sale (2026-09-26). Defaults `HORIZON=48`,
  `TARGET=0.02`, `TAKE_PROFIT=0.04` (0.05 until 2026-09-26), `MAX_SPREAD=0.05` (universe),
  `TRAIN_MAX_SPREAD=0.10`, `MIN_EXP_RET=0.01` (BUY; was 0.005 until 2026-09-26, when the
  exit rule changed — data-model.md), `MIN_PRICE=0.1` (BUY and `simulate` need the ask
  ≥ 0.1 key; user 2026-09-26), `EXIT_ON="bid1_fill"` (every exit and the reach features
  read the best bid that fills; `bid1` until 2026-09-26).
- Features: `FORECAST_FEATURES` = engine `FEATURES` + `LEVEL_FEATURES` +
  `REACH_FEATURES` (`add_reach_features`: the best bid's recent highs vs today's ask, and
  how often / how lately it was at the take-profit — added 2026-09-26) +
  `BOOK_BOT_FEATURES` + `CHURN_FEATURES` (138).
- Models: `fit_models` → 3 seeds × (XGBClassifier, XGBRegressor pseudo-Huber) in the
  backtest, `LIVE_SEEDS` = 9 in `predict_latest` (halves the seed noise: 2 of ~41 BUY
  flags flip between two runs vs 5 with 3 seeds; 57 s vs 19 s on the GPU, 2026-09-26);
  xgboost is imported lazily (only the forecast needs it).
- **The day's saved model** (2026-10-26, user): `predict_latest(..., model_dir=d)` scores with
  `live_models` — the model in `d/live_model.pkl` (pickle of the seeds' boosters + meta:
  `trained_at`, `n_train`, `base_rate`, `label_end`, `key`) while it is under
  `MODEL_MAX_AGE_H` (24 h) old and its `model_key` matches (features, horizon, target,
  take-profit, train spread, seeds, qualities, xgboost params and version); else a new
  model fit **from scratch** on every labelled row (never fine-tuned), written to a temp
  file and renamed. `picks`, `forecast` and `dashboard --forecast` pass
  `cache_dir_for(db)` and take `--retrain` (rebuild now); `picks --json` reports
  `model_trained_at`. Without `model_dir` it fits every call (the tests, the research
  yardsticks). Why daily: a frozen model ranks as well but its exp_ret falls behind (data-model.md,
  "Frozen vs retrained"). `picks` 106 s → 36 s once the day's model exists (the rest is the frame).
- **CPU inference time (measured 2026-11-06, GPU hidden from the process):** the saved model (9 seeds
  x 2 targets x 500 trees, 138 features) on pass 535's 1,734 scored books, Ryzen 5 7600X, no agent
  running. `predict`, whole batch: 0.063 ms a book on 12 threads (0.11 s), 0.077 on 6, 0.19 on 1.
  One book at a time: 38 ms on 1 thread, 53 on 12 (mostly per-call overhead; the agent scores a
  batch). `drivers` (SHAP) adds 0.092 ms a book on 12 threads. Warm `forecast_frame` + `predict` +
  `drivers`: 62.7 s, 36 ms per scored book, ~99.6% of it the frame, which rebuilds the whole history
  (1.5M rows). The frame took 281 s on its first run after 4 days without a read.
- `device()` — where xgboost trains: `$FORECAST_DEVICE` (`cpu` / `cuda`), else `cuda`
  when a GPU answers a one-tree fit, else `cpu`. On the RTX 4070 Ti a fit is ~3× faster
  than 4 CPU threads (3.3 s vs 9.2 s, same AUC ±0.003, 2026-09-26) and leaves the CPU
  to the scraper; the fitted booster is handed to the CPU for prediction. The tests pin
  `FORECAST_DEVICE=cpu` (a tiny planted set splits differently on the GPU's histograms).
- Screen: `python -m engine forecast [--backtest] [--buys-only]
  [--min-price K (default 0.1)] [--horizon H] [--target X] [--take-profit X] [--max-spread X]`;
  the same picks and track record as a dashboard tab: `python -m engine dashboard
  --forecast [--serve]` (`dashboard._forecast`).

### TimesFM — removed 2026-09-26

The optional TimesFM-3 features (`engine/tsfm.py`, `backpack/tsfm_forecast.py`,
`forecast --tsfm`, its `.venv-tsfm`) were removed on the user's call ("remove it");
they had measured no consistent gain (data-model.md → "Buy-level forecast",
ADR 0004). The code is in git history before the removal commit.

## `sync/pull.py` — the PC's end of the Azure link (2026-10-17)

The collector ran on an Azure VM (`collector/`, `sync/server.py`, `deploy/azure/`). The
PC-side tools:

```
python sync/pull.py --ssh azureuser@<vm> --key <pem> [--watch 3600]   # new rows -> backpack/trading_app.db
```

`pull.py` opens `ssh -L` to the VM's gRPC server (`sync/server.py`, 127.0.0.1
only) and appends what the replica lacks. How the sync works (per-table modes,
`book_dict` last, the guards) is in `sync/tables.py` and `sync/server.py`. `backpack/trading_app.db` is a read-only replica — never write it.
A column the collector adds later is added to the replica with the type and `DEFAULT` of
the VM's own definition (`_column_defs`, 2026-10-31), so the replica's old rows read the
default (the kind columns: the plain good), not NULL. `roster` syncs in `changed` mode.

## Tests

Offline, in `backpack/tests/`, run with `backpack/run_tests.ps1` (bare `python`). Seven files need the collector (`collector/`, `sync/server.py`) and report SKIP without it (`tests/_paths.py`).
`test_engine_pipeline`, `test_bot_registry`, `test_bot_groups` and `test_book_codec`
import the collector's writers (`snapshot_store`, `bot_registry`) from
`collector/`.
`test_sync.py` runs a real `server.py` (`sync/server.py`) against this repo's
`sync/pull.py` over a temp source: identical replica, only new/changed rows next time, a
later-added column, an unknown table refused, a replica ahead refused.

- `test_book_codec.py` - zstd book storage: plain and zstd both decode, no
  dictionary → plain JSON written, the after-pass bootstrap (thin pass trains nothing,
  a full pass trains once and compresses faithfully, later inserts compressed after
  `reset_codec`), retraining keeps old rows readable, a missing dictionary is a loud
  error, `bot_registry` finds bots inside compressed books, `book._parse` refuses a
  compressed book while the engine's db readers (`load_panel`, `load_bot_obs`,
  `account_stats`) read them through `book_decoder`, the app db as default with its own
  cache folder, `show` / `stats` (no table added on a plain DB).
- `test_panel.py` - the panel's agent side with a fake `node`: each action runs the agent
  command it stands for and nothing else (bad ids, out-of-range hours, unknown keys and
  actions refused, nothing run for them), `start` refused while the agent runs, every
  action audited; the guard (token, Host, Origin); over HTTP the token is only in the page.
- `test_stats.py` - `engine.stats` + the panel payload: decile edges, PSI and its bands,
  summaries (missing, bins, median), the atomic cache write, drift now vs reference and
  per day, a refresh over a temp db (books, minutes, items gone / back, breadth, the
  chained index, capped share, listings), a pass re-summarised once its last book lands,
  strict JSON payload.
- `test_engine_book.py` - `engine.read_book`: flag filter, dedupe direction, the
  buy fence (the 50-key flier case), the trust-modulated sell fence, missing-not-zero,
  the fence depth (a 30-deep book whose top cluster the all-listings median would fence
  out; a ≤ 7-deep book reads as before).
- `test_engine_pipeline.py` - the engine over a real temp db written through
  `snapshot_store`: scope, pricedb pairing, cache append + rebuild on reset, the
  step, gap and new-high rules, censored labels, no-look-ahead features, signal
  tails/backtest, and the dashboard payload.
- `test_bot_registry.py` - the bot registry over a temp db and a fake Steam:
  badge-only registration (any quality, incremental), roster members stay once
  seen, profiles fetched once, `mark` validation, the provider precedence, and
  `account_stats` with its incremental cache, the gladiator.tf API roster, and the
  Steam-429 stop (after one rate-limited roster the other Steam rosters are skipped).
- `test_bot_groups.py` - `engine.bot_groups` over a hand-built market: static vs
  repricing vs thin, the window, fleets vs provider x mode, `NOT_ONE_OPERATOR`,
  the fleet check, the exact one-scrap `gap_ref`, and the incremental cache.
- `test_bot_features.py` - `engine.bot_features` over hand-built obs: the decay
  arithmetic, raise / lower / new / one-scrap cut / lead flags, the lead known only
  after 6 h, k=0 vs k→∞ shrinkage, point-in-time (later passes never change earlier
  features), and the per-book aggregates.
- `test_forecast.py` - `engine.forecast`: the held level (a one-pass spike or a
  level before a >3 h gap never counts), the take-profit sold into the first best bid
  past it at its own price (a one-pass spike included), the `MAX_JUMP` misread guard,
  censoring (a collector outage censors; a gap in one series is data), no look-ahead in the level features, the purged walk-forward on a
  planted signal, the non-overlapping portfolio, `predict_latest`, and
  `forecast_frame` end to end over a temp trading.db (caches in the temp dir).

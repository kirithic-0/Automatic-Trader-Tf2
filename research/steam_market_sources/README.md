# Where to get Steam Community Market price history for TF2 items (2026-10-27)

Research mode. Goal (user, 2026-10-27): backfill each collected item with its Steam
Community Market (SCM) price graph. Items that are on backpack.tf but not on the SCM are
skipped for now. The KB has no SCM facts yet (`docs/glossary/tf2-economy.md` SCM entry
is still TO FILL IN). Nothing below was downloaded at scale; the probes were a handful of
unauthenticated GETs from this PC.

## Scraping the listing page works logged out (measured 2026-10-27)

The SCM listing page is now server-rendered React (`/public/ssr/*.js`). Its HTML carries
the page's React Query cache, escaped inside a script string. The cache holds the
queries `["market","pricehistory",440,<name>]` and `["market","orderbook",440,<name>]`,
**with data, when logged out.** Parse it with `ph_parse.py` (scratchpad; to be rebuilt):
replace `\"` with `"`, find the `queryKey`, then `raw_decode` the `{"state":{"data":…}}`
just before it.

- `pricehistory.data` = `{ecurrency, prices:[{time (unix), price_median, purchases}]}`.
  For the Key: **5,757 points, daily from 2012-12-12, hourly for the last 30 days (720
  points)**, up to the hour before the fetch. The sales count is units sold.
- `orderbook.data` = `amtMaxBuyOrder, amtMinSellOrder, cBuyOrders, cSellOrders,
  rgCompactBuyOrders/rgCompactSellOrders` (flat `[price, qty, price, qty, …]` in minor
  units). This is a free current SCM book.
- **Currency is set by IP: `ecurrency 24` = INR from this PC.** `?currency=1`, `?cc=us`
  and a `steamCurrencyId=1` cookie did not change it. Store the currency, and normalise
  (e.g. divide by the Key's own SCM series) rather than assuming USD.
- **Pace**: 15 different items at **one page every 4 s: 15 × 200, no 429**. Pages are
  ~600 KB and take ~0.4-1.2 s. The faster wall was not probed, because a market IP ban
  would hit this PC's Steam use. ~2,900 items ≈ 3.2 h and ≈ 1.7 GB transferred.
- **Burst at 5 req/s (2026-10-27, Cloudflare WARP on, IPv6 egress, loc IN)**: random
  items from pass 382 (Unique = bare name, Strange = `Strange ` + name). **100 pages
  came back 200 in 20 s; the 101st returned 429** and the run stopped (`burst.py`, scratchpad).
  So there is a budget of ~100 pages per window; the window length was not measured.
  At 5/s this costs 20 s of work per window. Latency p50/p90 was 709/933 ms. Currency was
  still INR (24). WARP egress IPs are shared with other WARP users, so their budget may
  be shared too.
- **Name hit rate**: 69 of 100 pages had history. Strange: 41/42. Unique bare name: only
  28/58. The 30 misses include Birdcage, Team Player, Abyss Diver, Noh Mercy and Federal
  Casemaker. Hypothesis, **not verified**: many Unique SCM names carry a `The ` prefix
  that bp.tf drops. Test that before building the name map.
- **Cooldown** (`cooldown.py`, one Key page a minute): still 429 at +4.2 min, **200 at
  +5.2 min**. So the budget is ~100 pages per ~5 min ≈ 20 pages/min. That is ~2.4 h for
  2,900 items on one IP, about the same as the 4 s pace.
- **`The ` prefix** (8 missed Unique items, both spellings): it **fixes 2 of 8**, The Team
  Player (1,698 pts) and The Abyss Diver (490 pts). Still missing under both spellings:
  Birdcage, Noh Mercy, Federal Casemaker, Team Captain, Engineer Mask, Brawling Buccaneer.
  So the rule is "try both". The rest are not on the SCM or use a third spelling (not
  known; not in the KB).
- **Not on the SCM**: a ~150 KB page **with no `["market","description",…]` query** and
  no price history. Seen for Refined Metal, Reclaimed Metal, Team Captain and "Strange
  Part: Kills". The user confirms (2026-10-27) that some bp.tf items simply do not exist on
  the SCM, so the scraper **skips these pages**. The one risk is a wrong name, which is
  skipped the same way. A one-off count of skips against `search/render` would catch
  that; it is optional.

The `/market/pricehistory/` JSON call still needs a login (below); the page does not.

## Steam's own endpoints (measured 2026-10-27, logged out, this PC's IP)

| endpoint | logged out | what it gives |
|---|---|---|
| `/market/priceoverview/?appid=440&currency=1&market_hash_name=…` | **works** (200) | now only: lowest ask, 24 h median, 24 h volume. Key: `$2.16 / $2.17 / 50,396` |
| `/market/pricehistory/?appid=440&market_hash_name=…` | **400, empty body** | the whole graph, but needs a logged-in `steamLoginSecure` cookie |
| `/market/listings/440/<name>` page | 200, but **no `var line1`** | the graph is embedded in the page only when logged in |
| `/market/search/render/?appid=440&norender=1` | (429 before it ran) | the list of every TF2 item on the SCM, 100 per page; how to enumerate names |

**Rate wall**: after ~5 requests in about a minute, both `pricehistory` and `search/render`
returned **429 with body `null`**. The SCM rate limit is per IP and tight. Third-party
trackers guess 15-25 req/min; forum reports say a ban can last hours. None of these
numbers come from Valve: **Valve publishes no limit** for any market endpoint (checked
again 2026-10-27). Community reports say the limit is per IP (since Oct 2022), varies with
login state and IP reputation, and that **datacenter IPs get 429 from the first request**.
So the Azure VM is likely a poor host for this; a home IP is better. Measure the real
logged-in limit on the first run.

What `pricehistory` returns when logged in (general Steam knowledge, **not verified here**):
`{"success":true,"price_prefix":"$","prices":[["Oct 27 2026 01: +0", 2.17, "2104"], …]}`.
Each row is a median sale price and units sold. Rows are daily for the item's whole SCM
life and hourly for roughly the last month. Prices are in the account's wallet currency.
Verify this on the first logged-in call.

## history.tf (probed 2026-10-27)

An SPA behind Cloudflare, but **no challenge**: plain HTTP works with no login, so pydoll is
not needed. Its own JSON API (found in `/assets/index-*.js`):

- `GET /api/analysis/scm/item?item_name=<SCM name>` → `{item_name, quality_id,
  historical_days, price, last_updated, analysis{price_change_percent, sold_count},
  trend_14d, price_history:[{date, avg_price, num_sold}]}`. It is **daily, USD and only
  ~90 days** (89 points for Beachcomber's Brim, 2026-07-30 → 10-27). `days=` and
  `historical_days=` are ignored. An unknown name gives 404 `{"detail":…}`.
- `GET /api/analysis/scm/items?limit=100&offset=…&sort_order=desc` lists everything:
  **3,828 TF2 SCM items in 39 pages** (all 200 at 2 s apart; no rate-limit headers). But
  the list only embeds the last ~14 days (median 11 points), whatever `historical_days: 365` says.
- **Coverage of our 2,881 (pass 382)**: 1,293 match by plain name and 155 more with `The `.
  **1,433 (50%) are not in it at all.**
  Its names agree with Steam's: The Team Player and The Abyss Diver with `The `; no
  Birdcage, Team Captain or Engineer Mask; Noh Mercy only Genuine; Federal Casemaker only
  Strange. So it is a usable **name map** for the half it has.

**Verdict**: worse than Steam's own listing page (90 d daily vs full history from 2012 +
30 d hourly, and half the coverage). Its one plus is USD with no Steam rate limit. Use it
as a name cross-check, not as the backfill source.

## Third parties

| source | TF2? | history | cost | note |
|---|---|---|---|---|
| **steamapis.com** `GET /v2/price-history?game=TF2&name=…&days=&dataPoints=` | TF2 game code listed | 30 d / 180 d / full on History plans | free = latest only; €4.99 / €19.99 / **€29.99/mo full** + request quota (500 free, 100K €9.99) | returns `timestamp, priceUSD/EUR/CNY/RUB`, downsampled to `dataPoints` (default 100); **no volume field documented** |
| **steamwebapi.com** `GET /steam/api/history?market_hash_name=&game=` | TF2 in supported games; history for TF2 not confirmed | daily, "≥ 90 days, often more" | credit packages, prices not on the docs page | `POST /steam/api/items/history` = many items per call |
| **pricedb.io** `GET /api/item-history/{sku}` | yes (TF2-only) | full, per SKU | free | uses our SKU key, but the only source seen in its examples is `bptf`; SCM history not confirmed |
| backpack.tf `IGetMarketPrices` | yes | **now only** | bp.tf API is paid | useless for a backfill |
| SteamDB | no | — | — | store prices only; TF2 is free to play |

No ready-made TF2 SCM history dataset was found. iflow.work archives only Dota 2/CS2 and
is no longer maintained.

## Recommendation

**Update, same day: scrape the logged-out listing page** (top section). It holds the
same series without a cookie, and the paragraphs below are kept for the record. The
first plan was **Steam's own `pricehistory` with a logged-in cookie**. It is free and gives the full
daily-plus-hourly graph **with volume**. Volume is the part the paid APIs drop or
downsample.
Cost is wall time. ~2,900 series at ~1 request / 4 s on one IP is ≈ 3-4 h, a one-off
overnight run. After that a daily top-up of the hourly tail is cheap.

1. Names: TF2 SCM `market_hash_name` is the item name, with the quality as a prefix for
   non-Unique items (`Strange …`). Build it from our SKU (`defindex;quality`). Check it
   against `search/render`, which also says which items are not on the SCM. Those are skipped.
2. Fetch: the user puts `steamLoginSecure` in a local `.env` (never in chat, never
   committed). One request at a time, 429 → back off for minutes, never retry hot. Set
   `currency=1` on the account side, or store the currency.
3. Store: a new table beside the books, e.g. `scm_price_history(sku, ts, median, volume,
   granularity)`. Write it to a separate DB or a new table, not inside `book_json`.

steamapis' €29.99 full-history month is the fallback if the cookie route is blocked. It
has no volume and is downsampled, so it is a worse signal.

Sources: Steam endpoints probed directly; docs.steamapis.com (`/market-data/rest/price-history`,
pricing page); steamwebapi.com/llms-full.txt; docs.pricedb.io; Hridaya Steam Market
Tracker docs (rate-limit guess); Steam forum threads on market IP bans.

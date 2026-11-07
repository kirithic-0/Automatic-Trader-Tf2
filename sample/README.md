# The sample

`sample.db` is a real slice of the collection, anonymised: **50 goods over 7 days**
(2026-10-17 00:21 to 10-23 23:06 UTC, 164 hourly passes, 8,073 books, 10 MB). The data
comes from backpack.tf's public classifieds.

It has the collector's schema (`collector/snapshot_store.py`), so every tool in the
repository reads it like the full database:

```powershell
cd backpack
python -m engine forecast --db ../sample/sample.db
cd ..
python collector/scrape_run.py --db replayed.db --book-port 0      # replay it through the collector
```

## How it was made

`build_sample.py` builds it from the full database, which is private. It is kept as a
record of what was done:

- **Which goods:** 50 admitted, plain Unique and Strange goods, drawn with a fixed seed
  (2026) from the 300 with the most listings in the window.
- **Which books:** every book of those goods in those passes, with every listing in it.
- **Anonymised:** each listing's `steamId` is replaced by a random 12-hex-digit code. An
  account gets one code throughout, so its behaviour across books is kept, and the mapping
  was never written down. `user` (the display name), `id` (the listing id) and `ua` (the
  bot software) are dropped.
- **Kept beside the books:** the passes, the 50 roster rows, the key-rate history, and the
  bot registry (`bot_account`, `bot_group_member`) re-keyed to the same codes.
- **Left out:** trade-offer URLs (`trade_url`), Steam profiles (`steam_profile`), the bot
  labels (`bot_label`), third-party price baselines (`snapshot_baseline`, `pricedb_items`)
  and the held-item watch (`watch_snapshots`). Their tables are present and empty.
- **Compressed** with a zstd dictionary trained on the sample (`book_dict`). Read
  `book_json` through `book_codec.Codec`, never with `json.loads`.

## What it can and cannot show

It is enough to run every step: the read rules, the features, the model, the backtest
and the collector's pass loop. It is far too small to reproduce the reported results,
which came from about 3,000 goods over seven weeks. With no trade-offer URLs, the
replay (`engine replay`) finds no tradable seller and buys nothing.

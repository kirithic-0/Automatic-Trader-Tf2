"""Offline tests for zstd book storage (`book_codec`, 2026-10-04) -- no network.

Pins:
  * `Codec` -- plain JSON and zstd frames both decode; no dictionary -> encode
    returns None (the writer stores plain JSON); a frame names its dictionary, and
    one missing from `book_dict` is a loud error, never an empty book.
  * the bootstrap -- a fresh DB written through `snapshot_store` is plain until a
    full pass exists; `ensure_dict` (scrape_run's after-pass hook) then trains once,
    compresses the plain rows byte-for-byte faithfully, and the next inserts are
    compressed; it does nothing on later calls.
  * retraining -- a new dictionary gets the next id, new rows use it, old rows still
    decode with theirs.
  * readers -- `bot_registry.register_new` finds bot accounts inside compressed
    books (it used SQL json_each before); the engine's parser refuses a compressed
    book instead of reading it as empty; `show` prints the book; `stats` on a DB
    without a dictionary does not add the table.

    python test_book_codec.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
_paths.use_collector()
import os as _os
import contextlib
import io
import json
import random
import sqlite3
import sys
import tempfile

import zstandard as zstd

import book_codec as bc
import bot_registry as br
import snapshot_store as ss
from engine import book as engine_book

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(name)


rng = random.Random(7)
BOTS = [f"B000000000000{i:04d}" for i in range(40)]
HUMANS = [f"H000000000000{i:04d}" for i in range(40)]
UAS = ["Gladiator.tf", "TF2Autobot v5.12.1", "tf2-trading-bot"]


def listing(side, steam, bot):
    keys = rng.choice([0, 0, 0, 1, 2])
    ref = round(rng.uniform(0.11, 60), 2)
    return {"user": ("bot " if bot else "user ") + steam[-4:], "steamId": steam,
            "premium": rng.random() < 0.2, "premiumFlag": rng.random() < 0.5,
            "price": {"keys": float(keys), "ref": ref, "usd": None,
                      "raw": (f"{keys} keys, " if keys else "") + f"{ref} ref"},
            "listingType": "bot" if bot else "normal",
            "dealType": "instant" if bot else "negotiate",
            "strange": False, "spelled": rng.random() < 0.05, "painted": False, "parted": False,
            "id": f"440_{rng.randrange(10**10, 10**11)}" if side == "sell"
                  else f"440_{steam}_{rng.getrandbits(128):032x}",
            "ua": rng.choice(UAS) if bot else None, "listedAt": 1791000000 + rng.randrange(10**6)}


def app_row(i, pass_no):
    def side(name):
        out = []
        for _ in range(rng.randrange(5, 30)):
            bot = rng.random() < 0.7
            out.append(listing(name, rng.choice(BOTS if bot else HUMANS), bot))
        return out
    buys, sells = side("buy"), side("sell")
    return {"item": f"Item {i}", "variant": 0, "quality": 6, "source": "app",
            "scrapedAt": f"2026-10-04T{pass_no:02d}:00:{i % 60:02d}.{i:06d}+00:00",
            "buyOrders": buys, "sellOrders": sells, "nBuyPage": len(buys), "nSellPage": len(sells),
            "nBuyTotal": len(buys), "nSellTotal": len(sells), "counts": {}}


def write_pass(eng, pass_no, n):
    pid = ss.start_pass(eng, n_items=n)
    for i in range(n):
        row = app_row(i, pass_no)
        ss.insert_snapshot(eng, row["item"], row, key_price_ref=64.5, pass_id=pid)
    ss.finish_pass(eng, pid, n_pages=n)
    return pid


def books(con, where="1=1"):
    codec = bc.Codec.from_con(con)
    return {sid: codec.decode(v) for sid, v in
            con.execute(f"SELECT id, book_json FROM snapshots WHERE {where}")}


def kinds(con):
    return dict(con.execute("SELECT typeof(book_json), count(*) FROM snapshots GROUP BY 1").fetchall())


print("=== Codec on its own ===")
empty = bc.Codec()
check("no dictionary -> encode gives None (plain JSON stored)", empty.encode({"buyOrders": []}), None)
check("plain JSON text decodes", empty.decode('{"buyOrders": [1]}'), {"buyOrders": [1]})
check("a NULL book reads as empty", empty.decode(None), {})

tmp = tempfile.mkdtemp()
path = _os.path.join(tmp, "app.db")
eng = ss.connect(path)

print("\n=== bootstrap: plain until a full pass, then one dictionary ===")
write_pass(eng, 1, 60)
check("a thin pass (60 < min_books) trains nothing", bc.ensure_dict(path, min_books=200), None)
con = sqlite3.connect(path)
check("rows are plain JSON before any dictionary", kinds(con), {"text": 60})
p2 = write_pass(eng, 2, 400)
before = books(con)
plain_bytes = con.execute("SELECT sum(length(book_json)) FROM snapshots WHERE pass_id = ?", (p2,)).fetchone()[0]
check("a full pass trains dictionary 1", bc.ensure_dict(path, min_books=200), 1)
check("trained on the full pass", con.execute("SELECT trained_on_pass, n_samples FROM book_dict").fetchone(),
      (p2, 400))
check("every plain row is now a zstd BLOB", kinds(con), {"blob": 460})
check("every book decodes back to exactly what was stored", books(con) == before, True)
blob = con.execute("SELECT book_json FROM snapshots LIMIT 1").fetchone()[0]
check("the frame header names dictionary 1", zstd.get_frame_parameters(blob).dict_id, 1)
z_bytes = con.execute("SELECT sum(length(book_json)) FROM snapshots WHERE pass_id = ?", (p2,)).fetchone()[0]
check("compressed well below the JSON (ratio > 3)", plain_bytes / z_bytes > 3, True)
check("ensure_dict does nothing once a dictionary exists", bc.ensure_dict(path, min_books=200), None)

print("\n=== the writer picks the dictionary up after reset_codec ===")
check("cached codec still has no dictionary", ss.book_codec_for(eng).newest, None)
ss.reset_codec(eng)
check("after reset_codec it encodes with dictionary 1", ss.book_codec_for(eng).newest, 1)
row = app_row(999, 3)
sid = ss.insert_snapshot(eng, row["item"], row, key_price_ref=64.5)
v = con.execute("SELECT book_json FROM snapshots WHERE id = ?", (sid,)).fetchone()[0]
check("a new insert is stored compressed", isinstance(v, bytes), True)
got = bc.Codec.from_con(con).decode(v)
check("and decodes to the listings written (total_keys folded in)",
      ([o["id"] for o in got["buyOrders"]], got["sellOrders"][0]["price"]["total_keys"]),
      ([o["id"] for o in row["buyOrders"]], ss.to_keys(row["sellOrders"][0]["price"], 64.5)))

print("\n=== retraining: new rows use the new id, old rows keep theirs ===")
check("a second dictionary gets id 2", bc.train(con, pass_id=p2, min_books=200), 2)
check("the newest dictionary encodes", bc.Codec.from_con(con).newest, 2)
check("dictionary-1 books still decode", books(con, f"pass_id = {p2}") ==
      {k: v for k, v in before.items() if k in books(con, f"pass_id = {p2}")}, True)
only2 = bc.Codec([(2, con.execute("SELECT dict FROM book_dict WHERE dict_id = 2").fetchone()[0])])
try:
    only2.decode(blob)
    check("a missing dictionary is a loud error", "decoded", "LookupError")
except LookupError:
    check("a missing dictionary is a loud error", True, True)
try:
    bc.Codec([(5, con.execute("SELECT dict FROM book_dict WHERE dict_id = 2").fetchone()[0])])
    check("a book_dict row whose bytes carry another id is refused", "accepted", "ValueError")
except ValueError:
    check("a book_dict row whose bytes carry another id is refused", True, True)

print("\n=== compress_plain: a straggler plain row ===")
con.execute("INSERT INTO snapshots (item, variant, quality, scraped_at, ingested_at, source, book_json) "
            "VALUES ('Straggler', 0, 6, 'x', 'x', 'app', ?)", (json.dumps({"buyOrders": [], "sellOrders": []}),))
con.commit()
check("one plain row compressed", bc.compress_plain(con), 1)
check("none left plain", kinds(con).get("text"), None)

print("\n=== readers ===")
reg = br.connect(path)
n_new, top = br.register_new(reg)
reg.commit()
listed = [o for b in books(con).values() for s in ("buyOrders", "sellOrders") for o in b.get(s, [])]
want = sorted({o["steamId"] for o in listed if o["listingType"] == "bot"})
check("bot_registry finds every bot account inside compressed books",
      sorted(r[0] for r in reg.execute("SELECT steamId FROM bot_account")), want)
check("and no human account", reg.execute(
    "SELECT count(*) FROM bot_account WHERE steamId LIKE 'H000000000000%'").fetchone()[0], 0)
first_id = reg.execute("SELECT first_snapshot_id FROM bot_account WHERE steamId = ?", (want[0],)).fetchone()[0]
check("first_snapshot_id = the first book listing it", first_id,
      min(sid for sid, b in books(con).items()
          for s in ("buyOrders", "sellOrders") for o in b.get(s, []) if o["steamId"] == want[0]))
reg.close()
try:
    engine_book.read_book(blob)
    check("the engine refuses a compressed book (not read as empty)", "read", "TypeError")
except TypeError:
    check("the engine refuses a compressed book (not read as empty)", True, True)

# the engine's db readers decode through panel.book_decoder (2026-10-17: trading_app.db is
# the engine's default db)
from engine.panel import book_decoder, cache_dir_for, load_panel, APP_DB, TRADING_DB, DEFAULT_DB
from engine.bot_groups import load_bot_obs
from engine.bots import account_stats
check("the engine's default db is trading_app.db", DEFAULT_DB, APP_DB)
check("each db has its own cache folder",
      (_os.path.basename(cache_dir_for("app")), _os.path.basename(cache_dir_for("trading")),
       cache_dir_for(path) == cache_dir_for(TRADING_DB)), ("engine_cache_app", "engine_cache", False))
check("book_decoder reads a zstd BLOB", book_decoder(con)(v), bc.Codec.from_con(con).decode(v))
check("... and plain JSON, unreadable text as {}",
      (book_decoder(con)('{"buyOrders": []}'), book_decoder(con)("not json")), ({"buyOrders": []}, {}))
cdir = _os.path.join(tmp, "cache")
pan = load_panel(path, cache_dir=cdir)
check("load_panel reads every compressed Unique/Strange book",
      len(pan) > 0 and pan.n_buy.sum() > 0, True)
obs = load_bot_obs(path, cache_dir=cdir)
check("load_bot_obs reads the listings inside compressed books", len(obs) > 0, True)
acct = account_stats(path, cache_dir=cdir)
check("account_stats counts accounts inside compressed books (no SQL json_each)",
      set(acct.steamId) >= set(want), True)

out = io.StringIO()
with contextlib.redirect_stdout(out):
    bc.main(["--db", path, "show", str(sid)])
check("show prints the book as JSON", json.loads(out.getvalue()), bc.Codec.from_con(con).decode(v))
con.close()

plain_db = _os.path.join(tmp, "plain.db")
pc = sqlite3.connect(plain_db)
pc.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, book_json TEXT)")
pc.execute("INSERT INTO snapshots (book_json) VALUES ('{}')")
pc.commit()
with contextlib.redirect_stdout(io.StringIO()):
    bc.main(["--db", plain_db, "stats"])
check("stats on a DB without a dictionary adds no table",
      pc.execute("SELECT count(*) FROM sqlite_master WHERE name = 'book_dict'").fetchone()[0], 0)
pc.close()
eng.dispose()

print()
if fails:
    print(f"FAILED ({len(fails)}): {fails}")
    sys.exit(1)
print("all offline checks passed")

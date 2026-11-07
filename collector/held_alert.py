"""Take-profit alerts on the held-item watch (2026-10-18, trading/handoff.md Phase 2.4).

After each watch tick, a watched entry that carries its entry price (`entry_keys`, the
ask paid) is checked against the forecast's own exit rule (trading/backpack/engine/
forecast.py): sell the first time a fillable bid is >= entry x (1 + TAKE_PROFIT), and a
bid above MAX_JUMP x the entry is a misread, never a sale. The bid is read with the
engine's rules (`engine_book`, a byte-for-byte copy of trading's engine/book.py), and
only from an account the agent may trade with: fenced, not UNTRUSTED (`bid1_fill`'s
rule), `listingType == bot` and `dealType == instant` (handoff Safety).

An alert says "go try", not "sold": the bid was there at the tick. It fires once when
the condition starts holding and re-arms when it stops (state in memory: a restart
alerts again if it still holds). Sent to a Discord webhook whose URL lives only on the
VM, in WEBHOOK_FILE (never in the repo, never in a log); no file = logged only.
"""
import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass

import goods
import engine_book as eb

# The forecast's exit rule (trading/backpack/engine/forecast.py; trading's test_sync-style
# check pins these to it).
TAKE_PROFIT = 0.04
MAX_JUMP = 3.0

WEBHOOK_FILE = os.path.expanduser("~/.config/bptf-collector/discord_webhook")
QUALITY_NAME = {6: "Unique", 11: "Strange"}


@dataclass(frozen=True)
class Hit:
    item: str
    quality: int
    entry: float          # keys paid
    bid: float            # the tradable bid, keys
    who: str              # the bidder's name
    steam_id: str
    trade_url: str        # None when the account has none stored
    bid1_fill: float      # the engine's own read-out, for comparison
    scraped_at: str

    @property
    def ret(self):
        return self.bid / self.entry - 1


def tradable_bid(book):
    """The best fenced bid an agent may sell into: (keys, listing) or None."""
    fenced = eb.fence_buys(eb.valid_buys(book))
    for p, o in fenced or []:
        if (str(o.get("steamId")) not in eb.UNSTABLE_FILLS and o.get("listingType") == "bot"
                and o.get("dealType") == "instant"):
            return p, o
    return None


def check(entry, book, scraped_at="", trade_url_for=lambda sid: None):
    """A Hit if this book meets the take-profit for this watched entry, else None.
    `book` carries total_keys (as stored)."""
    paid = entry.get("entry_keys")
    if not paid or paid <= 0:
        return None
    got = tradable_bid(book)
    if got is None:
        return None
    p, o = got
    if p < paid * (1 + TAKE_PROFIT) or p > paid * MAX_JUMP:
        return None
    return Hit(item=entry["item"], quality=entry["quality"], entry=paid, bid=p,
               who=str(o.get("user") or "?"),
               steam_id=str(o.get("steamId")), trade_url=trade_url_for(o.get("steamId")),
               bid1_fill=eb.read_book(book).bid1_fill, scraped_at=scraped_at)


def message(hit):
    q = QUALITY_NAME.get(hit.quality, f"q{hit.quality}")
    lines = [f"**Take profit? {hit.item} ({q})**",
             f"bid {hit.bid:.4g} keys from {hit.who} (bot, instant) -- "
             f"{hit.ret:+.1%} on the {hit.entry:.4g}-key entry",
             f"seen {hit.scraped_at[:16].replace('T', ' ')} UTC; go try, not sold",
             f"<{goods.app_url(hit.item, hit.quality)}>"]
    if hit.trade_url:
        lines.append(f"trade: <{hit.trade_url}>")
    return "\n".join(lines)


def _webhook_url(path=WEBHOOK_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            url = f.read().strip()
    except OSError:
        return None
    return url if url.startswith("https://") else None


def _post(url, text, timeout=10.0):
    req = urllib.request.Request(
        url, data=json.dumps({"content": text}).encode(), method="POST",
        # Discord's edge refuses urllib's default agent
        headers={"Content-Type": "application/json", "User-Agent": "bptf-collector (watch alert)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


class Alerter:
    """Edge-triggered: one alert when an entry's take-profit starts holding."""

    def __init__(self, webhook_file=WEBHOOK_FILE, post=_post, log=print):
        self.webhook_file, self.post, self.log = webhook_file, post, log
        self.firing = set()              # (item, quality, entry_keys) currently over the bar

    async def observe(self, entry, hit):
        """Feed one tick's verdict for one entry (hit None = not over the bar)."""
        key = (entry["item"], entry["quality"], entry.get("entry_keys"))
        if hit is None:
            self.firing.discard(key)
            return False
        if key in self.firing:
            return False
        self.firing.add(key)
        text = message(hit)
        self.log(f"[alert] {hit.item} q{hit.quality}: bid {hit.bid:.4g} vs entry "
                 f"{hit.entry:.4g} ({hit.ret:+.1%}) from {hit.who}")
        url = _webhook_url(self.webhook_file)
        if url is None:
            self.log("[alert] no webhook file -- logged only")
            return True
        try:
            await asyncio.to_thread(self.post, url, text)
        except Exception as e:           # never let an alert take the watch down
            self.log(f"[alert] webhook failed: {type(e).__name__} -- retried next tick")  # not the URL
            self.firing.discard(key)
        return True


def main(argv=None):
    """`python held_alert.py test` -- post one test message to the webhook in WEBHOOK_FILE."""
    import argparse
    p = argparse.ArgumentParser(description="take-profit alerts")
    p.add_argument("cmd", choices=["test"])
    p.add_argument("--webhook-file", default=WEBHOOK_FILE)
    a = p.parse_args(argv)
    url = _webhook_url(a.webhook_file)
    if url is None:
        print(f"no https URL in {a.webhook_file}")
        return 1
    try:
        status = _post(url, "bptf-collector: test alert (the watch's take-profit alerts land here)")
    except Exception as e:
        print(f"post failed: {type(e).__name__}: {getattr(e, 'code', '')}")
        return 1
    print(f"posted (HTTP {status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# agent

The Stage 6 taker agent (`handoff.md` §0), in Node on the user's PC. Python (`backpack/engine`)
decides -- what to buy (`engine picks`, `forecast.AGENT_RULE`: `p_up >= 0.65`, 0.1-2 keys, 2026-10-30), whether
a listing is still good (`engine recheck`), when to sell (`engine exits`); Node asks the user,
builds and sends the Steam side. The paper test is the replay on stored books
(`python -m engine replay`); the old paper agent is `docs/extra/paper-agent.md`.

**Day to day: `agent.cmd`** -- one command from any folder (once the agent folder is on PATH):
`agent status` (running? mode, kill switch, holdings, the last cycle; `status.js`, read-only),
`agent start` / `agent start manual`, `agent stop` / `agent go` (the kill switch),
`agent resume`, `agent report [--all]`, `agent trades` (`trades.js`: held with the latest bid,
every sale, buys that never filled), `agent history <item>` (`history.js` over `engine history`: one item's top buy order, lowest sell and tradable bid over time -- now, 1 h / 24 h / 7 / 30 days ago, the last moves, by day -- and a chart with what the agent paid and its take-profit; `--strange`, `--days N`), `agent dashboard` (`dashboard.js`: profit over time, return
per sale, held items against their take-profit and horizon, every trade -- one offline page,
`backpack/logs/dashboard.html`, rebuilt on each run), `agent log [n]`, `agent confirm`,
`agent code`, `agent limits`, and (2026-10-30) the levers the panel's Agent tab also pulls:
`agent set <limit> <value>` (`set.js`, checked before it replaces limits.json), `agent restart` /
`agent shutdown` / `agent cycle` (`control.js`: a request file the running agent takes **between**
cycles -- restart exits with code 75 and `run.cmd` / `run-auto.cmd` start it again in the same
window; an agent started before this change ignores them until it is restarted once by hand),
`agent sell <item|id>` / `agent tp <item|id> <pct>` / `agent horizon <item|id> <hours|reset>`
(`position.js`: the ledger's take-profit / horizon for a held item, every change in
`position_events`; a sell-now closes as exit `manual`), `agent panel` (the live page,
`python -m engine panel`; `panel.js` is the JSON it reads). limits.json is re-read at the
start of every cycle (a broken file keeps the last good one). `agent` alone lists them. Both views join the ledger with the log
(`lib/trades.js`): the bots' names and the latest bids are only in the log.

**Win rate** = sales that made a profit (`pnl_keys > 0`), whatever the exit: a horizon sale
above the price paid is a win, a take-profit can't lose.

```
npm install
node setup_2fa.js <account>   # once: turn the authenticator on
run.cmd                       # double-click: the live agent, every 600 s, until the window closes
node run.js [--once]          # the same from a terminal; VM settings from %USERPROFILE%/.steam-bot/agent.json
run-auto.cmd                  # double-click: AUTO mode (run.js --auto) -- no approvals, limits.json is the only gate
node report.js [--all]        # the results so far (backpack/agent.db)
node stop.js [off|status]     # the kill switch: nothing is sent while it is on
node stop.js resume           # after the win-rate stop: buy again, counting only new sales
node code.js                  # the login code Steam asks for at every login
node confirm.js               # accept / cancel pending confirmations by hand
node dry_buy.js listing.json  # build (never send) a buy offer from the real inventory
node --test                   # the tests (test/)
```

**The live agent** (`run.js`, `lib/cycle.js`). At start it logs in with the saved login
(`lib/session.js`: the password the first time only). Each cycle: pull the VM; **incoming
offers declined** (every offer another account sent us, logged with who and how many items;
the agent never accepts one, and a failure here only logs); **the restart
check** (`lib/reconcile.js`: every offer still open in the ledger is read back from Steam,
so an offer that filled while the agent was off is recorded, never repeated); **exits**
(`engine exits`, fresh books); **the key<->metal rebalance** (`lib/convert.js`, 2026-10-30:
the metal is kept between `key_band_low` (0.5) keys under and `key_band_high` (1.5) keys over
what buys may still spend -- `max_keys_in_play` minus the held positions' keys, at most
`max_metal_keys` (3); 0 while the win-rate stop holds buys -- by selling
keys into tradable key bids or buying keys from tradable key sells, metal-only listings on a
fresh key book (`engine recheck --key-book`: at most 3 scrap off the other side's best), at
most `max_conversions` (3) a cycle, each approved like a trade; a sale always leaves
ceil(`max_trade_keys`) - 1 keys (a pick under 2 keys may be listed as 1 key + ref); a key
book both sides of which are more than 2% off bp.tf's key price is no trade; a failed key
fetch skips only the rebalance; logged in the ledger's `conversions`, never a position; no
bands in limits.json = no conversion. What an accepted offer or conversion gave stays
reserved for 30 min, so a lagging inventory read never pays with it again); then, on a new
pass, **buys**. Every trade:

1. re-checked on a fresh book (`engine recheck`);
2. **you approve it** in the window: `y` within 15 min, else it is skipped (and the rest of
   that cycle's trades, since you are away);
3. re-checked again (the approval may have taken minutes);
4. settled (`currency.settleToward`, user 2026-10-30): a buy or a sale picks the split of
   refined / reclaimed / scrap -- change either way, at most 2 ref of it -- that leaves our
   reclaimed and scrap nearest `target_rec` / `target_scrap` in limits.json (15 / 10), so the
   small pieces hold steady and refined absorbs the rest. Without a target, the old rule
   (`currency.settle`): a buy pays in refined rounded up with the seller's change back, a
   sale takes the buyer's exact pieces. Change is logged;
5. sent (`lib/send.js`): Steam's trade-hold days checked first (ours > 0 = the kill switch
   goes on; theirs > 0 = the next bot), confirmed with the identity_secret, then the bot has
   30 s. No answer: cancel, read it again (a bot that accepted as we canceled is a fill), and
   try the next bot in range (buys at or below the model's ask; take-profit sales still +4%;
   horizon sales within 2% of the best bid), at most 3.

A confirmation that errors is canceled and read back like a timeout (it may have gone through):
an accept is a fill, an offer still open stops the cycle for the restart check. One agent per
account (`lib/lock.js`, `<account>.lock` in the secrets folder): a second `run.cmd` /
`run-auto.cmd` refuses to start; a lock whose process is gone is taken over.

Stop it any way; while it is off nothing is bought or sold. The ledger is
`backpack/agent.db`, the log `backpack/logs/agent.log`.

**Limits** (`limits.json`, `lib/limits.js`): keys in play (2 for the first run), one trade's
keys (2 since 2026-10-30), buys a pass, the metal target (`target_rec` / `target_scrap`), the
key<->metal band (`key_band_low` / `key_band_high` / `max_conversions`), and the win-rate stop --
fewer than 75% of the last 20 sales won stops buying (`BUYS_PAUSED` in the secrets folder)
until `node stop.js resume`. Sales are stopped only by the kill switch.

**The offers** (`lib/offers.js`, `lib/currency.js`): every asset is appid 440 / context 2, so
an offer can only name TF2 items. A buy requests the listing's asset (`440_<assetid>`) only
after `assetCheck` finds it in the seller's TF2 inventory, tradable, under the pick's exact
Steam name (`Strange <item>` for Strange; `<item>` or `The <item>` for Unique; measured
2026-10-28; Killstreak / Festivized / any other quality fail; an unreadable inventory = no
buy), and gives refined / reclaimed / scrap; a sale gives our item and takes the
buyer's metal (its public inventory). A price is keys + ref with the ref part n x .11 per
scrap; a price metal can't pay exactly is refused.

**The ledger** (`lib/ledger.js`): positions (opening / open / closing / closed / abandoned)
and every offer with each state it went through, timed (`sent` -> `active` -> `accepted` is
the fill time); several offers per position when the next bot is tried. Schema:
`docs/data-model.md` "The agent's ledger".

**Steam** (`lib/steam.js`): `steam-tradeoffer-manager` on the login's access token (no Web API
key needed), its own polling off.

**Secrets** live in `%USERPROFILE%\.steam-bot\` (override: `STEAM_BOT_DIR`), never in the repo
or a log: `<account>.maFile.json` (the whole `AddAuthenticator` response: `shared_secret` =
login codes, `identity_secret` = confirmations, `revocation_code`; plus `account_name`,
`steamid`, `enabled_at`, `finalized_at`) and `<account>.token.json` (the saved login: anyone
who copies it can trade from the account until Steam's "Deauthorize all devices").
`setup_2fa.js` writes the authenticator file before the activation step and never overwrites
one. **Back up** the authenticator file and write down the revocation code: losing both
leaves only Steam Support.

**Steam rules (2026-10-19):** with no phone on the account the activation code comes by
email. For the first 7 days after activation, trades can be held up to 15 days (cleared
2026-10-26 12:27 UTC).

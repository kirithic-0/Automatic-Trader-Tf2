# Bots — registry, behaviour groups, per-bot features

> Split out of `data-model.md` on 2026-11-07, unchanged. Grep `^#` and read the section you need.

## Bot registry — the static part (2026-09-24)

Who each bot account *is*, as raw facts. Written by `backpack/bot_registry.py`
(`sync`, `mark`); the provider **rule** is read-side, `engine/bots.py`
(`registry()`). Append-only:

- **`bot_account`** `(steamId PK, first_snapshot_id, first_seen_at, added_at)` —
  every account that has carried bp.tf's bot badge (`listingType == "bot"`) on any
  listing, **any quality**. Only badged accounts are registered or markable (a
  user decision). 523 on 2026-09-24 (462 counting Unique+Strange only).
- **`bot_group_member`** `(steamId, grp, first_seen_at)` — a registered account
  found in a pulled Steam group roster (`gladiatortfbots`, `merchanttfbots`,
  `TF2Autobot`, `qsstore@moderators`, and ten small fleet groups — see "Fleets"
  below). `<group>@<rank>` = only members holding that
  rank, read from the group's HTML member page (the XML roster has no ranks). Only registered accounts are stored, not the whole roster.
  **Permanent once seen**: dropping off a roster later changes nothing.
- **`steam_profile`** `(steamId, fetched_at, status, error, name, privacy,
  member_since, trade_ban, is_limited, vac_banned, groups_json, summary, raw_xml)`
  — one row per fetch of the public profile XML. Fetched once per account;
  `sync --refresh-profiles` adds a new row, never overwrites.
- **`bot_label`** `(steamId, provider, set_at, note)` — manual marks; latest wins.
  Closed set = `bot_registry.PROVIDERS` (the big providers, the 2026-09-25 fleets
  below, plus `other_bot, human, unknown`);
  `mark --new` adds a name.
- **`bot_sync`** — one row per sync: snapshot watermark (incremental scan) and
  roster/profile counts.

**Provider rule** (engine/bots.py): manual mark > dedicated bot roster
(`gladiatortfbots` → gladiator, `merchanttfbots` → merchanttf,
`qsstore@moderators` → quicksell; two different ones
→ `unknown` + conflict) > `TF2Autobot` group (a 10k-member community group — counts
only with the badge, which every registered account has) > `unknown`. A `human`
mark removes the account from the bot set. Provider is treated as static
identity: today's label applies to all history.

Why these groups (measured 2026-09-24 against the 462 Unique+Strange badged
accounts): `gladiatortfbots` 305 members, 154 seen, **0 never-badged** (clean);
`merchanttfbots` 7 / 6 / 0 (clean); `TF2Autobot` 10,859 / 127 / **75
never-badged** (community group, hence badge-gated); `tf2automatic` 16,796 / 24 /
58 (noisy — not used; what tf2automatic is, is not in the KB). Rosters together
covered 289 of 462 badged accounts, 74% of their listings.

First sync (2026-09-24, all qualities): **523 registered → gladiator 190,
tf2autobot 142, merchanttf 6, unknown 185**; 523 profiles, 0 errors. The busiest
unknowns are visibly *fleets* — numbered names and a shared summary or group
(`N ScrapyardBot`, Quicksell.store bots with group `qsstore`, `HexTrade`, CTradeTF
"DOLPHIN", CobraTF) — candidates for manual marks or new rosters; not decided.

Marked by hand 2026-09-25 (user decision): **scrapyard** = the 6 `N ScrapyardBot`
accounts (numbered names, no group); **quicksell** = 12 accounts — 11 with the
"Official Quicksell.store … Bot" profile summary, plus Harry (in `qsstore`, same
first-name pattern, empty summary). `qsstore` as a whole is *not* a roster (25,252
members, a community group) — but its **ranks** are: 1 Group Owner (zol, the human
who runs it — excluded) + **20 Group Moderators = the Quicksell.store bots**
(user-confirmed 2026-09-25). 12 moderators are registered (exactly the 12 marked by
hand, Harry included); 8 have never posted a badged listing (Henry, Key Bot 1–4,
Sebastian, Jack, Benjamin) and get `quicksell` automatically once they do. Added as
the roster `qsstore@moderators`. Summaries saying "Owner buys quicksells" (5 gladiator bots,
"haunted") are a different thing and were left alone.

**Fleets (user decision 2026-09-25).** The unknowns were sorted into fleets by
shared name pattern, profile summary and Steam group (candidate list with every
profile link went to the user; weak "theme only" clusters and the 7 unknowns in the
1,671-member `gladiatortf` community group were left `unknown`).

- *Own small group → roster* (roster-enough; 4–31 members each): `itemfortbots` →
  itemfort, `elsestore` ("TradeMann - Auto Trading", a class-hats fleet) → trademann,
  `tf2autobot-upgrade` → polishbot (PolishBot 4/5/6 + Kuina Bot 7), `TF2Syntax` →
  tf2syntax, `SafiStores` → safi, `tweakmaxing` → japantrip, `sentrytf` → sentrytf
  (profiles call it a bot *service*), `sawtradeshop` → superanimalworld, `bravobot`
  → bravobot, `Gn1dor43_TF2` → gn1dor43. Pulling them moved 8 accounts off
  `tf2autobot` (7 TradeMann class-hat bots, Nathan → sentrytf): a dedicated roster
  outranks the community group. No conflicts; nothing moved off gladiator.
- *Name + identical summary, no usable group → manual marks*: ctradetf (5,
  "Main: Brom127"), junkertf (4), paintedhats (4, "NAME 🎨 PAINTED HATS 24/7"),
  clone (3), gradle (4), sfuminator (3; its group is 5,691, community), hex (2;
  HexTrade is 634), ozenreg (Ozen! + Reg!, the two busiest unknowns), cobratf (3);
  plus 7 ItemFort bots not in `itemfortbots`.

ItemFort bots post most listings **without** the bot badge (badge share ~0.3–0.5,
vs ~1.0 for every other fleet) — why is not in the KB.

After this: 529 registered → gladiator 190, tf2autobot 136, **unknown 82**,
itemfort 30, trademann 12, quicksell 12, then 18 smaller fleets.

### Does a group behave as one? (measured 2026-09-25)

**Same price when co-listed.** Last 48 passes, Unique+Strange, raw valid listings
(pre-fence): for two registered bots on the same book side in the same pass, the
share quoting the same price (within 0.2%). Baseline, bots of *different* groups:
**57.9%** (2.7M pairs) — high because nearly every bot bids at the book median
(typical bot bid/median = 1.000). Single-operator fleets act as one: scrapyard
98.8%, quicksell 96.9%, hex 99.0%, merchanttf 99.9%, ctradetf 98.6%, polishbot
100%, trademann 99.1%, itemfort 100%, ozenreg 93.4%. **gladiator 74.0%** (1.2M
pairs) and tf2autobot 68.0% do **not**: of 1,950 gladiator pairs with ≥30
co-listings only 14% agree ≥95% of the time — about the spread of cross-group
pairs. **tf2syntax 14.2%** (TF2Syntax vs TF2Syntax #3: 0 of 48). Linking accounts
that agree ≥95% chains 235 accounts of every provider into one component, so
price agreement alone cannot *find* groups (median-following makes all bots look
alike).

**Gladiator plans.** gladiator.tf sells two plans (https://gladiator.tf/rent,
2026-09-25): **Basic** 6 keys/mo — "Manual pricing, Automatic offer accepting";
**Premium** 12 keys/mo — "Automatic Pricing, Undercutting and overcutting" (plus
autopricing, flexible manual listings, classified under/overcutting). **No public
source maps a bot to its plan**: `https://gladiator.tf/api/bots` (public, what the
Instant Trade userscript uses) returns only `{"success":1,"bots":[302 steamID64s]}`;
the Time Machine needs a login; the owner-script and instant-trade repos and
guide.tf say nothing about plans. The API list vs the Steam roster: 292 in both,
10 API-only, 13 group-only; 7 registered bots are gladiator by the API but not in
the group. The site's own `js/main.js` keeps the plan per bot as
`bot.tier` (pricing table `{0: 6 keys, 1: 12 keys}` → **0 = Basic, 1 = Premium**);
tier 0 forces `manualBuy`/`manualSell` and hides autoprice — but `bot.tier` is only
served behind the owner login (`/api/v2/manage/<id>/…` → 401; `/api/bots/<id>`,
`/api/v2/bots` → 404; `/api/bots?plan=1` ignores the parameter). **Every gladiator
bot observed carries the backpack.tf premium flag**; in main.js bp.tf premium is a
separate paid add-on (`/:manage/backpack/premium`, flag `bot.isBpPremium`) with no
tier check, so that flag says nothing about the plan.

**Inferring the plan from behaviour.** A Basic bot cannot autoprice, so its prices
should not move when the book does. All passes, per bot and (item, side), each
pair of consecutive observations: did the book median move (>0.5%) and did the
bot's price move (>0.2%)? Of the 93 gladiator bots with ≥30 median moves and ≥10
items, **82 reprice in ≥2% of transitions, 7 in <1%** (Skinny Bot: 104 items,
median moved 1,364×, bot moved 0.07%; ⚡MOLFAR⚡, Panda Bot [2], a1bot, SCRAP
VACUUM, Philibert BOT, Quinlan's Trade-Clanker), 4 in between; 51 have too little
data. The static 7 are **Basic candidates, not confirmed** — Premium also allows
manual listings, so "never moves" is necessary for Basic, not sufficient. The same
test finds ~14 static tf2autobot bots and shows itemfort as static (moves <1%,
tracking ≈0) while quicksell/scrapyard track the book (0.5–0.8).

**Overcut step.** When a bot holds the top of a book side, the gap to #2 (both
quoted in the same key count, exact ref): buys 86.1% tie, **7.1% at 0.11 ref**;
sells 70.5% tie, **16.6% at 0.11 ref** (plus 0.12 from rounding) — one scrap is the
overcut/undercut step. Per gladiator bot the one-scrap share runs continuously
0–0.3 (grum 0.58), so it does not split the plans on its own.

**Not captured, could help:** the classifieds parser (`bptf_scrape.py`
`_parse_listing`) drops `data-listing_comment` (the listing description) and the
buyout/offers flags even though the whole `li.listing` element reaches Python — a
parser-only change would keep them.

**tf2autobot** (user, 2026-09-25): the open-source tf2autobot has **no built-in
automatic under/overcutting** — a tf2autobot bot that cuts has been modified by its
owner. (18 tf2autobot bots hold the top by exactly one scrap in ≥30% of their
top-of-book moments.)

### The group key (user decision 2026-09-25)

Code: `engine/bot_groups.py` (interface in code-base.md). A bot's **behaviour
group** is its **operator fleet** when it has one (a provider run by one owner),
else **provider × pricing mode** — gladiator, tf2autobot, unknown and other_bot are
services/software whose every bot is configured by its own owner, and fleets that
fail the fleet check (`NOT_ONE_OPERATOR`: tf2syntax) join them. **Pricing mode** is
point-in-time over a **7-day** window: `static` = reprices in <1% of transitions
over ≥30 book moves (a Basic candidate for gladiator), `repricing` = more, `thin` =
fewer than 30 book moves (it will inherit its group's behaviour). Two modes, not
three: only static vs repricing has a gap in the data; the one-scrap cut rate stays
a continuous feature. Window: 72 h left 109 of 190 gladiator bots thin, 7 days 95,
all history 92 (11 days of data). Each account keeps its own features; the group is
what a thin account is pulled toward.

A second gladiator roster, **`api:gladiator.tf`** = `https://gladiator.tf/api/bots`
(gladiator's own list), joins the Steam group: first pull 302 bots, 197 registered,
including the 7 the Steam group missed (Epic Flintstones, Saul Goodman, KikonKi,
Fox (Trade Bot 3), Midas TF2², VFR800, BotBot Binks — before, unknown). One plain
request, no Steam rate limit. After a Steam roster 429s past the whole backoff,
`sync` skips the remaining Steam rosters (2026-09-25: five 429s in a row at ~4 min
each) and retries them next sync.

### Per-bot behaviour features — the dynamic part (built 2026-09-25)

Code: `engine/bot_features.py` (interface in code-base.md). Every account seen on a
Unique/Strange book (registered bot or not) gets, at every pass it quotes, rates that
are **time-decayed** (48 h half-life) and **shrunk toward its behaviour group**:
`rate = (n_event + k · group_rate) / (n_base + k)`, **k = 20** pseudo-observations —
so a `thin` bot inherits its group. The group is `groups_asof` = the behaviour group
as of the last UTC midnight before the pass (pricing mode from data up to then);
unregistered accounts form `nonbot`. The one input that is not point-in-time is the
registry itself (who is a bot, and its provider): today's, applied to all history, as
the provider rule already does. The rates, all from `load_bot_obs` (pre-fence):

- `prem_b / prem_s` — how far it quotes from the side median (the overbidder);
- `top_b / top_s` — share of its listings at rank 1;
- `cut1_b / cut1_s` — of its rank-1 listings, the share exactly one scrap ahead of #2;
- `raise_b, lower_b, raise_s, lower_s` — of its transitions (it quoted the same book
  side at the book's previous pass), the share moving > 0.2% up / down;
- `new_b` — share of its buy listings that are new on that book;
- `lead_b` — of its buy raises made while the book median had not moved, the share
  the median followed (+0.5% within 6 h); counted only once the 6 h have passed.

They reach the forecast as **per-book aggregates** (`bot_b_*` buys, `bot_s_*` sells):
how many listed accounts raised / lowered / arrived this pass (and over 6 h / 24 h),
the mean and max of the listed accounts' rates, the provider mix, and the rank-1
account's own rates. Values seen 2026-09-25 (132,934 account-passes, 59% of them
unregistered accounts): median `raise_b` 0.068, `lower_b` 0.020, `cut1_b` 0.122,
`lead_b` 0.55, `top_b` 0.34, `prem_b` +0.042. Measured value in
the Stage-5 model (see "Buy-level forecast" below): the bot aggregates lift
precision@10 from 36% to 42% and the portfolio from +0.47% to +0.62% per trade (4/5 →
5/5 positive days) — the largest gain of any feature group added. The fence's `trust` does **not** use them
yet (handoff step 3 is still the user's design call).


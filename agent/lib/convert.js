// Stage 6 -- the key<->metal rebalance (user, 2026-10-30): the account holds keys as its store
// and metal to trade with. Once a cycle, after the sales and before the buys, the metal is
// brought back inside a band around what the buys can still spend:
//
//   need  = max_keys_in_play - the keys held positions cost (what buys may still spend), at
//           most max_metal_keys (3, user 2026-10-30: the keys are the store, not all of the
//           room is held as metal; a pick of 1 key or more pays its whole keys in keys)
//   metal = our refined + reclaimed + scrap, in keys at the key's mid price now
//   metal < need - key_band_low  -> sell keys into tradable bids, up to need (whole keys)
//   metal > need + key_band_high -> buy keys from tradable sells with the extra metal
//
// key_band_low / key_band_high (0.5 / 1.5 keys) are optional limits: without them nothing is
// converted. At most max_conversions (default 3) keys a cycle. A sale always leaves
// ceil(max_trade_keys) - 1 keys (1 at 2): a pick under 2 keys may be listed as 1 key + ref,
// which metal cannot pay. The band's gap keeps it from
// flipping one key back and forth; the order (sales -> rebalance -> buys) is fixed, so no trade
// waits on another: a conversion that does not fill just leaves fewer buys this cycle.
//
// Each conversion is decided on a fresh key book (`engine recheck --key-book`: metal-only
// listings, a buy at most 3 scrap over the best bid or the cheapest sell, whichever is higher;
// a sale at most 3 scrap under either, whichever is lower), settled like any trade
// (currency.settleToward: the metal target holds), and sent by lib/send.js's attempt() -- the
// best bot, then the next, every bot in that window (user, 2026-11-01: not max_tries; the
// cheapest key sellers ignored three cycles of offers, the ones a scrap above went untried). It is logged in
// the ledger's conversions table: not a position, never in the win rate or the keys in play.
const {buyOffer, sellOffer, assetCheck, metalTarget} = require('./offers');
const {attempt} = require('./send');
const {SCRAP} = require('./currency');
const limits = require('./limits');

const KEY = 'Mann Co. Supply Crate Key';

const metalScrap = s => s.ref.length * SCRAP.ref + s.rec.length * SCRAP.rec + s.scrap.length * SCRAP.scrap;

// What to convert now: {kind: 'sell_key' | 'buy_key' | null, n, metal (keys), need}.
// `keep` = keys a sale never goes below.
function plan({metal, keys, keyScrap, need, low, high, max = 3, keep = 0}) {
	if (!(keyScrap > 0)) return {kind: null, n: 0, metal: NaN, need};
	let m = metal / keyScrap, n = 0, kind = null;
	if (m < need - low) [kind, n] = ['sell_key', Math.min(Math.ceil(need - m), keys - keep, max)];
	else if (m > need + high) [kind, n] = ['buy_key', Math.min(Math.floor(m - need), max)];
	return n > 0 ? {kind, n, metal: m, need} : {kind: null, n: 0, metal: m, need};
}

// The limits' metal room: what buys may still spend, at most max_metal_keys.
function needKeys(cfg, heldKeys) {
	let room = Math.max(0, cfg.max_keys_in_play - heldKeys);
	return cfg.max_metal_keys == null ? room : Math.min(room, cfg.max_metal_keys);
}

// The key's fresh book, or {ok: false, reason} -- a failed fetch only skips the rebalance,
// never the cycle's buys.
async function keyBook(deps) {
	try {
		let r = await deps.engine('recheck', ['--key-book', '--target', deps.target]);
		return r.data || {ok: false, reason: 'no key book'};
	} catch (err) {
		return {ok: false, reason: `key book: ${err.message.slice(0, 200)}`};
	}
}

// One key bought or sold: {filled, reasons}. `ours` = our stock now.
async function convertOne(deps, kind, book, ours, theirInventory) {
	let target = metalTarget(deps.cfg), reasons = [];
	let view = {...deps, ledger: deps.ledger.conversionView()};
	for (let l of book[kind === 'buy_key' ? 'buy' : 'sell'] || []) {
		let t = await theirInventory(deps, l.steam_id);
		let res;
		if (kind === 'buy_key') {
			let bad = assetCheck(l, t.items, KEY, 6);
			res = bad ? {ok: false, reason: bad} : buyOffer(l, ours, t.stock, target);
		} else {
			res = sellOffer({asset_id: ours.key[0]}, l, t.stock, ours, target);
		}
		if (!res.ok) { reasons.push(`${l.user}: ${res.reason}`); continue; }
		let id = deps.ledger.addConversion({kind, offer: res.offer, watchId: book.watch_id ?? null});
		if (res.offer.change_scrap) {
			deps.log(`    change: ${res.offer.change_scrap} scrap ${kind === 'buy_key' ? 'comes back from' : 'of ours goes to'} ${l.user}`);
		}
		let r = await attempt(view, 'convert', id, res.offer, l, {});
		if (r.filled) return {filled: true, listing: l, offer: res.offer};
		deps.log(`    ${r.next}`);
		reasons.push(r.next);
	}
	return {filled: false, reasons};
}

// The cycle's rebalance step. helpers = {ourStock, theirInventory, ask} (lib/cycle.js).
// Returns the keys converted.
async function rebalance(deps, state, {ourStock, theirInventory, ask}) {
	let cfg = deps.cfg;
	if (cfg.key_band_low == null || cfg.key_band_high == null) return 0;
	let done = 0, spent = new Set();
	let book = await keyBook(deps);
	if (!book || !book.ok) { deps.log(`  keys: no rebalance -- ${book ? book.reason : 'no key book'}`); return 0; }
	let ours = await ourStock(deps);
	// buys stopped by the win-rate stop: nothing to hold metal for
	let paused = !limits.winRate({ledger: deps.ledger, mode: 'live', cfg, resumedFile: deps.resumedFile}).ok;
	let p = plan({metal: metalScrap(ours), keys: ours.key.length, keyScrap: Math.round(book.key_ref * 9),
		need: paused ? 0 : needKeys(cfg, deps.ledger.exposure('live').keys), low: cfg.key_band_low, high: cfg.key_band_high,
		max: cfg.max_conversions ?? 3, keep: Math.max(0, Math.ceil(cfg.max_trade_keys) - 1)});
	let where = `metal ${p.metal.toFixed(2)} keys, ${ours.key.length} keys, ${paused ? 'buys paused' : `metal wanted ${p.need.toFixed(2)}`}`;
	if (!p.kind) { deps.log(`  keys: in the band (${where})`); return 0; }
	deps.log(`  keys: ${p.kind === 'sell_key' ? 'sell' : 'buy'} ${p.n} (${where}; key ${book.ask_ref} / ${book.bid_ref} ref)`);
	for (let i = 0; i < p.n; i++) {
		let stop = limits.stopped(deps.stopFiles);
		if (stop) { deps.log(`  keys: ${stop}`); break; }
		if (i) {
			book = await keyBook(deps);
			if (!book || !book.ok) { deps.log(`  keys: stopped -- ${book ? book.reason : 'no key book'}`); break; }
			ours = await ourStock(deps);
		}
		// what we already gave this cycle may still show in a lagging inventory
		for (let k of Object.keys(ours)) ours[k] = ours[k].filter(id => !spent.has(id));
		if (p.kind === 'sell_key' && ours.key.length <= Math.max(0, Math.ceil(cfg.max_trade_keys) - 1)) {
			deps.log('  keys: no key left to sell');
			break;
		}
		let best = book[p.kind === 'buy_key' ? 'buy' : 'sell'][0];
		if (!best) { deps.log(`  keys: no tradable ${p.kind === 'buy_key' ? 'sell' : 'bid'} in range`); break; }
		let q = p.kind === 'sell_key' ? `SELL 1 key to ${best.user} for ${best.ref} ref` : `BUY 1 key from ${best.user} for ${best.ref} ref`;
		if (!(await ask(deps, state, `${q} (rebalance: ${where})`))) { deps.log('  keys: not approved'); break; }
		let r = await convertOne(deps, p.kind, book, ours, theirInventory);
		if (!r.filled) { deps.log(`  keys: no fill -- ${r.reasons.join('; ')}`); break; }
		for (let a of r.offer.give) spent.add(String(a.assetid));
		deps.log(`  KEY ${p.kind === 'sell_key' ? 'SOLD' : 'BOUGHT'} for ${r.listing.ref} ref with ${r.listing.user}`);
		done++;
	}
	return done;
}

module.exports = {KEY, plan, needKeys, metalScrap, rebalance, convertOne};

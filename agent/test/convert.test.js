// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {Ledger} = require('../lib/ledger');
const {cycle} = require('../lib/cycle');
const {plan, needKeys, KEY} = require('../lib/convert');
const {reconcile} = require('../lib/reconcile');

const KS = 582;   // a key = 64.66 ref = 582 scrap

test('plan: inside the band nothing; short of metal sells keys up to need; extra metal buys keys', () => {
	let p = (metalKeys, need, keys = 8) => plan({metal: metalKeys * KS, keys, keyScrap: KS, need, low: 0.5, high: 1.5});
	assert.equal(p(1.0, 1.0).kind, null);
	assert.equal(p(0.6, 1.0).kind, null, 'within half a key under');
	assert.equal(p(2.4, 1.0).kind, null, 'within 1.5 keys over');
	assert.deepEqual([p(0.2, 2.0).kind, p(0.2, 2.0).n], ['sell_key', 2]);
	assert.deepEqual([p(0, 2.0, 1).kind, p(0, 2.0, 1).n], ['sell_key', 1], 'no more keys than we hold');
	assert.equal(p(0, 2.0, 0).kind, null, 'no keys: nothing to sell');
	assert.deepEqual([p(3.7, 1.0).kind, p(3.7, 1.0).n], ['buy_key', 2]);
	assert.deepEqual([p(9, 0).kind, p(9, 0).n], ['buy_key', 3], 'at most 3 a cycle');
	assert.equal(p(1.4, 0).kind, null, 'everything in play: up to 1.5 keys of metal kept');
});

const CFG = {max_open_positions: 100, max_keys_in_play: 2, max_trade_keys: 2, max_new_buys_per_pass: 10, win_window: 20,
	min_win_rate: 0.75, max_tries: 3, bot_wait_s: 30, poll_ms: 3000, approve_wait_s: 900,
	target_rec: 15, target_scrap: 10, key_band_low: 0.5, key_band_high: 1.5};
const URL = sid => `https://steamcommunity.com/tradeoffer/new/?partner=${sid}&token=tok`;
const row = (side, sid, ref) => ({side, steam_id: sid, user: 'bot' + sid, trade_url: URL(sid), keys: 0, ref,
	total_keys: +(ref / 64.66).toFixed(4), listing_id: side === 'sell' ? `440_${sid}00` : `440_${sid}_h`,
	asset_id: side === 'sell' ? `${sid}00` : null});
const items = (name, n, tag) => Array.from({length: n}, (_, i) => ({assetid: `${tag}${i}`, market_hash_name: name, tradable: true}));
const inv = ({key = 0, ref = 0, rec = 0, scrap = 0}, tag = '') => [...items(KEY, key, tag + 'K'), ...items('Refined Metal', ref, tag + 'R'),
	...items('Reclaimed Metal', rec, tag + 'C'), ...items('Scrap Metal', scrap, tag + 'S')];

// Every bot accepts on the first poll unless listed in `ignore`.
function steam(ours, ignore = []) {
	let offers = {}, n = 0, sent = [];
	return {sent, async holdDays() { return {me: 0, them: 0}; },
		async send(spec) { let id = String(++n); offers[id] = {spec, state: 'Active'}; sent.push(spec); return id; },
		async confirm() {}, async state(id) {
			if (offers[id].state === 'Active' && !ignore.includes(offers[id].spec.partner)) offers[id].state = 'Accepted';
			return {state: offers[id].state};
		},
		async cancel(id) { offers[id].state = 'Canceled'; }, async received() { return []; }, async sentSince() { return []; },
		async incoming() { return []; }, async decline() {}, async inventory() { return ours; }};
}

function deps(ours, book, over = {}) {
	let d = fs.mkdtempSync(path.join(os.tmpdir(), 'convert-'));
	let ledgerPath = path.join(d, 'agent.db'), asked = [], logs = [];
	let engine = async screen => ({code: 0, data: screen === 'exits' ? [] : screen === 'recheck' ? book
		: {stale: false, pass_id: 1, n_over_bar: 0, n_held: 0, picks: []}});
	return {ledger: new Ledger(ledgerPath), ledgerPath, target: '127.0.0.1:1', cfg: CFG, log: s => logs.push(s), logs,
		engine, inventory: async sid => [...inv({ref: 80, rec: 30, scrap: 30}, 't' + sid), ...items(KEY, 1, `${sid}0`).map(x => ({...x, assetid: `${sid}00`}))],
		sleep: async () => {}, steam: steam(ours), approve: async q => { asked.push(q); return true; }, asked,
		stopFiles: [path.join(d, 'STOP')], resumedFile: path.join(d, 'RESUMED'), ...over};
}

const BOOK = {ok: true, watch_id: 9, ask_ref: 64.77, bid_ref: 64.66, key_ref: 64.715,
	buy: [row('sell', '1', 64.77)], sell: [row('buy', '2', 64.66), row('buy', '3', 64.55)]};

test('no metal, 2 keys of room: sells 2 keys, each for metal that lands near 15 rec / 10 scrap', async () => {
	let D = deps(inv({key: 8, rec: 2}), BOOK);
	let r = await cycle(D, {lastPassId: null});
	assert.equal(r.converted, 2);
	assert.equal(D.steam.sent.length, 2);
	let s = D.steam.sent[0];
	assert.equal(s.partner, '2');
	assert.deepEqual(s.give.map(a => a.assetid), ['K0'], 'one of our keys, no metal');
	let got = s.receive.map(a => a.assetid);
	let rec = 2 + got.filter(id => id.includes('C')).length, scrap = got.filter(id => id.includes('S')).length;
	// 582 scrap: 14 rec + 9 scrap + 59 refined -> 16 / 9, as near 15 / 10 as 582 allows (2 off)
	assert.deepEqual([rec, scrap], [16, 9]);
	assert.equal(D.steam.sent[1].give[0].assetid, 'K1', 'the second sale gives another key');
	let c = D.ledger.db.prepare('SELECT * FROM conversions ORDER BY id').all();
	assert.deepEqual(c.map(x => [x.kind, x.state, x.price_ref]), [['sell_key', 'accepted', '64.66'], ['sell_key', 'accepted', '64.66']]);
	assert.equal(D.ledger.exposure('live').keys, 0, 'a conversion is not a position');
	assert.match(D.asked[0], /^SELL 1 key to bot2 for 64\.66 ref/);
});

test('extra metal: buys a key, paid in metal, the seller\'s asset checked', async () => {
	let D = deps(inv({key: 0, ref: 300, rec: 15, scrap: 10}), BOOK);
	let r = await cycle(D, {lastPassId: null});
	assert.equal(r.converted, 2, '4.7 keys of metal, 2 of room: 2 keys bought, metal back to ~2.7');
	let s = D.steam.sent[0];
	assert.ok(s.receive.some(a => a.assetid === '100'), 'the listed key');
	// 64.77 ref = 583 scrap: 65 refined out, 2 scrap back -- our 15 rec / 10 scrap untouched
	assert.deepEqual([s.give.length, s.give.every(a => a.assetid.startsWith('R'))], [65, true]);
	assert.equal(s.change_scrap, 2);
	D = deps(inv({ref: 300, rec: 15, scrap: 10}), BOOK, {inventory: async () => inv({ref: 5}, 'x')});
	r = await cycle(D, {lastPassId: null});
	assert.equal(r.converted, 0, 'their inventory has no such key: no buy');
	assert.equal(D.steam.sent.length, 0);
});

test('a key buy tries every seller in the window, past max_tries: four ignore us, the fifth fills', async () => {
	// 2026-11-01: the cheapest key sellers ignored three cycles of offers, the rest went untried
	let sells = [[1, 63.66], [2, 63.66], [3, 63.77], [4, 63.88], [5, 64.11]].map(([sid, ref]) => row('sell', String(sid), ref));
	let D = deps(inv({key: 0, ref: 300, rec: 15, scrap: 10}), {...BOOK, ask_ref: 63.66, bid_ref: 64.11, key_ref: 63.885, buy: sells},
		{steam: steam(inv({key: 0, ref: 300, rec: 15, scrap: 10}), ['1', '2', '3', '4'])});
	D.cfg = {...CFG, max_conversions: 1};
	let r = await cycle(D, {lastPassId: null});
	assert.equal(r.converted, 1, D.logs.join('\n'));
	assert.deepEqual(D.steam.sent.map(s => s.partner), ['1', '2', '3', '4', '5']);
	assert.ok(D.logs.some(l => /KEY BOUGHT for 64\.11 ref with bot5/.test(l)));
});

test('inside the band, no bands set, or no key book: nothing converted', async () => {
	// 2 keys of room, ~2.2 keys of metal: in the band
	let D = deps(inv({key: 8, ref: 140, rec: 15, scrap: 10}), BOOK);
	assert.equal((await cycle(D, {lastPassId: null})).converted, 0);
	assert.ok(D.logs.some(l => /keys: in the band/.test(l)));
	let {key_band_low, key_band_high, ...plain} = CFG;
	D = deps(inv({key: 8}), BOOK, {cfg: plain});
	assert.equal((await cycle(D, {lastPassId: null})).converted, 0);
	D = deps(inv({key: 8}), BOOK);
	let engine = D.engine;
	D.engine = async (screen, args) => { if (screen === 'recheck') throw new Error('engine recheck failed (2): tunnel down'); return engine(screen, args); };
	let r = await cycle(D, {lastPassId: null});
	assert.deepEqual([r.converted, r.bought], [0, 0], 'a failed key fetch skips the rebalance, the cycle goes on');
	assert.ok(D.logs.some(l => /no rebalance -- key book: engine recheck failed/.test(l)));
	assert.ok(D.logs.some(l => /pass 1:/.test(l)), 'the buys step still ran');
	D = deps(inv({key: 8}), {ok: false, reason: 'no tradable key bid in metal now'});
	assert.equal((await cycle(D, {lastPassId: null})).converted, 0);
	assert.ok(D.logs.some(l => /no rebalance -- no tradable key bid/.test(l)));
});

test('the restart check: an open conversion is read back from Steam', async () => {
	let D = deps(inv({key: 8}), BOOK);
	let offer = {partner: '2', trade_url: URL('2'), listing_id: 'x', price: {keys: 0, ref: '64.66'},
		receive: [{assetid: 'a'}], give: [{assetid: 'K0'}]};
	let id = D.ledger.addConversion({kind: 'sell_key', offer});
	D.ledger.setConversion(id, 'sent', {steamOfferId: '77'});
	assert.ok(D.ledger.reservedAssets().has('K0'), 'its key is promised');
	D.steam.state = async () => ({state: 'Accepted'});
	await reconcile(D);
	assert.equal(D.ledger.conversion(id).state, 'accepted');
	let id2 = D.ledger.addConversion({kind: 'sell_key', offer});
	await reconcile(D);
	assert.equal(D.ledger.conversion(id2).state, 'canceled', 'never reached Steam');
});

test('edge: a sale keeps the keys a pick may need; nothing to do is no plan; no key price, no plan', () => {
	let p = o => plan({keyScrap: KS, low: 0.5, high: 1.5, ...o});
	assert.deepEqual([p({metal: 0, keys: 3, need: 3, keep: 1}).kind, p({metal: 0, keys: 3, need: 3, keep: 1}).n], ['sell_key', 2]);
	assert.equal(p({metal: 0, keys: 1, need: 3, keep: 1}).kind, null, 'the last key stays (a 1 key + ref pick)');
	assert.equal(p({metal: 9 * KS, keys: 0, need: 0, max: 0}).kind, null, 'max_conversions 0: none');
	assert.equal(plan({metal: 9 * KS, keys: 0, keyScrap: NaN, need: 0, low: 0.5, high: 1.5}).kind, null, 'never buy on a NaN price');
	assert.equal(plan({metal: 9 * KS, keys: 0, keyScrap: 0, need: 0, low: 0.5, high: 1.5}).kind, null);
});

test('edge: the metal wanted is the room, at most max_metal_keys', () => {
	assert.equal(needKeys({max_keys_in_play: 8, max_metal_keys: 3}, 1.43), 3);
	assert.ok(Math.abs(needKeys({max_keys_in_play: 8, max_metal_keys: 3}, 6.5) - 1.5) < 1e-9);
	assert.equal(needKeys({max_keys_in_play: 8, max_metal_keys: 3}, 9), 0, 'over the limit (a limit lowered): none');
	assert.ok(Math.abs(needKeys({max_keys_in_play: 8}, 1.43) - 6.57) < 1e-9, 'no cap: the whole room');
});

test('edge: with 8 keys of room and the 3-key cap, the 8 keys in hand become ~3 keys of metal, not 6.6', async () => {
	let D = deps(inv({key: 8, ref: 29, rec: 17, scrap: 64}), BOOK, {cfg: {...CFG, max_keys_in_play: 8, max_metal_keys: 3}});
	let r = await cycle(D, {lastPassId: null});
	assert.equal(r.converted, 3, D.logs.join(' | '));   // 0.65 keys of metal < 3 - 0.5: ceil(2.35) = 3
	assert.ok(D.logs.some(l => /keys: sell 3 \(metal 0\.65 keys, 8 keys, metal wanted 3\.00/.test(l)), D.logs.join(' | '));
});

test('edge: buys paused by the win-rate stop -- no key sold for buys that will not come', async () => {
	let D = deps(inv({key: 8}), BOOK, {cfg: {...CFG, max_keys_in_play: 8, max_metal_keys: 3}});
	fs.writeFileSync(path.join(path.dirname(D.resumedFile), 'BUYS_PAUSED'), 'paused for the test');
	assert.equal((await cycle(D, {lastPassId: null})).converted, 0);
	assert.ok(D.logs.some(l => /buys paused/.test(l)), D.logs.join(' | '));
});

test('edge: a lagging inventory still lists the key just sold -- the next sale and the buys never give it', async () => {
	let D = deps(inv({key: 8}), BOOK);     // the fake inventory never changes: the worst lag
	await cycle(D, {lastPassId: null});
	let given = D.steam.sent.map(s => s.give[0].assetid);
	assert.deepEqual(given, ['K0', 'K1'], 'each sale a different key');
	let ours = require('../lib/currency').stock(await D.steam.inventory(), D.ledger.reservedAssets());
	assert.ok(!ours.key.includes('K0') && !ours.key.includes('K1'), 'a buy reading our stock now does not see them');
});

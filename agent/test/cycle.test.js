// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {Ledger} = require('../lib/ledger');
const {cycle} = require('../lib/cycle');

const CFG = {max_open_positions: 100, max_keys_in_play: 1, max_trade_keys: 1,
	max_new_buys_per_pass: 10, win_window: 20, min_win_rate: 0.75, max_tries: 3, bot_wait_s: 30, poll_ms: 3000,
	approve_wait_s: 900};
const URL = sid => `https://steamcommunity.com/tradeoffer/new/?partner=${sid}&token=tok`;
// prices in whole scrap: 30.33 ref = 273 scrap, at 60 ref a key
const listing = (side, sid, ref) => ({side, reason: null, steam_id: sid, user: 'bot' + sid, trade_url: URL(sid),
	listing_id: side === 'sell' ? `440_${sid}00` : `440_${sid}_h`, asset_id: side === 'sell' ? `${sid}00` : null,
	keys: 0, ref, total_keys: +(ref / 60).toFixed(4)});
const metal = (name, n, tag) => Array.from({length: n}, (_, i) => ({assetid: `${tag}${i}`, market_hash_name: name, tradable: true}));
const OURS = [...metal('Refined Metal', 60, 'R'), ...metal('Reclaimed Metal', 20, 'C'), ...metal('Scrap Metal', 20, 'S')];
const THEIRS = [...metal('Refined Metal', 60, 'tR'), ...metal('Reclaimed Metal', 5, 'tC'), ...metal('Scrap Metal', 5, 'tS')];
const pick = (item, ask1, p_up = 0.8) => ({item, quality: 6, variant: 0, label: `${item} (Unique)`, ask1, p_up, exp_ret: 0.03});

function fakeEngine(script) {
	let calls = [];
	let fn = async (screen, args) => {
		calls.push([screen, ...args]);
		let r = script[screen];
		return {code: 0, data: typeof r === 'function' ? r(args) : r};
	};
	fn.calls = calls;
	return fn;
}

// Every bot accepts on the first poll unless listed in `ignore`.
function fakeSteam({ignore = [], ourHold = 0} = {}) {
	let offers = {}, n = 0, sent = [];
	return {
		sent,
		async holdDays() { return {me: ourHold, them: 0}; },
		async send(spec) { let id = String(++n); offers[id] = {spec, state: 'Active'}; sent.push(spec); return id; },
		async confirm() {},
		async state(id) {
			let o = offers[id];
			if (o.state === 'Active' && !ignore.includes(o.spec.partner)) o.state = 'Accepted';
			return {state: o.state};
		},
		async cancel(id) { offers[id].state = 'Canceled'; },
		async received(id) { return offers[id].spec.receive.map(a => ({assetid: a.assetid, new_assetid: `new-${a.assetid}`})); },
		async sentSince() { return []; },
		async incoming() { return []; },
		async decline() {},
		async inventory() { return OURS; },
	};
}

// A seller's inventory: their metal plus the listed asset (`<sid>00`) named `name`.
const sellerInv = (name = 'A') => async sid => [...THEIRS, {assetid: `${sid}00`, market_hash_name: name, tradable: true}];

function deps(over = {}) {
	let d = fs.mkdtempSync(path.join(os.tmpdir(), 'cycle-'));
	let ledgerPath = path.join(d, 'agent.db');
	let asked = [];
	return {ledger: new Ledger(ledgerPath), ledgerPath, target: '127.0.0.1:1', cfg: CFG, log: () => {},
		inventory: sellerInv(), sleep: async () => {}, steam: fakeSteam(),
		approve: async q => { asked.push(q); return true; }, asked,
		stopFiles: [path.join(d, 'STOP')], resumedFile: path.join(d, 'RESUMED'), ...over};
}

const buyEngine = (picks, recheck) => fakeEngine({exits: [],
	picks: {stale: false, pass_id: 300, n_over_bar: picks.length, n_held: 0, picks}, recheck});

test("a pick: re-checked, approved, re-checked again, paid in refined, the seller's change back, bought", async () => {
	let engine = buyEngine([pick('A', 0.51)], () => ({ok: true, watch_id: 8, listing: listing('sell', '1', 30.33),
		candidates: [listing('sell', '1', 30.33), listing('sell', '2', 30.44)]}));
	let D = deps({engine});
	let r = await cycle(D, {lastPassId: null});
	assert.deepEqual(r, {sold: 0, bought: 1, converted: 0});
	assert.equal(engine.calls.filter(c => c[0] === 'recheck').length, 2, 'once before asking, once after the yes');
	assert.match(D.asked[0], /^BUY A \(Unique\) from bot1 at 0\.5055 keys \(p_up 0\.80/);
	let p = D.ledger.held()[0];
	assert.deepEqual([p.item, p.state, p.asset_id, p.pass_id], ['A', 'open', 'new-100', 300]);
	let give = D.steam.sent[0].give.map(a => a.assetid);
	assert.deepEqual([give.filter(a => a[0] === 'R').length, give.filter(a => a[0] === 'C').length,
		give.filter(a => a[0] === 'S').length], [31, 0, 0], '30.33 ref: 31 refined, our small pieces kept');
	assert.deepEqual(D.steam.sent[0].receive.map(a => a.assetid), ['100', 'tC0', 'tC1'], 'their 2 reclaimed back as change');
});

test('not approved, or no answer: nothing is sent, and no answer skips the rest of the cycle', async () => {
	let engine = buyEngine([pick('A', 0.5), pick('B', 0.3)], () => ({ok: true, listing: listing('sell', '1', 30),
		candidates: [listing('sell', '1', 30)]}));
	let D = deps({engine, approve: async () => false});
	assert.deepEqual(await cycle(D, {lastPassId: null}), {sold: 0, bought: 0, converted: 0});
	assert.equal(D.steam.sent.length, 0);
	let asked = 0;
	let E = deps({engine, approve: async () => { asked++; return null; }});
	await cycle(E, {lastPassId: null});
	assert.equal(asked, 1, 'away: B is not even asked');
	assert.equal(E.steam.sent.length, 0);
});

test('gone at approval: the second re-check says no, nothing sent', async () => {
	let n = 0;
	let engine = buyEngine([pick('A', 0.5)], () => ++n === 1
		? {ok: true, listing: listing('sell', '1', 30), candidates: [listing('sell', '1', 30)]}
		: {ok: false, reason: 'dearer than the pick'});
	let D = deps({engine});
	await cycle(D, {lastPassId: null});
	assert.equal(D.steam.sent.length, 0);
	assert.equal(D.ledger.held().length, 0);
});

test('keys in play: the second pick waits', async () => {
	let engine = buyEngine([pick('A', 0.6), pick('B', 0.6)], args => ({ok: true,
		listing: listing('sell', args[0] === 'A' ? '1' : '2', 36), candidates: [listing('sell', args[0] === 'A' ? '1' : '2', 36)]}));
	let D = deps({engine});
	assert.deepEqual(await cycle(D, {lastPassId: null}), {sold: 0, bought: 1, converted: 0});
	assert.equal(D.asked.length, 1, 'B over max_keys_in_play: not asked');
});

test('a sale: the exit says SELL, approved, re-checked, sold to the next bot when the first ignores us', async () => {
	let D = deps({steam: fakeSteam({ignore: ['11']})});
	let b = D.ledger.openBuy({mode: 'live', pick: {...pick('A', 0.5), pass_id: 299}, offer: {partner: '1', trade_url: URL(1),
		price: {keys: 0, ref: '30', total_keys: 0.5}, receive: [{appid: 440, contextid: '2', assetid: '100'}], give: []}});
	D.ledger.setOffer(b.offerId, 'sent', {steamOfferId: '900'});
	D.ledger.setOffer(b.offerId, 'accepted', {assetId: 'ours-1'});
	let bids = [listing('buy', '11', 31.33), listing('buy', '12', 31.22)];
	D.engine = fakeEngine({
		exits: [{position_id: b.positionId, item: 'A', action: 'sell', why: 'take_profit', min_keys: 0.52, listing: bids[0]}],
		recheck: () => ({ok: true, watch_id: 9, listing: bids[0], candidates: bids}),
		picks: {stale: true, reason: 'old pass', picks: []},
	});
	let r = await cycle(D, {lastPassId: null});
	assert.deepEqual(r, {sold: 1, bought: 0, converted: 0});
	assert.match(D.asked[0], /^SELL A \(take_profit\) to bot11 at 0\.5222 keys \(paid 0\.5000, \+4\.4%\)/);
	let p = D.ledger.position(b.positionId);
	assert.deepEqual([p.state, p.exit_reason, p.exit_keys], ['closed', 'take_profit', 0.5203]);
	assert.deepEqual(D.steam.sent.map(s => s.partner), ['11', '12']);
	assert.equal(D.steam.sent[1].give[0].assetid, 'ours-1', 'the sale gives our item');
});

test('a sale no bot can pay: nothing sent, and the held line says why for each bot', async () => {
	let lines = [];
	let D = deps({inventory: async () => [], log: l => lines.push(l)});      // the buyers hold no metal
	let b = D.ledger.openBuy({mode: 'live', pick: {...pick('A', 0.5), pass_id: 299}, offer: {partner: '1', trade_url: URL(1),
		price: {keys: 0, ref: '30', total_keys: 0.5}, receive: [{appid: 440, contextid: '2', assetid: '100'}], give: []}});
	D.ledger.setOffer(b.offerId, 'sent', {steamOfferId: '900'});
	D.ledger.setOffer(b.offerId, 'accepted', {assetId: 'ours-1'});
	let bids = [listing('buy', '11', 31.33), listing('buy', '12', 31.22)];
	D.engine = fakeEngine({
		exits: [{position_id: b.positionId, item: 'A', action: 'sell', why: 'take_profit', min_keys: 0.52, listing: bids[0]}],
		recheck: () => ({ok: true, watch_id: 9, listing: bids[0], candidates: bids}),
		picks: {stale: true, reason: 'old pass', picks: []},
	});
	let r = await cycle(D, {lastPassId: null});
	assert.deepEqual(r, {sold: 0, bought: 0, converted: 0});
	assert.deepEqual(D.steam.sent, []);
	let held = lines.find(l => /sell A: no bot took it/.test(l));
	assert.match(held, /bot11: buyer can't pay: .*; bot12: buyer can't pay: .* -- held, tried again next cycle$/);
});

test('our trades would be held: the kill switch goes on, nothing sent', async () => {
	let engine = buyEngine([pick('A', 0.5)], () => ({ok: true, listing: listing('sell', '1', 30), candidates: [listing('sell', '1', 30)]}));
	let D = deps({engine, steam: fakeSteam({ourHold: 7})});
	let r = await cycle(D, {lastPassId: null});
	assert.equal(r.stopped, true);
	assert.match(fs.readFileSync(D.stopFiles[0], 'utf8'), /hold our trades 7 days/);
	assert.equal(D.steam.sent.length, 0);
	assert.deepEqual(await cycle(D, {lastPassId: null}), {sold: 0, bought: 0, stopped: true}, 'stays stopped');
});

test('the kill switch: nothing at all this cycle', async () => {
	let engine = buyEngine([pick('A', 0.5)], () => ({ok: true}));
	let D = deps({engine});
	fs.writeFileSync(D.stopFiles[0], 'x');
	assert.equal((await cycle(D, {lastPassId: null})).stopped, true);
	assert.equal(engine.calls.length, 0);
});

test('the asset check: a seller whose asset is not the pick is skipped, the next one is bought', async () => {
	let cands = [listing('sell', '1', 30), listing('sell', '2', 30.11), listing('sell', '3', 30.22)];
	let engine = buyEngine([pick('A', 0.6)], () => ({ok: true, listing: cands[0], candidates: cands}));
	let names = {1: 'Strange A', 2: 'Professional Killstreak A', 3: 'The A'};
	let lines = [];
	let D = deps({engine, log: l => lines.push(l),
		inventory: async sid => [...THEIRS, {assetid: `${sid}00`, market_hash_name: names[sid], tradable: true}]});
	assert.deepEqual(await cycle(D, {lastPassId: null}), {sold: 0, bought: 1, converted: 0});
	assert.deepEqual(D.steam.sent.map(s => s.partner), ['3'], 'only the seller whose asset is "The A"');
	assert.equal(D.steam.sent[0].receive[0].assetid, '300');
	assert.ok(lines.some(l => /BOUGHT A \(Unique\) at 0\.5037 from bot3/.test(l)), lines.join('\n'));
});

test('the asset check: missing, untradable, or an unreadable inventory -> no buy, nothing sent', async () => {
	for (let inventory of [async () => THEIRS,
		async sid => [...THEIRS, {assetid: `${sid}00`, market_hash_name: 'A', tradable: false}],
		async () => { throw new Error('inventory is private'); }]) {
		let engine = buyEngine([pick('A', 0.6)], () => ({ok: true, listing: listing('sell', '1', 30), candidates: [listing('sell', '1', 30)]}));
		let lines = [];
		let D = deps({engine, inventory, log: l => lines.push(l)});
		assert.deepEqual(await cycle(D, {lastPassId: null}), {sold: 0, bought: 0, converted: 0});
		assert.equal(D.steam.sent.length, 0);
		assert.ok(lines.some(l => /skip A \(Unique\): no fill -- bot1: (asset 100 is not|their inventory could not be read)/.test(l)), lines.join('\n'));
	}
});

test('incoming offers: every one declined and logged on one line; a failure never stops the cycle', async () => {
	let steam = fakeSteam(), declined = [], lines = [];
	steam.incoming = async () => [
		{id: '71', partner: '765', give: 3, receive: 0, message: 'free keys!\nclick here'},
		{id: '72', partner: '766', give: 1, receive: 1, message: ''}];
	steam.decline = async id => { if (id === '72') throw new Error('HTTP 500'); declined.push(id); };
	let engine = buyEngine([pick('A', 0.6)], () => ({ok: true, listing: listing('sell', '1', 30), candidates: [listing('sell', '1', 30)]}));
	let D = deps({engine, steam, log: l => lines.push(l)});
	assert.deepEqual(await cycle(D, {lastPassId: null}), {sold: 0, bought: 1, converted: 0}, 'the cycle goes on');
	assert.deepEqual(declined, ['71']);
	assert.ok(lines.includes('  incoming: declined offer 71 from 765 (asks 3 of ours, gives 0; message "free keys! click here")'));
	assert.ok(lines.some(l => l.startsWith('  incoming: decline 72 from 766: HTTP 500')));

	steam.incoming = async () => { throw new Error('429'); };
	let E = deps({engine: buyEngine([], () => ({ok: false})), steam, log: l => lines.push(l)});
	assert.equal((await cycle(E, {lastPassId: null})).stopped, undefined);
	assert.ok(lines.includes('  incoming: not readable (429)'));
});

test('incoming offers: the kill switch on -> not even read', async () => {
	let steam = fakeSteam(), read = 0;
	steam.incoming = async () => { read++; return []; };
	let D = deps({steam, engine: buyEngine([], () => ({ok: false}))});
	fs.writeFileSync(D.stopFiles[0], 'x');
	await cycle(D, {lastPassId: null});
	assert.equal(read, 0);
});

test('report: held, closed, per trade vs the backtest', () => {
	const {stats} = require('../lib/report');
	let D = deps();
	let o = t => ({partner: '1', price: {keys: 0, ref: '0', total_keys: t}, receive: [], give: []});
	for (let [item, entry, exit] of [['A', 1.0, 1.04], ['B', 2.0, 1.9], ['C', 1.0, null]]) {
		let r = D.ledger.openBuy({mode: 'live', pick: pick(item, entry), offer: o(entry)});
		D.ledger.setOffer(r.offerId, 'sent');
		D.ledger.setOffer(r.offerId, 'accepted', {assetId: 'p'});
		if (exit == null) continue;
		let s = D.ledger.openSell({positionId: r.positionId, offer: o(exit)});
		D.ledger.setOffer(s.offerId, 'sent');
		D.ledger.setOffer(s.offerId, 'accepted', {exitReason: exit > entry ? 'take_profit' : 'horizon'});
	}
	let s = stats(D.ledger, 'live');
	assert.equal(s.held.length, 1);
	assert.equal(s.closed.length, 2);
	assert.equal(s.win, 0.5);
});

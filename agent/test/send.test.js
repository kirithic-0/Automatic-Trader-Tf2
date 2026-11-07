// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {Ledger} = require('../lib/ledger');
const {tryOffers, HoldError} = require('../lib/send');

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'send-')), 'agent.db');
const CFG = {max_tries: 3, bot_wait_s: 30, poll_ms: 3000};
const URL = sid => `https://steamcommunity.com/tradeoffer/new/?partner=${sid}&token=t`;
const sellListing = (sid, total) => ({side: 'sell', reason: null, steam_id: sid, user: 'bot' + sid, trade_url: URL(sid),
	listing_id: `440_${sid}00`, asset_id: `${sid}00`, keys: 0, ref: total * 60, total_keys: total});
const pick = {item: 'Hat', quality: 6, variant: 0, label: 'Hat (Unique)', pass_id: 300, p_up: 0.8, exp_ret: 0.03, ask1: 0.5};
const specFor = l => ({partner: l.steam_id, trade_url: l.trade_url, listing_id: l.listing_id,
	price: {keys: 0, ref: '30', total_keys: l.total_keys}, receive: [{appid: 440, contextid: '2', assetid: l.asset_id}],
	give: [{appid: 440, contextid: '2', assetid: 'r1'}]});

// A fake Steam: per partner, what the bot does -- 'accept' (on the 2nd poll), 'ignore',
// 'decline', 'race' (accepts as we cancel), 'hold' (their trades held); `ourHold` = our days.
function fakeSteam(behaviour, {ourHold = 0} = {}) {
	let offers = {}, n = 0, calls = [];
	return {
		calls,
		async holdDays(url) {
			let sid = url.match(/partner=(\d+)/)[1];
			return {me: ourHold, them: behaviour[sid] === 'hold' ? 3 : 0};
		},
		async send(spec, ...rest) {
			let id = String(++n);
			offers[id] = {sid: spec.partner, state: 'CreatedNeedsConfirmation', polls: 0, spec};
			calls.push(['send', spec.partner, ...rest]);
			return id;
		},
		async confirm(id) { offers[id].state = 'Active'; calls.push(['confirm', id]); },
		async state(id) {
			let o = offers[id], b = behaviour[o.sid];
			if (o.state === 'Active' && ++o.polls >= 2) {
				if (b === 'accept') o.state = 'Accepted';
				if (b === 'decline') o.state = 'Declined';
			}
			return {state: o.state};
		},
		async cancel(id) {
			calls.push(['cancel', id]);
			offers[id].state = behaviour[offers[id].sid] === 'race' ? 'Accepted' : 'Canceled';
		},
		async received(id) {
			return offers[id].spec.receive.map(a => ({assetid: a.assetid, new_assetid: `new-${a.assetid}`}));
		},
	};
}

function deps(steam) {
	let ledger = new Ledger(tmp());
	return {steam, ledger, log: () => {}, cfg: CFG, sleep: async () => {}};
}

function buy(D, candidates) {
	return tryOffers(D, {kind: 'buy', candidates, build: l => ({ok: true, offer: specFor(l)}),
		start: offer => D.ledger.openBuy({mode: 'live', pick, offer})});
}

test('the first bot accepts: filled, the new asset id kept, every state timed', async () => {
	let D = deps(fakeSteam({1: 'accept'}));
	let r = await buy(D, [sellListing('1', 0.5), sellListing('2', 0.5)]);
	assert.equal(r.filled, true);
	let p = D.ledger.position(r.positionId);
	assert.deepEqual([p.state, p.asset_id], ['open', 'new-100']);
	assert.deepEqual(D.ledger.events(1).map(e => e.state), ['built', 'sent', 'active', 'accepted']);
	assert.equal(D.ledger.offer(1).steam_offer_id, '1');
	assert.deepEqual(D.steam.calls[0], ['send', '1'], 'sent with no message');
});

test('no answer in 30 s: canceled, the next bot gets the same position', async () => {
	let D = deps(fakeSteam({1: 'ignore', 2: 'accept'}));
	let r = await buy(D, [sellListing('1', 0.5), sellListing('2', 0.49)]);
	assert.equal(r.filled, true);
	assert.equal(D.ledger.offer(1).state, 'canceled');
	assert.equal(D.ledger.offer(2).state, 'accepted');
	assert.equal(D.ledger.offer(2).position_id, r.positionId);
	assert.equal(D.ledger.position(r.positionId).entry_keys, 0.49, 'the entry is what the second bot was paid');
	assert.deepEqual(D.steam.calls.filter(c => c[0] === 'cancel'), [['cancel', '1']]);
});

test('the bot accepts as we cancel: a fill, and no second offer', async () => {
	let D = deps(fakeSteam({1: 'race', 2: 'accept'}));
	let r = await buy(D, [sellListing('1', 0.5), sellListing('2', 0.5)]);
	assert.equal(r.filled, true);
	assert.equal(D.ledger.offer(1).state, 'accepted');
	assert.equal(D.steam.calls.filter(c => c[0] === 'send').length, 1);
});

test('a bot whose trades would be held is skipped; a decline moves on too', async () => {
	let D = deps(fakeSteam({1: 'hold', 2: 'decline', 3: 'accept'}));
	let r = await buy(D, [sellListing('1', 0.5), sellListing('2', 0.5), sellListing('3', 0.5)]);
	assert.equal(r.filled, true);
	assert.deepEqual([1, 2, 3].map(i => D.ledger.offer(i).state), ['failed', 'declined', 'accepted']);
	assert.equal(D.steam.calls.filter(c => c[0] === 'send').length, 2, 'nothing is sent to a held partner');
});

test('three bots, no fill: at most 3 tried, the buy abandoned', async () => {
	let D = deps(fakeSteam({1: 'ignore', 2: 'ignore', 3: 'ignore', 4: 'accept'}));
	let r = await buy(D, ['1', '2', '3', '4'].map(s => sellListing(s, 0.5)));
	assert.equal(r.filled, false);
	assert.equal(D.ledger.position(r.positionId).state, 'abandoned');
	assert.equal(D.steam.calls.filter(c => c[0] === 'send').length, 3);
	assert.equal(D.ledger.isHeld('Hat', 6, 0), false);
});

test('an offer that cannot be built does not count as a try', async () => {
	let D = deps(fakeSteam({2: 'accept'}));
	let r = await tryOffers(D, {kind: 'buy', candidates: [sellListing('1', 0.5), sellListing('2', 0.5)],
		build: l => l.steam_id === '1' ? {ok: false, reason: 'no exact change'} : {ok: true, offer: specFor(l)},
		start: offer => D.ledger.openBuy({mode: 'live', pick, offer})});
	assert.equal(r.filled, true);
	assert.equal(D.ledger.db.prepare('SELECT count(*) n FROM offers').get().n, 1);
});

test('our own trades would be held: HoldError, nothing sent, the buy abandoned', async () => {
	let D = deps(fakeSteam({1: 'accept'}, {ourHold: 15}));
	await assert.rejects(buy(D, [sellListing('1', 0.5)]), HoldError);
	assert.equal(D.steam.calls.length, 0);
	assert.equal(D.ledger.position(1).state, 'abandoned');
});

test('a sale no bot takes goes back to open', async () => {
	let D = deps(fakeSteam({1: 'ignore'}));
	let b = await buy({...D, steam: fakeSteam({1: 'accept'})}, [sellListing('1', 0.5)]);
	let bid = {...sellListing('1', 0.53), side: 'buy', asset_id: null};
	let r = await tryOffers(D, {kind: 'sell', candidates: [bid], build: l => ({ok: true, offer: specFor(l)}),
		start: offer => ({positionId: b.positionId, ...D.ledger.openSell({positionId: b.positionId, offer})}),
		extra: {exitReason: 'take_profit'}});
	assert.equal(r.filled, false);
	assert.equal(D.ledger.position(b.positionId).state, 'open');
});

// confirm() throws, but Steam may have taken the confirmation: `after` = the offer's state
// then; `cancelFails` = the cancel throws too (a 429).
function confirmFails(after, {cancelFails = false, onCancel = 'Canceled'} = {}) {
	let s = fakeSteam({1: 'accept', 2: 'accept'});
	let confirm = s.confirm, cancel = s.cancel, state = s.state, n = 0;
	s.confirm = async id => { if (++n > 1) return confirm(id); throw new Error('ETIMEDOUT'); };
	s.cancel = async id => { s.calls.push(['cancel', id]); if (cancelFails) throw new Error('HTTP 429'); s._st[id] = onCancel; };
	s._st = {1: after};
	s.state = async id => id in s._st ? {state: s._st[id]} : state(id);
	return s;
}

test('confirmation fails but went through, and the cancel fails: stopped, the offer left for the restart check', async () => {
	let D = deps(confirmFails('Active', {cancelFails: true}));
	await assert.rejects(buy(D, [sellListing('1', 0.5), sellListing('2', 0.5)]), /still Active after a failed confirmation/);
	assert.equal(D.steam.calls.filter(c => c[0] === 'send').length, 1, 'no second bot while the first offer is live');
	let [o] = D.ledger.openOffers();
	assert.deepEqual([o.state, o.steam_offer_id], ['sent', '1']);
	assert.equal(D.ledger.position(o.position_id).state, 'opening', 'not given up: reconcile decides');
});

test('confirmation fails, the bot took it anyway (accepted as we cancel): a fill, no second offer', async () => {
	let D = deps(confirmFails('Active', {onCancel: 'Accepted'}));
	let r = await buy(D, [sellListing('1', 0.5), sellListing('2', 0.5)]);
	assert.equal(r.filled, true);
	assert.equal(D.steam.calls.filter(c => c[0] === 'send').length, 1);
	assert.equal(D.ledger.held()[0].asset_id, 'new-100');
});

test('confirmation fails and the cancel ends it: the next bot', async () => {
	let D = deps(confirmFails('CreatedNeedsConfirmation'));
	let r = await buy(D, [sellListing('1', 0.5), sellListing('2', 0.5)]);
	assert.equal(r.filled, true);
	assert.equal(r.listing.steam_id, '2');
	assert.equal(D.ledger.offer(1).state, 'canceled');
});

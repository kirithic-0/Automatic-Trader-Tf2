// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {Ledger} = require('../lib/ledger');
const {reconcile} = require('../lib/reconcile');

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'reconcile-')), 'agent.db');
const pick = item => ({item, quality: 6, variant: 0, label: item, pass_id: 300, p_up: 0.8, exp_ret: 0.03, ask1: 0.5});
const offer = (total, assetid) => ({partner: '1', trade_url: 'https://steamcommunity.com/tradeoffer/new/?partner=1&token=t',
	price: {keys: 0, ref: '30', total_keys: total}, receive: [{appid: 440, contextid: '2', assetid}],
	give: [{appid: 440, contextid: '2', assetid: 'r1'}]});

// One of our offers as Steam lists it: matches offer(_, assetid) to partner 1.
const listed = (id, assetid, extra = {}) => ({id, state: 'Accepted', partner: '1', give: ['r1'], receive: [assetid],
	created: new Date().toISOString(), ...extra});

// Steam as the agent left it: offer id -> state; cancel moves Active -> `onCancel`.
function fakeSteam(states, {sent = [], onCancel = {}} = {}) {
	let calls = [];
	return {
		calls,
		async state(id) { return {state: states[id]}; },
		async cancel(id) { calls.push(['cancel', id]); states[id] = onCancel[id] || 'Canceled'; },
		async received(id) { return [{assetid: `a${id}`, new_assetid: `new${id}`}]; },
		async sentSince() { return sent; },
	};
}

const deps = (ledger, steam) => ({ledger, steam, log: () => {}, sleep: async () => {}});

test('offers that filled, ended or are still out while the agent was off', async () => {
	let L = new Ledger(tmp());
	let mk = (item, sid) => {
		let r = L.openBuy({mode: 'live', pick: pick(item), offer: offer(0.5, `a${sid}`)});
		L.setOffer(r.offerId, 'sent', {steamOfferId: sid});
		L.setOffer(r.offerId, 'active');
		return r;
	};
	let filled = mk('Filled', '11'), declined = mk('Declined', '12'), out = mk('StillOut', '13'), race = mk('Race', '14');
	let steam = fakeSteam({11: 'Accepted', 12: 'Declined', 13: 'Active', 14: 'Active'}, {onCancel: {14: 'Accepted'}});
	assert.equal(await reconcile(deps(L, steam)), 4);

	let p = L.position(filled.positionId);
	assert.deepEqual([p.state, p.asset_id], ['open', 'new11'], 'a fill while off: held, with its new asset id');
	assert.equal(L.position(declined.positionId).state, 'abandoned');
	assert.equal(L.position(out.positionId).state, 'abandoned', 'still out: canceled, the buy dropped');
	assert.equal(L.position(race.positionId).state, 'open', 'accepted as it was canceled: a fill');
	assert.deepEqual(steam.calls, [['cancel', '13'], ['cancel', '14']]);
	assert.equal(L.openOffers().length, 0);
	assert.equal(await reconcile(deps(L, steam)), 0, 'nothing left to check');
});

test('stopped around the send: found by partner + asset ids, or never sent', async () => {
	let L = new Ledger(tmp());
	let sent = L.openBuy({mode: 'live', pick: pick('Sent'), offer: offer(0.5, 'a21')});
	let never = L.openBuy({mode: 'live', pick: pick('Never'), offer: offer(0.5, 'a22')});
	let steam = fakeSteam({21: 'Accepted'}, {sent: [listed('21', 'a21'),
		listed('29', 'a22', {partner: '2'}), listed('28', 'a22', {give: ['r9']})]});   // other partner / other assets: not it
	await reconcile(deps(L, steam));
	assert.equal(L.offer(sent.offerId).steam_offer_id, '21');
	assert.equal(L.position(sent.positionId).state, 'open');
	assert.equal(L.offer(never.offerId).state, 'canceled');
	assert.equal(L.position(never.positionId).state, 'abandoned');
});

test('a sale that filled while off closes the position; a held item gets its missing asset id', async () => {
	let L = new Ledger(tmp());
	let b = L.openBuy({mode: 'live', pick: pick('Hat'), offer: offer(0.5, 'a31')});
	L.setOffer(b.offerId, 'sent', {steamOfferId: '31'});
	L.setOffer(b.offerId, 'accepted');                                  // asset id not read at the fill
	let other = L.openBuy({mode: 'live', pick: pick('Cap'), offer: offer(0.5, 'a32')});
	L.setOffer(other.offerId, 'sent', {steamOfferId: '32'});
	L.setOffer(other.offerId, 'accepted', {assetId: 'x'});
	let s = L.openSell({positionId: other.positionId, offer: offer(0.53, 'x')});
	L.setOffer(s.offerId, 'sent', {steamOfferId: '33'});
	L.setOffer(s.offerId, 'active');
	await reconcile(deps(L, fakeSteam({33: 'Accepted'})));
	assert.equal(L.position(b.positionId).asset_id, 'new31');
	let p = L.position(other.positionId);
	assert.deepEqual([p.state, p.exit_reason], ['closed', 'take_profit']);
	assert.ok(Math.abs(p.pnl_keys - 0.03) < 1e-12);
});

test('a trade hold is left alone; a position with no offer out is given up', async () => {
	let L = new Ledger(tmp());
	let held = L.openBuy({mode: 'live', pick: pick('Held'), offer: offer(0.5, 'a41')});
	L.setOffer(held.offerId, 'sent', {steamOfferId: '41'});
	L.setOffer(held.offerId, 'active');
	let orphan = L.openBuy({mode: 'live', pick: pick('Orphan'), offer: offer(0.5, 'a42')});
	L.setOffer(orphan.offerId, 'failed', {final: false});              // stopped before the next bot
	let steam = fakeSteam({41: 'InEscrow'});
	await reconcile(deps(L, steam));
	assert.equal(L.offer(held.offerId).state, 'escrow');
	assert.equal(L.position(held.positionId).state, 'opening');
	assert.equal(L.position(orphan.positionId).state, 'abandoned');
	assert.equal(steam.calls.length, 0);
});

test('stopped around the send, PC off for days: the search starts before the offer was built', async () => {
	let L = new Ledger(tmp());
	let r = L.openBuy({mode: 'live', pick: pick('Old'), offer: offer(0.5, 'a31')});
	let built = new Date(Date.now() - 5 * 86400e3).toISOString();
	L.db.prepare('UPDATE offers SET created_at = ? WHERE id = ?').run(built, r.offerId);
	let steam = fakeSteam({31: 'Accepted'}, {sent: [listed('31', 'a31')]});
	let asked = [];
	let since = steam.sentSince;
	steam.sentSince = async d => { asked.push(d); return since(d); };
	await reconcile(deps(L, steam));
	assert.ok(asked[0] <= new Date(Date.parse(built) - 3600e3), `searched from ${asked[0].toISOString()}`);
	assert.equal(L.position(r.positionId).state, 'open', 'it filled while off: held');
});

test('stopped around the send: an earlier identical offer already in the ledger is not it', async () => {
	let L = new Ledger(tmp());
	let r = L.openBuy({mode: 'live', pick: pick('Again'), offer: offer(0.5, 'a51')});
	L.setOffer(r.offerId, 'sent', {steamOfferId: '50'});
	L.setOffer(r.offerId, 'canceled', {final: false});                 // the same bot, tried before
	let again = L.addOffer({positionId: r.positionId, offer: offer(0.5, 'a51')});
	let t = Date.now();
	let steam = fakeSteam({50: 'Canceled', 51: 'Accepted'}, {sent: [
		listed('50', 'a51', {state: 'Canceled', created: new Date(t - 60e3).toISOString()}),
		listed('51', 'a51', {created: new Date(t).toISOString()})]});
	await reconcile(deps(L, steam));
	assert.equal(L.offer(again.offerId).steam_offer_id, '51');
	assert.equal(L.position(r.positionId).state, 'open');
});

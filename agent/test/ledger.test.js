// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {execFileSync} = require('child_process');
const {Ledger} = require('../lib/ledger');

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'ledger-')), 'agent.db');

const pick = {item: 'Mr. Juice', quality: 6, variant: 0, label: 'Mr. Juice (Unique)', pass_id: 202,
	exp_ret: 0.0229, p_up: 0.46, ask1: 1.6487};
const offer = (kind, total, assetid = '17656752805') => ({
	partner: 'S0000000000000001', trade_url: 'https://steamcommunity.com/tradeoffer/new/?partner=1&token=t',
	listing_id: `440_${assetid}`, price: {keys: 1, ref: '41.88', total_keys: total},
	receive: [{appid: 440, contextid: '2', assetid}], give: [{appid: 440, contextid: '2', assetid: 'k1'}],
});

test('a buy that fills, then a take-profit sale: the whole life of a position', () => {
	let L = new Ledger(tmp());
	let {positionId, offerId} = L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487), watchId: 77});
	assert.equal(L.position(positionId).state, 'opening');
	assert.equal(L.offer(offerId).watch_id, 77);
	assert.ok(Math.abs(L.position(positionId).take_profit_at - 1.6487 * 1.04) < 1e-12);
	assert.deepEqual([...L.reservedAssets()], ['k1']);

	L.setOffer(offerId, 'sent', {steamOfferId: '7000001'});
	L.setOffer(offerId, 'accepted', {assetId: 'our-new-id'});
	let p = L.position(positionId);
	assert.equal(p.state, 'open');
	assert.equal(p.asset_id, 'our-new-id');
	assert.equal(L.offer(offerId).steam_offer_id, '7000001');
	assert.deepEqual(L.events(offerId).map(e => e.state), ['built', 'sent', 'accepted']);
	assert.deepEqual([...L.reservedAssets()], ['k1'], 'given in an accept 30 min ago or less: still reserved (a lagging inventory)');
	assert.equal(L.reservedAssets(0).size, 0, 'and free after');

	let sell = L.openSell({positionId, offer: offer('sell', 1.72)});
	assert.equal(L.position(positionId).state, 'closing');
	L.setOffer(sell.offerId, 'sent');
	L.setOffer(sell.offerId, 'accepted', {exitReason: 'take_profit'});
	p = L.position(positionId);
	assert.equal(p.state, 'closed');
	assert.equal(p.exit_reason, 'take_profit');
	assert.ok(Math.abs(p.pnl_keys - (1.72 - 1.6487)) < 1e-12);
	assert.equal(L.held().length, 0);
	assert.equal(L.lastPass('live'), 202);
	assert.equal(L.lastPass('paper'), null);
	L.close();
});

test('a declined buy abandons the position; a declined sell reopens it', () => {
	let L = new Ledger(tmp());
	let a = L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487)});
	L.setOffer(a.offerId, 'sent');
	L.setOffer(a.offerId, 'active');
	L.setOffer(a.offerId, 'declined', {detail: 'listing gone'});
	assert.equal(L.position(a.positionId).state, 'abandoned');
	assert.equal(L.offer(a.offerId).reason, 'listing gone');
	assert.equal(L.isHeld('Mr. Juice', 6, 0), false);

	let b = L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487)});
	L.setOffer(b.offerId, 'sent');
	L.setOffer(b.offerId, 'accepted');
	let s = L.openSell({positionId: b.positionId, offer: offer('sell', 1.6)});
	L.setOffer(s.offerId, 'sent');
	L.setOffer(s.offerId, 'declined');
	assert.equal(L.position(b.positionId).state, 'open');
	L.close();
});

test('a bot that does not answer: the next bot on the same position, the entry set at the fill', () => {
	let L = new Ledger(tmp());
	let {positionId, offerId} = L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487)});
	L.setOffer(offerId, 'sent', {steamOfferId: '1'});
	L.setOffer(offerId, 'active');
	L.setOffer(offerId, 'canceled', {detail: 'no answer in 30 s', final: false});
	assert.equal(L.position(positionId).state, 'opening', 'not final: the position waits for the next bot');
	assert.throws(() => L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6)}), /already held/);
	let next = L.addOffer({positionId, offer: offer('buy', 1.6402, '999')});
	assert.throws(() => L.addOffer({positionId, offer: offer('buy', 1.6)}), /has an open offer/);
	L.setOffer(next.offerId, 'sent', {steamOfferId: '2'});
	L.setOffer(next.offerId, 'active');
	L.setOffer(next.offerId, 'accepted', {assetId: 'new'});
	let p = L.position(positionId);
	assert.equal(p.state, 'open');
	assert.equal(p.entry_keys, 1.6402, 'the entry is the offer that filled');
	assert.ok(Math.abs(p.take_profit_at - 1.6402 * 1.04) < 1e-12);
	assert.ok(Math.abs(Date.parse(p.horizon_at) - Date.now() - 48 * 3600e3) < 60e3, 'the 48 h run from the fill');

	let s1 = L.openSell({positionId, offer: offer('sell', 1.71)});
	L.setOffer(s1.offerId, 'sent');
	L.setOffer(s1.offerId, 'failed', {detail: 'their inventory changed', final: false});
	assert.equal(L.position(positionId).state, 'closing');
	L.giveUp(positionId, 'three bots, no answer');
	assert.equal(L.position(positionId).state, 'open', 'a sale given up goes back to open');

	let other = L.openBuy({mode: 'live', pick: {...pick, item: 'Other'}, offer: offer('buy', 0.5)});
	L.setOffer(other.offerId, 'failed', {final: false});
	L.giveUp(other.positionId, 'no bot answered');
	assert.equal(L.position(other.positionId).state, 'abandoned');
	assert.throws(() => L.giveUp(other.positionId, 'x'), /nothing to give up/);
	L.close();
});

test('recentSales: the newest sales first, from a time on', () => {
	let L = new Ledger(tmp());
	for (let [item, exit] of [['A', 1.1], ['B', 0.9], ['C', 1.2]]) {
		let r = L.openBuy({mode: 'live', pick: {...pick, item}, offer: offer('buy', 1)});
		L.setOffer(r.offerId, 'sent');
		L.setOffer(r.offerId, 'accepted', {assetId: item});
		let s = L.openSell({positionId: r.positionId, offer: offer('sell', exit)});
		L.setOffer(s.offerId, 'sent');
		L.setOffer(s.offerId, 'accepted');
	}
	assert.deepEqual(L.recentSales('live', 2).map(r => r.item), ['C', 'B']);
	assert.equal(L.recentSales('live', 20, '2999-01-01').length, 0);
	L.close();
});

test('the ledger refuses what cannot happen', () => {
	let L = new Ledger(tmp());
	let a = L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487)});
	assert.throws(() => L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487)}), /already held/);
	assert.throws(() => L.setOffer(a.offerId, 'accepted'), /built -> accepted not allowed/);
	assert.throws(() => L.openSell({positionId: a.positionId, offer: offer('sell', 2)}), /opening -> closing not allowed/);
	assert.equal(L.db.prepare('SELECT count(*) n FROM offers').get().n, 1, 'a refused sell leaves no offer row');
	L.setOffer(a.offerId, 'canceled');
	assert.throws(() => L.setOffer(a.offerId, 'sent'), /canceled -> sent not allowed/);
	assert.throws(() => L.openBuy({mode: 'demo', pick: {...pick, item: 'X'}, offer: offer('buy', 1)}), /CHECK/);
	L.close();
});

test('Python reads what is held: engine picks --ledger', () => {
	let file = tmp();
	let L = new Ledger(file);
	L.openBuy({mode: 'live', pick, offer: offer('buy', 1.6487)});
	let gone = L.openBuy({mode: 'live', pick: {...pick, item: 'Gone'}, offer: offer('buy', 1)});
	L.setOffer(gone.offerId, 'canceled');
	L.close();
	let backpack = path.join(__dirname, '..', '..', 'backpack');
	let out;
	try {
		out = execFileSync('python', ['-W', 'ignore', '-c',
			'import sys, json; from engine.screens.picks import ledger_held; ' +
			'print(json.dumps(sorted(ledger_held(sys.argv[1]))))', file], {cwd: backpack, encoding: 'utf8'});
	} catch (err) {
		if (err.code === 'ENOENT') return;            // no python here: nothing to check
		throw err;
	}
	assert.deepEqual(JSON.parse(out), [['Mr. Juice', 6, 0]]);
});

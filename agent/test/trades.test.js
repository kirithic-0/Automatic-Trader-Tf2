// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {Ledger} = require('../lib/ledger');
const {trades, fromLog} = require('../lib/trades');

const LOG = [
	'2026-10-28T22:21:42.363Z   BOUGHT Unknown Warrior (Unique) at 0.1256 from Cookie Bot [⇄] 24/7 (p_up 0.81)',
	'2026-10-29T06:58:38.372Z   SOLD Unknown Warrior (take_profit) at 0.1307 to 💠 DOLPHIN | CTradeTF: 💠: +0.0051 keys',
	'2026-10-29T08:00:00.000Z   hold Hat: under the take-profit (best tradable bid 0.1084 < 0.1217)',
	'2026-10-29T08:10:00.000Z   hold Hat: under the take-profit (best tradable bid 0.1100 < 0.1217)',
];

test('fromLog: bot names with brackets, colons and emoji; the newest hold line per item', () => {
	let l = fromLog(LOG);
	assert.deepEqual(l.bought.map(b => [b.item, b.who]), [['Unknown Warrior', 'Cookie Bot [⇄] 24/7']]);
	assert.deepEqual(l.sold.map(s => [s.item, s.who]), [['Unknown Warrior', '💠 DOLPHIN | CTradeTF: 💠']]);
	assert.equal(l.hold.get('Hat').bid, 0.11);
});

test('trades: a sale with both bots named, a held item with its bid, a buy that never filled', () => {
	let L = new Ledger(path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trades-')), 'a.db'));
	let offer = (t, a) => ({partner: '765', price: {keys: 0, ref: '7', total_keys: t}, receive: [{appid: 440, contextid: '2', assetid: a}], give: []});
	let pick = item => ({item, quality: 6, variant: 0, label: item, pass_id: 1, p_up: 0.8, exp_ret: 0.03, ask1: 0.13});
	let at = iso => L.db.prepare('UPDATE offers SET updated_at = ? WHERE id = (SELECT max(id) FROM offers)').run(iso);
	let w = L.openBuy({mode: 'live', pick: pick('Unknown Warrior'), offer: offer(0.1256, 'a')});
	L.setOffer(w.offerId, 'sent'); L.setOffer(w.offerId, 'accepted', {assetId: 'x'}); at('2026-10-28T22:21:40.000Z');
	let s = L.openSell({positionId: w.positionId, offer: offer(0.1307, 'x')});
	L.setOffer(s.offerId, 'sent'); L.setOffer(s.offerId, 'accepted', {exitReason: 'take_profit'}); at('2026-10-29T06:58:37.000Z');
	L.db.prepare('UPDATE positions SET closed_at = ? WHERE id = ?').run('2026-10-29T06:58:37.000Z', w.positionId);
	let h = L.openBuy({mode: 'live', pick: pick('Hat'), offer: offer(0.117, 'b')});
	L.setOffer(h.offerId, 'sent'); L.setOffer(h.offerId, 'accepted', {assetId: 'y'});
	let g = L.openBuy({mode: 'live', pick: pick('Gone'), offer: offer(0.2, 'c')});
	L.setOffer(g.offerId, 'canceled');
	let [sold, held, gone] = trades(L, LOG);
	assert.deepEqual([sold.state, sold.seller, sold.buyer, +sold.ret.toFixed(4)], ['closed', 'Cookie Bot [⇄] 24/7', '💠 DOLPHIN | CTradeTF: 💠', 0.0406]);
	assert.ok(Math.abs(sold.held_h - 8.6153) < 1e-3);
	assert.deepEqual([held.state, held.bid, held.seller], ['open', 0.11, '765'], 'no BOUGHT line: the Steam id');
	assert.deepEqual([gone.state, gone.seller, gone.held_h], ['abandoned', null, null]);
	L.close();
});

test('trades: a Unique and a Strange of one item, both held, each get their own hold line', () => {
	let L = new Ledger(path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trades-')), 'a.db'));
	let offer = (t, a) => ({partner: '765', price: {keys: 0, ref: '7', total_keys: t}, receive: [{appid: 440, contextid: '2', assetid: a}], give: []});
	let hold = (quality, t, a) => {
		let w = L.openBuy({mode: 'live', pick: {item: 'Sophisticated Smoker', quality, variant: 0, label: 'x', pass_id: 1, p_up: 0.7, exp_ret: 0.03, ask1: t}, offer: offer(t, a)});
		L.setOffer(w.offerId, 'sent'); L.setOffer(w.offerId, 'accepted', {assetId: a});
	};
	hold(11, 0.4526, 's');
	hold(6, 0.2014, 'u');
	let lines = [
		'2026-11-01T09:06:07.467Z   hold Sophisticated Smoker (Strange): under the take-profit (best tradable bid 0.4595 < 0.4707)',
		'2026-11-01T09:06:07.468Z   hold Sophisticated Smoker (Unique): under the take-profit (best tradable bid 0.2048 < 0.2095)',
	];
	let [strange, unique] = trades(L, lines);
	assert.deepEqual([strange.label, strange.bid], ['Sophisticated Smoker (Strange)', 0.4595]);
	assert.deepEqual([unique.label, unique.bid], ['Sophisticated Smoker (Unique)', 0.2048]);
	L.close();
});

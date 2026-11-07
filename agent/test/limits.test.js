// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {Ledger} = require('../lib/ledger');
const limits = require('../lib/limits');

const dir = () => fs.mkdtempSync(path.join(os.tmpdir(), 'limits-'));
const cfg = {max_open_positions: 2, max_keys_in_play: 3, max_trade_keys: 2,
	max_new_buys_per_pass: 10, win_window: 4, min_win_rate: 0.75, max_tries: 3, bot_wait_s: 30, poll_ms: 3000,
	approve_wait_s: 900};
const offer = t => ({partner: '1', price: {keys: 0, ref: '0', total_keys: t},
	receive: [{appid: 440, contextid: '2', assetid: 'a'}], give: []});
const buy = (L, item, t) => {
	let r = L.openBuy({mode: 'live', pick: {item, quality: 6, variant: 0, ask1: t}, offer: offer(t)});
	L.setOffer(r.offerId, 'sent');
	L.setOffer(r.offerId, 'accepted');
	return r.positionId;
};
const sell = (L, id, t) => {
	let s = L.openSell({positionId: id, offer: offer(t)});
	L.setOffer(s.offerId, 'sent');
	L.setOffer(s.offerId, 'accepted', {exitReason: 'horizon'});
};
const files = d => ({stopFiles: [path.join(d, 'STOP')], resumedFile: path.join(d, 'RESUMED')});

test('limits: open positions, keys in play, trade size, per pass', () => {
	let d = dir(), L = new Ledger(path.join(d, 'a.db'));
	let ok = p => limits.allowBuy({ledger: L, mode: 'live', priceKeys: p, cfg, ...files(d)});
	assert.equal(ok(1).ok, true);
	assert.match(ok(2.5).reason, /max_trade_keys/);
	buy(L, 'A', 1.5);
	assert.match(ok(1.6).reason, /keys in play/);           // 1.5 + 1.6 > 3
	assert.equal(ok(1.5).ok, true);
	buy(L, 'B', 1.0);
	assert.match(ok(0.1).reason, /max_open_positions/);
	assert.deepEqual(L.exposure('live'), {open: 2, keys: 2.5});
	assert.match(limits.allowBuy({ledger: L, mode: 'live', priceKeys: 1, cfg, boughtThisPass: 10, ...files(d)}).reason,
		/new buys a pass/);
	L.close();
});

test('the win-rate stop: < 75% of the last 4 sales won stops buys; resume starts a new window', () => {
	let d = dir(), L = new Ledger(path.join(d, 'a.db'));
	let c = {...cfg, max_open_positions: 100, max_keys_in_play: 100};
	let ok = () => limits.allowBuy({ledger: L, mode: 'live', priceKeys: 0.1, cfg: c, ...files(d)});
	for (let [item, exit] of [['A', 1.1], ['B', 1.1], ['C', 0.9]]) sell(L, buy(L, item, 1.0), exit);
	assert.equal(ok().ok, true, 'three sales: not a full window yet');
	sell(L, buy(L, 'D', 1.0), 0.95);                        // 2 of 4 won
	assert.match(ok().reason, /only 50% of the last 4 sales won/);
	assert.match(limits.allowSell({stopFiles: files(d).stopFiles}).ok ? 'sells go on' : '', /sells go on/);
	for (let [item, exit] of [['E', 1.1], ['F', 1.1], ['G', 1.1]]) sell(L, buy(L, item, 1.0), exit);   // 3 of the last 4 won
	assert.match(ok().reason, /only 50%/, 'it stays stopped although the last 4 now pass: resumed by hand only');
	limits.resume(files(d).resumedFile, new Date(Date.now() + 1000), path.join(d, 'BUYS_PAUSED'));   // after the last sale
	assert.equal(ok().ok, true, 'after resume only new sales count');
	L.close();
});

test('the kill switch stops buys and sells; limits never stop a sell', () => {
	let d = dir(), L = new Ledger(path.join(d, 'a.db'));
	let {stopFiles} = files(d);
	assert.equal(limits.allowSell({stopFiles}).ok, true);
	limits.stop('a trade hold', stopFiles);
	assert.match(limits.allowSell({stopFiles}).reason, /kill switch/);
	assert.match(fs.readFileSync(stopFiles[0], 'utf8'), /a trade hold/);
	assert.match(limits.allowBuy({ledger: L, mode: 'live', priceKeys: 0.1, cfg, ...files(d)}).reason, /kill switch/);
	L.close();
});

test('limits.json loads and is checked', () => {
	let c = limits.load();
	assert.ok(c.max_keys_in_play >= 0, 'the live file: a keys-in-play limit (the user tunes it; 0 = buying off, as since 2026-11-07)');
	assert.equal(c.bot_wait_s, 30);
	let bad = path.join(dir(), 'l.json');
	fs.writeFileSync(bad, JSON.stringify({...cfg, max_trade_keys: 'two'}));
	assert.throws(() => limits.load(bad), /max_trade_keys/);
});

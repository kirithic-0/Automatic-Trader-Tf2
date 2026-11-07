// node --test   (from agent/)
// The panel's levers: requests to the running agent, limits set and re-read, held positions
// changed by hand (and a sell-now recorded as 'manual'), and the snapshot the panel reads.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const control = require('../lib/control');
const limits = require('../lib/limits');
const {Ledger} = require('../lib/ledger');
const {cycle} = require('../lib/cycle');
const {agentState, bots, funnel, snapshot} = require('../lib/snapshot');
const {trades} = require('../lib/trades');

const dir = () => fs.mkdtempSync(path.join(os.tmpdir(), 'control-'));
const offer = (t, partner = '1') => ({partner, trade_url: `https://steamcommunity.com/tradeoffer/new/?partner=${partner}&token=t`,
	price: {keys: 0, ref: '30', total_keys: t}, receive: [{appid: 440, contextid: '2', assetid: 'a' + t}], give: []});
function held(L, item, t, partner = '1') {
	let r = L.openBuy({mode: 'live', pick: {item, quality: 6, variant: 0, ask1: t, pass_id: 1}, offer: offer(t, partner)});
	L.setOffer(r.offerId, 'sent', {steamOfferId: 's' + r.offerId});
	L.setOffer(r.offerId, 'active');
	L.setOffer(r.offerId, 'accepted', {assetId: 'ours-' + item});
	return r.positionId;
}

test('requests: asked, listed, taken once, taken back; the wait wakes on one', async () => {
	let d = dir();
	assert.deepEqual(control.pending(d), {});
	control.request('restart', 'from a test', d);
	assert.match(control.pending(d).restart, /from a test$/);
	assert.equal(await control.wait(60e3, async () => {}, d), 'restart', 'the wait ends at once');
	assert.match(control.take('restart', d), /from a test/);
	assert.equal(control.take('restart', d), null, 'taken once');
	control.request('cycle', 'x', d);
	assert.equal(control.cancel('cycle', d), true);
	assert.equal(control.cancel('cycle', d), false);
	let slept = 0;
	assert.equal(await control.wait(5000, async ms => { slept += ms; }, d, 2000), 'timeout');
	assert.equal(slept, 5000, 'no request: the whole wait');
	control.request('cycle', 'a', d);
	control.request('shutdown', 'b', d);
	assert.equal(await control.wait(1000, async () => {}, d), 'shutdown', 'shutdown wins over a cycle');
	assert.throws(() => control.request('reboot', 'x', d), /unknown request/);
});

test('limits: set checks first and keeps the note; the agent re-reads them, a broken file keeps the last good', () => {
	let d = dir(), f = path.join(d, 'limits.json');
	fs.copyFileSync(path.join(__dirname, '..', 'limits.json'), f);
	let cfg = limits.load(f);
	let r = limits.set('max_keys_in_play', '3', f);
	assert.deepEqual([r.old, r.value], [cfg.max_keys_in_play, 3]);
	assert.ok(JSON.parse(fs.readFileSync(f, 'utf8'))._note, 'the note stays');
	assert.throws(() => limits.set('min_win_rate', 7, f), /a share/);
	assert.throws(() => limits.set('max_keys_in_play', -1, f), />= 0/);
	assert.throws(() => limits.set('max_loss', 1, f), /unknown limit/);
	assert.equal(limits.load(f).max_keys_in_play, 3, 'a refused set leaves the file as it was');
	let lines = [];
	let next = limits.reloaded(cfg, l => lines.push(l), f);
	assert.equal(next.max_keys_in_play, 3);
	assert.deepEqual(lines, [`limits changed: max_keys_in_play ${cfg.max_keys_in_play} -> 3`]);
	fs.writeFileSync(f, '{ not json');
	assert.equal(limits.reloaded(next, l => lines.push(l), f), next, 'unreadable: the last good limits');
	assert.match(lines[1], /not read/);
});

test('a held position by hand: take-profit, horizon, sell-now; only while it is held; every change kept', () => {
	let L = new Ledger(path.join(dir(), 'a.db'));
	let id = held(L, 'Hat', 0.5);
	let p = L.setTakeProfit(id, 0.06, 'test');
	assert.ok(Math.abs(p.take_profit_at - 0.53) < 1e-12);
	assert.throws(() => L.setTakeProfit(id, 0), /over 0/);
	assert.throws(() => L.setTakeProfit(id, 2), /up to 1/);
	let before = Date.now();
	p = L.setHorizon(id, 12);
	assert.ok(Math.abs(Date.parse(p.horizon_at) - (before + 12 * 3600e3)) < 5000);
	p = L.setHorizon(id, null);
	assert.equal(p.horizon_at, new Date(Date.parse(p.opened_at) + 48 * 3600e3).toISOString(), 'reset = bought + 48 h');
	assert.throws(() => L.setHorizon(id, 400), /0 to 336/);
	assert.equal(L.soldByHand(id), false);
	L.sellNow(id, 'the panel');
	assert.ok(Date.parse(L.position(id).horizon_at) <= Date.now());
	assert.equal(L.soldByHand(id), true);
	L.setHorizon(id, 24);
	assert.equal(L.soldByHand(id), false, 'a later horizon takes the sell-now back');
	assert.deepEqual(L.positionEvents(id).map(e => e.what), ['take_profit', 'horizon', 'horizon', 'sell_now', 'horizon']);
	assert.equal(L.positionEvents(id)[3].detail, 'the panel');
	L.openSell({positionId: id, offer: offer(0.52)});
	assert.throws(() => L.sellNow(id), /closing: only a held item/);
});

test("sell-now: the next cycle's sale is recorded as exit 'manual'", async () => {
	let d = dir(), L = new Ledger(path.join(d, 'a.db'));
	let id = held(L, 'Hat', 0.5);
	L.sellNow(id);
	let bid = {side: 'buy', steam_id: '11', user: 'bot11', trade_url: 'https://steamcommunity.com/tradeoffer/new/?partner=11&token=t',
		listing_id: '440_11_h', asset_id: null, keys: 0, ref: 29, total_keys: 0.4833};
	let metal = (n, tag) => Array.from({length: n}, (_, i) => ({assetid: tag + i, market_hash_name: 'Refined Metal', tradable: true}));
	let D = {ledger: L, ledgerPath: path.join(d, 'a.db'), target: 't', log: () => {}, sleep: async () => {},
		cfg: {max_open_positions: 9, max_keys_in_play: 9, max_trade_keys: 1, max_new_buys_per_pass: 10, win_window: 20,
			min_win_rate: 0.75, max_tries: 3, bot_wait_s: 30, poll_ms: 3000, approve_wait_s: 900},
		approve: async () => true, inventory: async () => metal(40, 't'), stopFiles: [path.join(d, 'STOP')], resumedFile: path.join(d, 'R'),
		engine: async screen => ({code: 0, data: {
			exits: [{position_id: id, item: 'Hat', action: 'sell', why: 'horizon', min_keys: 0, listing: bid}],
			recheck: {ok: true, listing: bid, candidates: [bid]}, picks: {stale: true, picks: []}}[screen]}),
		steam: {async holdDays() { return {me: 0, them: 0}; }, async send() { return '1'; }, async confirm() {},
			async state() { return {state: 'Accepted'}; }, async cancel() {}, async received() { return []; },
			async sentSince() { return []; }, async incoming() { return []; }, async decline() {},
			async inventory() { return [{assetid: 'ours-Hat', market_hash_name: 'Hat', tradable: true}, ...metal(5, 'o')]; }}};
	let r = await cycle(D, {lastPassId: null});
	assert.equal(r.sold, 1);
	assert.deepEqual([L.position(id).state, L.position(id).exit_reason], ['closed', 'manual']);
});

test('the snapshot: state from the log, bots and funnel from the offers', () => {
	let L = new Ledger(path.join(dir(), 'a.db'));
	held(L, 'Hat', 0.5, '7');
	held(L, 'Cap', 0.2, '7');
	let r = L.openBuy({mode: 'live', pick: {item: 'Mask', quality: 6, variant: 0, ask1: 0.3}, offer: offer(0.3, '8')});
	L.setOffer(r.offerId, 'sent', {steamOfferId: 'x'});
	L.setOffer(r.offerId, 'canceled', {final: false});
	L.giveUp(r.positionId, 'no answer');
	let lines = [
		'2026-10-30T08:00:00.000Z AUTO MODE: no approvals -- every trade inside limits.json goes out on its own',
		'2026-10-30T08:00:00.001Z agent: ledger x, every 600 s, limits {}',
		'2026-10-30T08:00:05.000Z   BOUGHT Hat (Unique) at 0.5000 from Kind Bot (p_up 0.80)',
		'2026-10-30T08:01:00.000Z cycle: sold 0, bought 1; holding 1 (0.5000 keys)',
		'2026-10-30T08:01:00.001Z next cycle in 540 s (close the window to stop)'];
	let st = agentState(lines, 1234, pid => pid === 1234);
	assert.deepEqual([st.running, st.mode, st.started_at, st.last_cycle.text, st.next_cycle_at],
		[true, 'auto', '2026-10-30T08:00:00.001Z', 'cycle: sold 0, bought 1; holding 1 (0.5000 keys)', '2026-10-30T08:10:00.001Z']);
	assert.equal(agentState(lines, 1234, () => false).running, false);
	let busy = agentState([...lines, '2026-10-30T08:10:01.000Z pull: ok'], 1234, () => true);
	assert.deepEqual([busy.cycle_since, busy.next_cycle_at], ['2026-10-30T08:10:01.000Z', null], 'logging past the wait: a cycle runs');
	let f = funnel(L);
	assert.deepEqual(f.buy, {offers: 3, reached_steam: 3, accepted: 2, declined: 0, no_answer: 1, failed: 0, open: 0});
	let b = bots(L, trades(L, []));
	assert.deepEqual(b.map(x => [x.id, x.offers, x.accepted, x.no_answer, x.accept_rate]), [['7', 2, 2, 0, 1], ['8', 1, 0, 1, 0]]);
	assert.ok(b[0].median_accept_s >= 0, 'active -> accepted timed');
	let s = snapshot({ledger: L, lines, cfg: {max_keys_in_play: 2, _note: 'x'}, pid: 1, isAlive: () => true,
		kill: 'stopped: test', pending: {cycle: 'now'}});
	assert.deepEqual([s.state.kill, s.state.pending, s.limits, s.stats.held, s.rows.length, s.log.length],
		['stopped: test', {cycle: 'now'}, {max_keys_in_play: 2}, 2, 3, 5]);
	assert.doesNotThrow(() => JSON.stringify(s));
});

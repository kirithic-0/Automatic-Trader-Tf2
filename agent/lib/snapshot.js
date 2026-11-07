// The live agent as data, for `engine panel`'s Agent tab (agent/panel.js prints it as JSON)
// and `agent status` (status.js prints it as text). Read-only.
//
//   agentState(lines, pid, isAlive) -> {running, pid, mode, started_at, last_line_at,
//       last_cycle: {at, text}, next_cycle_at, cycle_since}   from the log's last lines + the
//       lock; cycle_since = when the cycle now running began (it logged past next_cycle_at)
//   bots(ledger, rows, mode) -> [{id, name, offers, accepted, declined, no_answer, failed, open,
//       accept_rate, median_accept_s, bought_from, sold_to}]   every counterparty, by offers
//   funnel(ledger, mode) -> {buy: {...}, sell: {...}}  offers by how they ended
//   snapshot({ledger, lines, mode, cfg, pid, isAlive, kill, paused, resumedAt, pending})
const {trades} = require('./trades');
const {stats, BACKTEST} = require('./report');

const ts = line => line.slice(0, 24);
const lastOf = (lines, re) => { for (let i = lines.length - 1; i >= 0; i--) if (re.test(lines[i])) return i; return -1; };

function agentState(lines, pid, isAlive) {
	let start = lastOf(lines, /agent: ledger /);
	let c = lastOf(lines, /cycle: sold|cycle failed|STOPPED:/);
	let next = lastOf(lines, /next cycle in \d+ s/);
	let nextAt = null, since = null;
	if (next >= 0 && next > c - 1) nextAt = new Date(Date.parse(ts(lines[next])) + Number(lines[next].match(/next cycle in (\d+) s/)[1]) * 1000).toISOString();
	if (next >= 0 && next < lines.length - 1 && next > c - 1) since = ts(lines[next + 1]);   // lines after the wait: a cycle runs
	return {
		running: !!(pid && isAlive(pid)), pid: pid || null,
		mode: start < 0 ? null : /AUTO MODE/.test(lines[start - 1] || '') ? 'auto' : 'manual',
		started_at: start >= 0 ? ts(lines[start]) : null,
		last_line_at: lines.length ? ts(lines[lines.length - 1]) : null,
		last_cycle: c >= 0 ? {at: ts(lines[c]), text: lines[c].slice(25).trim()} : null,
		next_cycle_at: since ? null : nextAt,
		cycle_since: since,
	};
}

const END = {accepted: 'accepted', declined: 'declined', canceled: 'no_answer', expired: 'no_answer', failed: 'failed', escrow: 'failed'};
const median = xs => { if (!xs.length) return null; let s = xs.slice().sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

function bots(ledger, rows, mode = 'live') {
	let names = new Map();
	for (let r of rows) {
		if (r.seller_id && r.seller && r.seller !== r.seller_id) names.set(r.seller_id, r.seller);
		if (r.buyer_id && r.buyer && r.buyer !== r.buyer_id) names.set(r.buyer_id, r.buyer);
	}
	let acceptS = new Map();
	for (let e of ledger.db.prepare(`SELECT o.id, e.state, e.at FROM offer_events e JOIN offers o ON o.id = e.offer_id
		WHERE o.mode = ? AND e.state IN ('active', 'accepted') ORDER BY e.id`).all(mode)) {
		let a = acceptS.get(e.id) || acceptS.set(e.id, {}).get(e.id);
		a[e.state] = Date.parse(e.at);
	}
	let by = new Map();
	for (let o of ledger.db.prepare('SELECT * FROM offers WHERE mode = ? ORDER BY id').all(mode)) {
		let b = by.get(o.partner) || by.set(o.partner, {id: o.partner, name: names.get(o.partner) || null, offers: 0,
			accepted: 0, declined: 0, no_answer: 0, failed: 0, open: 0, bought_from: 0, sold_to: 0, _s: []}).get(o.partner);
		b.offers++;
		let end = END[o.state];
		if (end) b[end]++; else b.open++;
		if (o.state === 'accepted') {
			b[o.kind === 'buy' ? 'bought_from' : 'sold_to']++;
			let a = acceptS.get(o.id);
			if (a && a.active && a.accepted) b._s.push((a.accepted - a.active) / 1000);
		}
	}
	return [...by.values()].map(({_s, ...b}) => ({...b, accept_rate: b.offers - b.open ? b.accepted / (b.offers - b.open) : null,
		median_accept_s: median(_s)})).sort((a, b) => b.offers - a.offers);
}

function funnel(ledger, mode = 'live') {
	let out = {};
	for (let kind of ['buy', 'sell']) {
		let f = {offers: 0, reached_steam: 0, accepted: 0, declined: 0, no_answer: 0, failed: 0, open: 0};
		for (let o of ledger.db.prepare('SELECT state, steam_offer_id FROM offers WHERE mode = ? AND kind = ?').all(mode, kind)) {
			f.offers++;
			if (o.steam_offer_id) f.reached_steam++;
			let end = END[o.state];
			if (end) f[end]++; else f.open++;
		}
		out[kind] = f;
	}
	return out;
}

function snapshot({ledger, lines, mode = 'live', cfg, pid = null, isAlive = () => false, kill = null, paused = null,
	resumedAt = null, pending = {}, logLines = 80}) {
	let rows = trades(ledger, lines, {mode});
	let s = stats(ledger, mode);
	let events = ledger.db.prepare(`SELECT e.*, p.item FROM position_events e JOIN positions p ON p.id = e.position_id
		WHERE p.mode = ? ORDER BY e.id DESC LIMIT 30`).all(mode);
	return {
		generated: new Date().toISOString(), mode,
		state: {...agentState(lines, pid, isAlive), kill, paused, resumed_at: resumedAt || null, pending},
		limits: cfg ? Object.fromEntries(Object.entries(cfg).filter(([k]) => !k.startsWith('_'))) : null,
		backtest: BACKTEST,
		stats: {held: s.held.length, held_keys: s.held_keys, closed: s.closed.length, abandoned: s.abandoned,
			pnl_keys: s.pnl_keys, per_trade: s.per_trade, win: s.win, hours: s.hours, by_reason: s.byReason},
		rows, bots: bots(ledger, rows, mode), funnel: funnel(ledger, mode), events,
		log: lines.slice(-logLines),
	};
}

module.exports = {agentState, bots, funnel, snapshot};

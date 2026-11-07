// Every live trade, as one row each, for `agent trades` (trades.js) and `agent dashboard`
// (dashboard.js). The ledger has the prices, times and Steam ids; the log adds what the
// ledger does not keep -- the bots' names (the BOUGHT / SOLD lines) and, for what is held,
// the latest best bid the exit rule saw (the `hold <item>: ... (best tradable bid x < y)` lines).
//
//   trades(ledger, logLines, {mode}) -> [{id, item, quality, label, state, p_up, opened_at,
//       bought_at, entry_keys, seller, sold_at, exit_keys, buyer, exit_reason, pnl_keys, ret,
//       held_h, take_profit_at, horizon_at, tries, bid, bid_at, hold_why}]
//   logTail(file, bytes) -> the log's last lines
const fs = require('fs');

const QUALITY = {6: 'Unique', 11: 'Strange'};
const ts = line => line.slice(0, 24);

function logTail(file, bytes = 2e6) {
	if (!fs.existsSync(file)) return [];
	let fd = fs.openSync(file, 'r'), size = fs.fstatSync(fd).size, n = Math.min(size, bytes);
	let buf = Buffer.alloc(n);
	fs.readSync(fd, buf, 0, n, size - n);
	fs.closeSync(fd);
	return buf.toString('utf8').split(/\r?\n/).filter(Boolean);
}

// The BOUGHT / SOLD lines, and the newest hold line per item and quality ('Hat|Strange'); a
// hold line from before 2026-11-01 names no quality and is keyed by the item alone.
function fromLog(lines) {
	let bought = [], sold = [], hold = new Map();
	for (let l of lines) {
		let m;
		if ((m = l.match(/ BOUGHT (.+) \(\w+\) at [\d.]+ from (.+) \(p_up [\d.]+\)$/))) bought.push({at: ts(l), item: m[1], who: m[2]});
		else if ((m = l.match(/ SOLD (.+?) \(\w+\) at [\d.]+ to (.+): [+-][\d.]+ keys$/))) sold.push({at: ts(l), item: m[1], who: m[2]});
		else if ((m = l.match(/^\S+ +hold (.+?)(?: \((\w+)\))?: (.+)$/))) {
			let bid = m[3].match(/best tradable bid ([\d.]+)/);
			hold.set(m[2] ? `${m[1]}|${m[2]}` : m[1], {at: ts(l), why: m[3], bid: bid ? Number(bid[1]) : null});
		}
	}
	return {bought, sold, hold};
}

// The log line for this item closest to the fill (within 10 min).
function nameAt(list, item, at) {
	if (!at) return null;
	let t = Date.parse(at), best = null;
	for (let e of list) {
		let d = Math.abs(Date.parse(e.at) - t);
		if (e.item === item && d < 600e3 && (!best || d < best.d)) best = {d, who: e.who};
	}
	return best && best.who;
}

function trades(ledger, logLines = [], {mode = 'live'} = {}) {
	let log = fromLog(logLines);
	let offers = ledger.db.prepare('SELECT * FROM offers WHERE mode = ? ORDER BY id').all(mode);
	let byPos = new Map();
	for (let o of offers) (byPos.get(o.position_id) || byPos.set(o.position_id, []).get(o.position_id)).push(o);
	let now = Date.now();
	return ledger.db.prepare('SELECT * FROM positions WHERE mode = ? ORDER BY id').all(mode).map(p => {
		let os = byPos.get(p.id) || [];
		let buy = os.find(o => o.kind === 'buy' && o.state === 'accepted');
		let sell = os.find(o => o.kind === 'sell' && o.state === 'accepted');
		let held = ['open', 'closing'].includes(p.state);
		let h = held ? log.hold.get(`${p.item}|${QUALITY[p.quality] || 'q' + p.quality}`) || log.hold.get(p.item) : null;
		let boughtAt = buy ? buy.updated_at : null;
		let end = p.state === 'closed' ? Date.parse(p.closed_at) : held ? now : null;
		return {
			id: p.id, item: p.item, quality: p.quality, label: `${p.item} (${QUALITY[p.quality] || 'q' + p.quality})`,
			state: p.state, p_up: p.p_up, opened_at: p.opened_at, bought_at: boughtAt,
			entry_keys: p.entry_keys, seller: buy ? nameAt(log.bought, p.item, boughtAt) || buy.partner : null,
			seller_id: buy ? buy.partner : null,
			sold_at: sell ? sell.updated_at : null, exit_keys: p.exit_keys,
			buyer: sell ? nameAt(log.sold, p.item, sell.updated_at) || sell.partner : null, buyer_id: sell ? sell.partner : null,
			exit_reason: p.exit_reason, pnl_keys: p.pnl_keys,
			ret: p.pnl_keys != null && p.entry_keys ? p.pnl_keys / p.entry_keys : null,
			held_h: boughtAt && end ? (end - Date.parse(boughtAt)) / 3600e3 : null,
			take_profit_at: p.take_profit_at, horizon_at: p.horizon_at,
			tries: os.filter(o => o.kind === 'buy').length,
			bid: h ? h.bid : null, bid_at: h ? h.at : null, hold_why: h ? h.why : null,
		};
	});
}

module.exports = {trades, logTail, fromLog, QUALITY};

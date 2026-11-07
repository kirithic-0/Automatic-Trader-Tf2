// Every live trade in the terminal: what is held (with the latest best bid), then every sale,
// newest first, then the buys that never filled.   node trades.js   (agent trades)
const path = require('path');
const {Ledger} = require('./lib/ledger');
const {BACKPACK} = require('./lib/python');
const {trades, logTail} = require('./lib/trades');

const k = x => x == null ? '-' : x.toFixed(4);
const pct = x => x == null ? '-' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`;
const when = iso => iso ? iso.slice(5, 16).replace('T', ' ') : '-';
const fit = (s, n) => { s = String(s ?? '-'); return [...s].length > n ? [...s].slice(0, n - 1).join('') + '…' : s + ' '.repeat(n - [...s].length); };

let ledger = new Ledger();
let rows = trades(ledger, logTail(path.join(BACKPACK, 'logs', 'agent.log')));
ledger.close();
let held = rows.filter(r => ['opening', 'open', 'closing'].includes(r.state));
let closed = rows.filter(r => r.state === 'closed').sort((a, b) => b.sold_at.localeCompare(a.sold_at));
let gone = rows.filter(r => r.state === 'abandoned');

console.log(`HOLDING ${held.length}  (times UTC)`);
console.log(`  ${fit('item', 30)} ${fit('bought', 11)} ${'paid'.padStart(7)} ${'bid now'.padStart(7)} ${'vs paid'.padStart(7)} ${'sell at'.padStart(7)}  ${'horizon'.padStart(8)}  from`);
for (let r of held) {
	let h = (Date.parse(r.horizon_at) - Date.now()) / 3600e3;
	console.log(`  ${fit(r.label, 30)} ${fit(when(r.bought_at), 11)} ${k(r.entry_keys).padStart(7)} ${k(r.bid).padStart(7)} ` +
		`${pct(r.bid != null ? r.bid / r.entry_keys - 1 : null).padStart(7)} ${k(r.take_profit_at).padStart(7)}  ` +
		`${(h >= 0 ? `${h.toFixed(1)} h` : `${(-h).toFixed(1)} h over`).padStart(8)}  ${fit(r.seller, 24)}`);
}

let won = closed.filter(r => r.pnl_keys > 0).length;
let pnl = closed.reduce((s, r) => s + r.pnl_keys, 0);
console.log(`\nSOLD ${closed.length}: ${won} in profit (${closed.length ? (won / closed.length * 100).toFixed(0) : '-'}%), ` +
	`${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)} keys`);
console.log(`  ${fit('item', 30)} ${fit('sold', 11)} ${'paid'.padStart(7)} ${'sold'.padStart(7)} ${'keys'.padStart(8)} ${'%'.padStart(6)} ${'held'.padStart(6)}  ${fit('exit', 11)} bought from -> sold to`);
for (let r of closed) {
	console.log(`  ${fit(r.label, 30)} ${fit(when(r.sold_at), 11)} ${k(r.entry_keys).padStart(7)} ${k(r.exit_keys).padStart(7)} ` +
		`${((r.pnl_keys >= 0 ? '+' : '') + r.pnl_keys.toFixed(4)).padStart(8)} ${pct(r.ret).padStart(6)} ${(r.held_h.toFixed(1) + 'h').padStart(6)}  ` +
		`${fit(r.exit_reason, 11)} ${fit(r.seller, 20)} -> ${fit(r.buyer, 20)}`);
}

if (gone.length) {
	console.log(`\nBUYS THAT NEVER FILLED ${gone.length}`);
	for (let r of gone) console.log(`  ${fit(r.label, 30)} ${fit(when(r.opened_at), 11)} ${k(r.entry_keys).padStart(7)}  ${r.exit_reason}`);
}

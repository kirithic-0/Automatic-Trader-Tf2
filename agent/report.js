// The live results so far: node report.js [--all]  (--all also lists every position)
const {Ledger} = require('./lib/ledger');
const {stats, summary} = require('./lib/report');

let ledger = new Ledger();
for (let line of summary(ledger, 'live')) console.log(line);
let s = stats(ledger, 'live');
let now = Date.now();
if (s.held.length) {
	console.log('\nholding:');
	for (let p of s.held) {
		let h = (Date.parse(p.horizon_at) - now) / 3600e3;
		console.log(`  ${p.item.padEnd(36)} q${p.quality}  entry ${p.entry_keys.toFixed(4)}  take-profit ${p.take_profit_at.toFixed(4)}  ` +
			`${h >= 0 ? `${h.toFixed(1)} h to the horizon` : `${(-h).toFixed(1)} h past the horizon`}`);
	}
}
if (process.argv.includes('--all') && s.closed.length) {
	console.log('\nclosed:');
	for (let c of s.closed) {
		console.log(`  ${c.closed_at.slice(0, 16)}  ${c.item.padEnd(36)} ${c.entry_keys.toFixed(4)} -> ${c.exit_keys.toFixed(4)}  ` +
			`${(c.pnl_keys / c.entry_keys * 100).toFixed(2)}%  ${c.exit_reason}`);
	}
}
ledger.close();

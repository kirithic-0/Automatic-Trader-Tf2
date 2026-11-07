// Change a held position by hand (the ledger's position_events keep every change). The next
// cycle's exit rule acts on it; a sale already out is not touched.
//   node position.js <id | item> sell-now          sell into the best tradable bid next cycle
//   node position.js <id | item> tp <percent>      take-profit this far over the price paid (e.g. 6)
//   node position.js <id | item> horizon <hours>   sell from this many hours from now on (0-336)
//   node position.js <id | item> horizon reset     back to the model's own: bought + 48 h
//   node position.js list                          the held positions
const {Ledger} = require('./lib/ledger');

const k4 = x => Number(x).toFixed(4);

function find(ledger, key) {
	let held = ledger.held().filter(p => p.mode === 'live');
	if (/^\d+$/.test(key)) return held.find(p => p.id === Number(key)) || ledger.position(Number(key));
	let hits = held.filter(p => p.item.toLowerCase().includes(key.toLowerCase()));
	if (hits.length === 1) return hits[0];
	throw new Error(hits.length ? `"${key}" matches ${hits.map(p => `${p.id} ${p.item}`).join(', ')}: give the id`
		: `no held position matches "${key}"`);
}

function main() {
	let [key, what, value] = process.argv.slice(2);
	let ledger = new Ledger();
	try {
		if (!key || key === 'list') {
			for (let p of ledger.held().filter(p => p.mode === 'live')) {
				console.log(`${p.id}  ${p.item} (${p.state}) paid ${k4(p.entry_keys)}, take-profit ${k4(p.take_profit_at)}, horizon ${p.horizon_at}`);
			}
			return;
		}
		let p = find(ledger, key);
		let detail = process.env.AGENT_ACTION_BY || 'by hand (position.js)';
		let q;
		if (what === 'sell-now') q = ledger.sellNow(p.id, detail);
		else if (what === 'tp') q = ledger.setTakeProfit(p.id, Number(value) / 100, detail);
		else if (what === 'horizon') q = ledger.setHorizon(p.id, value === 'reset' ? null : Number(value), detail);
		else throw new Error('usage: node position.js <id | item> sell-now | tp <percent> | horizon <hours | reset>');
		console.log(`${q.id} ${q.item}: take-profit ${k4(q.take_profit_at)} keys (paid ${k4(q.entry_keys)}), horizon ${q.horizon_at}` +
			(what === 'sell-now' ? ' -- sold into the best tradable bid at the next cycle' : ''));
	} finally {
		ledger.close();
	}
}

try { main(); } catch (err) { console.error(err.message); process.exitCode = 1; }

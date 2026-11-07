// A mode's results so far, from the ledger: what is held, what closed, and how
// the closed trades compare with the backtest (research/forecast_v2/README.md, the
// never-tuned window 09-25 -> 09-29: 149 trades, 73% won, +2.15% a trade).
const BACKTEST = {per_trade: 0.0215, win: 0.73};

function stats(ledger, mode) {
	let q = (sql, ...p) => ledger.db.prepare(sql).all(mode, ...p);
	let held = q(`SELECT item, quality, entry_keys, opened_at, horizon_at, take_profit_at FROM positions
		WHERE mode = ? AND state IN ('opening', 'open', 'closing') ORDER BY opened_at`);
	let closed = q(`SELECT item, entry_keys, exit_keys, pnl_keys, exit_reason, opened_at, closed_at FROM positions
		WHERE mode = ? AND state = 'closed' ORDER BY closed_at`);
	let abandoned = q(`SELECT count(*) AS n FROM positions WHERE mode = ? AND state = 'abandoned'`)[0].n;
	let rets = closed.map(c => c.pnl_keys / c.entry_keys);
	let byReason = {};
	for (let c of closed) {
		let r = byReason[c.exit_reason] ||= {n: 0, pnl: 0};
		r.n++;
		r.pnl += c.pnl_keys;
	}
	return {
		held, closed, abandoned, byReason,
		held_keys: held.reduce((s, p) => s + p.entry_keys, 0),
		pnl_keys: closed.reduce((s, c) => s + c.pnl_keys, 0),
		per_trade: rets.length ? rets.reduce((s, r) => s + r, 0) / rets.length : null,
		win: rets.length ? rets.filter(r => r > 0).length / rets.length : null,
		hours: closed.length ? closed.reduce((s, c) => s + (Date.parse(c.closed_at) - Date.parse(c.opened_at)), 0) / closed.length / 3600e3 : null,
	};
}

const pct = x => x == null ? '-' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`;

function summary(ledger, mode = 'live') {
	let s = stats(ledger, mode);
	let lines = [`${mode}: holding ${s.held.length} (${s.held_keys.toFixed(4)} keys); closed ${s.closed.length}, ` +
		`realised ${s.pnl_keys >= 0 ? '+' : ''}${s.pnl_keys.toFixed(4)} keys; ${s.abandoned} buy(s) abandoned`];
	if (s.closed.length) {
		lines.push(`  per trade ${pct(s.per_trade)} (backtest ${pct(BACKTEST.per_trade)}), won ${pct(s.win).slice(1)} ` +
			`(backtest ${(BACKTEST.win * 100).toFixed(0)}%), held ${s.hours.toFixed(1)} h on average`);
		lines.push('  by exit: ' + Object.entries(s.byReason).map(([r, v]) =>
			`${r} ${v.n} (${v.pnl >= 0 ? '+' : ''}${v.pnl.toFixed(4)} keys)`).join(', '));
	}
	return lines;
}

module.exports = {stats, summary, BACKTEST};

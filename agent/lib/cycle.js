// Stage 6, Phase 5 -- one cycle of the live agent: offers others sent us declined, the restart
// check, exits (getting out is never blocked by a limit), the key<->metal rebalance
// (lib/convert.js), then the buys of a new pass. Every decision is the engine's
// (Python screens through `deps.engine`): picks (forecast.AGENT_RULE), the fresh re-check,
// the exit rule. This file asks the user, builds the offers and hands them to lib/send.js.
//
// For each trade: the engine re-checks it on a fresh book -> the user approves it (15 min;
// no answer = skipped, and the rest of this cycle's approvals are skipped too: the user is
// away) -> the book is re-checked again (the approval may have taken minutes) -> the offer
// is settled (a buy: refined rounded up with the seller's change back first, our small pieces
// only after; a sale: the buyer's exact pieces first; change logged) -> tryOffers: the best
// bot, 30 s, then the next ones in range.
//
// deps: engine(screen, args), steam (lib/steam.js), inventory(steamid) (a counterparty's
// public inventory), ledger, ledgerPath, target (the book server tunnel), cfg (limits.json),
// log, approve(question, waitS), sleep(ms), latestPass(), stopFiles / resumedFile (tests).
const {stock, EMPTY} = require('./currency');
const {buyOffer, sellOffer, assetCheck, metalTarget} = require('./offers');
const {tryOffers, HoldError} = require('./send');
const {reconcile} = require('./reconcile');
const {rebalance} = require('./convert');
const limits = require('./limits');
const {QUALITY} = require('./trades');

const MODE = 'live';
const k4 = x => Number(x).toFixed(4);

// {stock, items}; items null when it can't be read (a sale: exact payment only; a buy: refused,
// the asset can't be checked).
async function theirInventory(deps, steamid) {
	try {
		let items = await deps.inventory(steamid);
		return {stock: stock(items), items};
	} catch (err) {
		deps.log(`    their inventory ${steamid}: ${err.message} -- exact payment only, no buy`);
		return {stock: EMPTY, items: null};
	}
}

// Text another account wrote (an offer message), made safe for one log line.
const quote = s => JSON.stringify(String(s).replace(/[\u0000-\u001f\u007f]+/g, ' ').slice(0, 80));

// Offers other accounts sent us: the agent never takes one, it declines them all and logs who.
// A failure here only logs: it never stops the cycle.
async function declineIncoming(deps) {
	let offers;
	try { offers = await deps.steam.incoming(); } catch (err) {
		deps.log(`  incoming: not readable (${err.message})`);
		return 0;
	}
	let n = 0;
	for (let o of offers) {
		try {
			await deps.steam.decline(o.id);
			n++;
			deps.log(`  incoming: declined offer ${o.id} from ${o.partner} (asks ${o.give} of ours, gives ${o.receive}; ` +
				`message ${quote(o.message)})`);
		} catch (err) {
			deps.log(`  incoming: decline ${o.id} from ${o.partner}: ${err.message}`);
		}
	}
	return n;
}

async function ourStock(deps) {
	return stock(await deps.steam.inventory(), deps.ledger.reservedAssets());
}

// Ask the user; null (no answer) marks them away for the rest of the cycle.
async function ask(deps, state, question) {
	if (state.away) return false;
	let yes = await deps.approve(question, deps.cfg.approve_wait_s);
	if (yes === null) {
		state.away = true;
		deps.log('  no answer in time: the rest of this cycle\'s trades are skipped');
	}
	return !!yes;
}

async function recheck(deps, item, quality, args) {
	let r = await deps.engine('recheck', [item, '--quality', String(quality), ...args, '--target', deps.target]);
	return r.data;
}

async function exitsStep(deps, state) {
	let r = await deps.engine('exits', ['--ledger', deps.ledgerPath, '--target', deps.target]);
	let sold = 0;
	for (let d of r.data || []) {
		if (d.action !== 'sell') {
			// The quality too: a Unique and a Strange of one item are two positions (trades.js reads this)
			deps.log(`  hold ${d.item} (${QUALITY[d.quality] || 'q' + d.quality}): ${d.why}${d.detail ? ` (${d.detail})` : ''}`);
			continue;
		}
		let gate = limits.allowSell({stopFiles: deps.stopFiles});
		if (!gate.ok) { deps.log(`  sell ${d.item}: ${gate.reason}`); continue; }
		let p = deps.ledger.position(d.position_id);
		if (!p.asset_id) { deps.log(`  sell ${d.item}: its asset id is not known yet`); continue; }
		let l = d.listing;
		let pct = (l.total_keys / p.entry_keys - 1) * 100;
		if (!(await ask(deps, state, `SELL ${d.item} (${d.why}) to ${l.user} at ${k4(l.total_keys)} keys ` +
			`(paid ${k4(p.entry_keys)}, ${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)`))) {
			deps.log(`  sell ${d.item}: not approved`);
			continue;
		}
		let rc = await recheck(deps, p.item, p.quality, ['--ask1', String(p.entry_keys), '--sell', '--min', String(d.min_keys)]);
		if (!rc || !rc.ok) { deps.log(`  sell ${d.item}: gone at approval -- ${rc ? rc.reason : 'no re-check'}`); continue; }
		let ours = await ourStock(deps);
		let candidates = rc.candidates || [rc.listing];
		let why = deps.ledger.soldByHand(p.id) ? 'manual' : d.why;          // the user's sell-now
		let res = await tryOffers(deps, {kind: 'sell', candidates, watchId: rc.watch_id ?? null, extra: {exitReason: why},
			start: offer => ({positionId: p.id, ...deps.ledger.openSell({positionId: p.id, offer, watchId: rc.watch_id ?? null})}),
			...await builders(deps, 'sell', p, ours, candidates)});
		if (res.filled) {
			let q = deps.ledger.position(p.id);
			deps.log(`  SOLD ${d.item} (${why}) at ${k4(res.listing.total_keys)} to ${res.listing.user}: ` +
				`${q.pnl_keys >= 0 ? '+' : ''}${k4(q.pnl_keys)} keys`);
			sold++;
		} else deps.log(`  sell ${d.item}: no bot took it -- ${res.reasons.join('; ') || 'no candidate'} -- held, tried again next cycle`);
	}
	return sold;
}

// The offer builder for each candidate, with that counterparty's inventory read once. A buy
// first checks the listed asset is the pick's item (offers.assetCheck).
async function builders(deps, kind, position, ours, candidates, pick = null) {
	let theirs = new Map();
	for (let l of candidates.slice(0, deps.cfg.max_tries + 2)) theirs.set(l.steam_id, await theirInventory(deps, l.steam_id));
	let target = metalTarget(deps.cfg);
	return {build: l => {
		let t = theirs.get(l.steam_id) || {stock: EMPTY, items: null};
		if (kind === 'sell') return sellOffer(position, l, t.stock, ours, target);
		let bad = assetCheck(l, t.items, pick.item, pick.quality);
		return bad ? {ok: false, reason: bad} : buyOffer(l, ours, t.stock, target);
	}};
}

async function buysStep(deps, state) {
	if (deps.latestPass && state.lastPassId != null && (await deps.latestPass()) <= state.lastPassId) {
		deps.log(`  picks: no pass newer than ${state.lastPassId} yet`);   // skip the model run
		return 0;
	}
	let r = await deps.engine('picks', ['--ledger', deps.ledgerPath, '--k', String(deps.cfg.max_new_buys_per_pass)]);
	let out = r.data || {};
	if (out.stale) { deps.log(`  picks: stale -- ${out.reason}`); return 0; }
	if (out.pass_id === state.lastPassId) { deps.log(`  picks: pass ${out.pass_id} already done`); return 0; }
	state.lastPassId = out.pass_id;
	deps.log(`  pass ${out.pass_id}: ${out.n_over_bar} over the bar, ${out.picks.length} picked, ${out.n_held} held`);
	let bought = 0;
	for (let pick of out.picks.map(p => ({...p, pass_id: out.pass_id}))) {   // the ledger keeps the pass
		let gate = limits.allowBuy({ledger: deps.ledger, mode: MODE, priceKeys: pick.ask1, cfg: deps.cfg,
			boughtThisPass: bought, stopFiles: deps.stopFiles, resumedFile: deps.resumedFile});
		if (!gate.ok) { deps.log(`  skip ${pick.label}: ${gate.reason}`); continue; }
		let rc = await recheck(deps, pick.item, pick.quality, ['--ask1', String(pick.ask1)]);
		if (!rc || !rc.ok) { deps.log(`  skip ${pick.label}: ${rc ? rc.reason : 'no recheck'}`); continue; }
		if (!(await ask(deps, state, `BUY ${pick.label} from ${rc.listing.user} at ${k4(rc.listing.total_keys)} keys ` +
			`(p_up ${pick.p_up.toFixed(2)}, ask1 ${k4(pick.ask1)}; sells at +4% or after 48 h)`))) {
			deps.log(`  skip ${pick.label}: not approved`);
			continue;
		}
		rc = await recheck(deps, pick.item, pick.quality, ['--ask1', String(pick.ask1)]);   // the approval took time
		if (!rc || !rc.ok) { deps.log(`  skip ${pick.label}: gone at approval -- ${rc ? rc.reason : 'no recheck'}`); continue; }
		let ours = await ourStock(deps);
		let candidates = rc.candidates || [rc.listing];
		let res = await tryOffers(deps, {kind: 'buy', candidates, watchId: rc.watch_id ?? null,
			start: offer => deps.ledger.openBuy({mode: MODE, pick, offer, watchId: rc.watch_id ?? null}),
			...await builders(deps, 'buy', null, ours, candidates, pick)});
		if (res.filled) {
			deps.log(`  BOUGHT ${pick.label} at ${k4(res.listing.total_keys)} from ${res.listing.user} (p_up ${pick.p_up.toFixed(2)})`);
			bought++;
		} else deps.log(`  skip ${pick.label}: no fill -- ${res.reasons.join('; ')}`);
	}
	return bought;
}

// One cycle. `state` persists across cycles ({lastPassId}).
async function cycle(deps, state) {
	let stop = limits.stopped(deps.stopFiles);
	if (stop) { deps.log(`  ${stop}: nothing this cycle`); return {sold: 0, bought: 0, stopped: true}; }
	state.away = false;
	try {
		await declineIncoming(deps);
		await reconcile(deps);
		let sold = await exitsStep(deps, state);
		let converted = await rebalance(deps, state, {ourStock, theirInventory, ask});
		let bought = await buysStep(deps, state);
		return {sold, bought, converted};
	} catch (err) {
		if (!(err instanceof HoldError)) throw err;
		limits.stop(err.message, deps.stopFiles);
		deps.log(`  STOPPED: ${err.message} (the kill switch is on; \`node stop.js off\` once checked)`);
		return {sold: 0, bought: 0, stopped: true};
	}
}

module.exports = {cycle, exitsStep, buysStep, declineIncoming, MODE};

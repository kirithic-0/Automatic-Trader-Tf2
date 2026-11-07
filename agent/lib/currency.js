// TF2 currency: keys and metal. 1 ref = 3 reclaimed = 9 scrap (docs/glossary/tf2-economy.md);
// prices are keys + ref, the ref part a whole number of scrap (0.11 ref each).
// Item names as Steam's inventory lists them (measured 2026-10-19 on a bot's public
// inventory: all four Unique, tradable, one asset each).
const NAMES = {
	key: 'Mann Co. Supply Crate Key',
	ref: 'Refined Metal',
	rec: 'Reclaimed Metal',
	scrap: 'Scrap Metal',
};
const SCRAP = {ref: 9, rec: 3, scrap: 1};

// {keys, ref} (a listing's price) -> {keys, scrap}. Throws on a price that is not whole
// keys + whole scrap: the agent never guesses an amount.
function toUnits(price) {
	let keys = Number(price.keys || 0), ref = Number(price.ref || 0);
	if (!Number.isInteger(keys) || keys < 0) throw new Error(`not whole keys: ${price.keys}`);
	// bp.tf writes n scrap as n x .11 (7 scrap = .77, not 7/9 = .78); 2026-10-19, pass 202:
	// 97.1% of listed ref prices are, the rest (5.38 ref) can't be paid in metal exactly
	let whole = Math.floor(ref), cents = Math.round((ref - whole) * 100);
	if (ref < 0 || cents % 11) throw new Error(`not whole scrap: ${price.ref} ref`);
	return {keys, scrap: whole * 9 + cents / 11};
}

function refString(scrap) {
	let r = Math.floor(scrap / 9), s = scrap % 9;
	return s ? `${r}.${String(Math.round(s * 11)).padStart(2, '0')}` : `${r}`;
}

// Our tradable currency, from inventory items ({assetid, market_hash_name, tradable}),
// minus `reserved` asset ids (already promised in another open offer).
function stock(items, reserved = new Set()) {
	let out = {key: [], ref: [], rec: [], scrap: []};
	for (let it of items) {
		if (!it.tradable || reserved.has(String(it.assetid))) continue;
		for (let [kind, name] of Object.entries(NAMES)) {
			if (it.market_hash_name === name) out[kind].push(String(it.assetid));
		}
	}
	return out;
}

// Exact payment of {keys, scrap} from `have` (stock()), or {ok: false, reason}.
// Largest metal first: with 9/3/1 each dividing the next, this finds exact change
// whenever any exists (checked against brute force in the tests).
function pay(units, have) {
	if (have.key.length < units.keys) {
		return {ok: false, shortKeys: true, reason: `short ${units.keys - have.key.length} key(s)`};
	}
	let left = units.scrap, take = {};
	for (let kind of ['ref', 'rec', 'scrap']) {
		let n = Math.min(have[kind].length, Math.floor(left / SCRAP[kind]));
		take[kind] = have[kind].slice(0, n);
		left -= n * SCRAP[kind];
	}
	if (left) {
		return {ok: false, reason: `no exact change: ${refString(left)} ref of ${refString(units.scrap)} left over ` +
			`(have ${have.ref.length} ref, ${have.rec.length} rec, ${have.scrap.length} scrap)`};
	}
	return {ok: true, assetids: [...have.key.slice(0, units.keys), ...take.ref, ...take.rec, ...take.scrap],
		counts: {key: units.keys, ref: take.ref.length, rec: take.rec.length, scrap: take.scrap.length}};
}

const EMPTY = {key: [], ref: [], rec: [], scrap: []};

// Settle {keys, scrap} between a payer and a payee: the payer's exact pieces, or -- when
// it has no exact change -- a little more, with the payee's metal coming back as change
// (smallest change first, at most maxChange scrap). The net is always exactly the price.
// Keys never take change: the payer must hold them. Both sides' stock = stock(). This is
// how the agent makes change without smelting (step 3.4): the counterparty's inventory
// supplies the small pieces.
//
// `preferChange` (our buys, user 2026-10-26): keep the payer's small pieces -- first pay in
// refined only, rounded up, with the payee's change back; then in refined + reclaimed;
// only then the payer's exact pieces as above.
function settle(units, payer, payee = EMPTY, maxChange = 18, {preferChange = false} = {}) {
	if (preferChange && payer.key.length >= units.keys) {
		for (let kinds of [['ref'], ['ref', 'rec']]) {
			let only = {...EMPTY, key: payer.key};
			for (let k of kinds) only[k] = payer[k];
			for (let e = 0; e <= maxChange; e++) {
				let over = pay({keys: units.keys, scrap: units.scrap + e}, only);
				if (!over.ok) continue;
				if (!e) return {ok: true, payer: over.assetids, payee: [], change: 0, counts: over.counts};
				let back = pay({keys: 0, scrap: e}, payee);
				if (back.ok) {
					return {ok: true, payer: over.assetids, payee: back.assetids, change: e, counts: over.counts,
						change_counts: back.counts};
				}
			}
		}
	}
	let exact = pay(units, payer);
	if (exact.ok || exact.shortKeys) {
		return exact.ok ? {ok: true, payer: exact.assetids, payee: [], change: 0, counts: exact.counts} : exact;
	}
	for (let e = 1; e <= maxChange; e++) {
		let over = pay({keys: units.keys, scrap: units.scrap + e}, payer);
		if (!over.ok) continue;
		let back = pay({keys: 0, scrap: e}, payee);
		if (back.ok) {
			return {ok: true, payer: over.assetids, payee: back.assetids, change: e, counts: over.counts,
				change_counts: back.counts};
		}
	}
	return {ok: false, reason: `${exact.reason}; no change within ${refString(maxChange)} ref either`};
}

// Settle {keys, scrap} so OUR reclaimed and scrap counts end as near `target` ({rec, scrap},
// pieces) as this trade allows (user, 2026-10-30: hold a steady 15 rec + 10 scrap instead of
// spending small pieces down and smelting-short; refined takes up the rest). `ours` says
// which side is us: 'payer' (a buy) or 'payee' (a sale). Every split of the price into
// refined / reclaimed / scrap moving either way is tried -- each kind moves one way only,
// the pieces moving back (change) worth at most maxChange scrap -- and the one leaving
// |rec - target.rec| + |scrap - target.scrap| smallest wins; ties: less change, then fewer
// pieces. Same result shape as settle(); the net is always exactly the price.
function settleToward(units, payer, payee = EMPTY, target, ours, maxChange = 18) {
	if (payer.key.length < units.keys) {
		return {ok: false, shortKeys: true, reason: `short ${units.keys - payer.key.length} key(s)`};
	}
	let P = units.scrap, me = ours === 'payer' ? payer : payee, sign = ours === 'payer' ? -1 : 1;
	let best = null;
	// n* = pieces of each kind the payer hands over (negative: the payee hands them back)
	let cLo = -Math.min(payee.rec.length, Math.floor(maxChange / 3)), cHi = Math.min(payer.rec.length, Math.floor((P + maxChange) / 3));
	let sLo = -Math.min(payee.scrap.length, maxChange), sHi = Math.min(payer.scrap.length, P + maxChange);
	for (let nc = cLo; nc <= cHi; nc++) {
		for (let ns = sLo; ns <= sHi; ns++) {
			let rest = P - 3 * nc - ns;
			if (rest % 9) continue;
			let nr = rest / 9;
			if (nr > payer.ref.length || -nr > payee.ref.length) continue;
			let change = 9 * Math.max(0, -nr) + 3 * Math.max(0, -nc) + Math.max(0, -ns);
			if (change > maxChange) continue;
			let dist = Math.abs(me.rec.length + sign * nc - target.rec) + Math.abs(me.scrap.length + sign * ns - target.scrap);
			let key = [dist, change, Math.abs(nr) + Math.abs(nc) + Math.abs(ns)];
			if (!best || key[0] < best.key[0] || (key[0] === best.key[0] && (key[1] < best.key[1] ||
				(key[1] === best.key[1] && key[2] < best.key[2])))) best = {key, nr, nc, ns, change};
		}
	}
	if (!best) {
		return {ok: false, reason: `no exact change for ${refString(P)} ref within ${refString(maxChange)} ref of change ` +
			`(payer has ${payer.ref.length} ref, ${payer.rec.length} rec, ${payer.scrap.length} scrap)`};
	}
	let give = {ref: Math.max(0, best.nr), rec: Math.max(0, best.nc), scrap: Math.max(0, best.ns)};
	let back = {ref: Math.max(0, -best.nr), rec: Math.max(0, -best.nc), scrap: Math.max(0, -best.ns)};
	let ids = (s, n) => [...s.ref.slice(0, n.ref), ...s.rec.slice(0, n.rec), ...s.scrap.slice(0, n.scrap)];
	let out = {ok: true, payer: [...payer.key.slice(0, units.keys), ...ids(payer, give)], payee: ids(payee, back),
		change: best.change, counts: {key: units.keys, ...give}};
	if (best.change) out.change_counts = {key: 0, ...back};
	return out;
}

module.exports = {NAMES, SCRAP, EMPTY, toUnits, refString, stock, pay, settle, settleToward};

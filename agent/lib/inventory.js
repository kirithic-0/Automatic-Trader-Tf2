// A TF2 inventory from Steam's public inventory endpoint (the inventory must be public),
// as [{assetid, market_hash_name, tradable}]. Read-only: a 429 waits and retries (lib/retry.js).
const {on429} = require('./retry');

async function tf2Inventory(steamid, {log} = {}) {
	let items = [], start = null;
	for (let page = 0; page < 20; page++) {
		let url = `https://steamcommunity.com/inventory/${steamid}/440/2?l=english&count=2000` +
			(start ? `&start_assetid=${start}` : '');
		let j = await on429(async () => {
			let r = await fetch(url);
			if (r.status === 403) throw new Error('inventory is private: set Inventory to Public in the profile privacy settings');
			if (!r.ok) throw new Error(`inventory HTTP ${r.status}`);
			return r.json();
		}, {what: 'their inventory', log});
		let desc = new Map((j.descriptions || []).map(d => [`${d.classid}_${d.instanceid}`, d]));
		for (let a of j.assets || []) {
			let d = desc.get(`${a.classid}_${a.instanceid}`) || {};
			items.push({assetid: String(a.assetid), market_hash_name: d.market_hash_name, tradable: !!d.tradable});
		}
		if (!j.more_items) return items;
		start = j.last_assetid;
	}
	throw new Error('inventory: too many pages');
}

// Our own inventory read logged in can fail on Steam's side, its reason as text in an HTTP 500:
// "EYldRefreshAppIfNecessary(440) failed with EResult 55" (RemoteCallFailed: Steam could not
// refresh it from TF2's item server) 2026-11-01 10:16-11:26 UTC, 7 of 8 cycles lost, and "The
// request is a duplicate and the action has already occurred in the past" 11:56, while the
// public endpoint answered 200. Dr. McKay (node-steamcommunity): Steam-side, on and off. On any
// failure (a 429 only after lib/retry.js's waits) the public endpoint stands in -- a read only;
// a send never goes through here. It can lag a trade: a currency asset we just gave may still
// show, and its offer then fails at send (nothing goes out twice); one we just got may be missing.
async function withPublicFallback(primary, steamid, {log = () => {}, fallback = tf2Inventory} = {}) {
	try {
		return await primary();
	} catch (err) {
		log(`    our inventory: ${err.message} -- reading the public inventory instead`);
		return (await fallback(steamid, {log})).filter(it => it.tradable);
	}
}

module.exports = {tf2Inventory, withPublicFallback};

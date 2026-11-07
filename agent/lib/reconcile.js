// Stage 6, Phase 5 -- the restart check: before the agent sends anything, every offer its
// ledger still has open is read back from Steam. The PC is not on all the time, and an
// offer may have filled while the agent was off: Steam's word, not a guess, decides.
//
//   built, no Steam id  -> the agent stopped around the send: Steam's offers since an hour
//                          before it was built are searched for one to the same partner with
//                          exactly its asset ids (offers carry no message), not already in the
//                          ledger, newest first; found = sent, not found = never sent (canceled)
//   sent / active       -> Accepted: a fill (a buy gets its new asset id); still open: cancel,
//                          read again (a lost race is a fill); ended: recorded as Steam says
//   escrow              -> Accepted: a fill; still held: left alone, logged
// Then: a held item with no asset id yet gets it from its buy's trade; a position left
// opening / closing with no open offer is given up (a buy abandoned, a sale back to open).
// The rebalance's key<->metal offers (ledger conversions) are read back the same way; they
// have no position to follow.
const {newAssetId, END} = require('./send');

const OPEN = ['Active', 'CreatedNeedsConfirmation'];

// One offer's identity without a message: who, and exactly which assets each way.
const ids = list => list.map(String).sort().join(',');
const sameTrade = (o, x) => x.partner === o.partner
	&& ids(x.give) === ids(JSON.parse(o.give_json).map(a => a.assetid))
	&& ids(x.receive) === ids(JSON.parse(o.receive_json).map(a => a.assetid));

async function settle(deps, o, state, why) {
	let {ledger, steam, log} = deps;
	if (state === 'Accepted') {
		let p = ledger.position(o.position_id);
		let assetId = o.kind === 'buy' ? await newAssetId(deps, o.steam_offer_id, JSON.parse(o.receive_json)[0].assetid) : null;
		ledger.setOffer(o.id, 'accepted', {assetId, detail: `restart check: ${why}`,
			exitReason: o.kind === 'sell' ? (new Date().toISOString() < p.horizon_at ? 'take_profit' : 'horizon') : null});
		log(`  restart: ${o.kind} offer ${o.id} (${p.item}) was ACCEPTED ${why}`);
		return;
	}
	if (state === 'InEscrow') {
		if (o.state !== 'escrow') ledger.setOffer(o.id, 'escrow', {detail: 'restart check: Steam holds it'});
		log(`  restart: ${o.kind} offer ${o.id} is in a trade hold -- left alone`);
		return;
	}
	if (OPEN.includes(state)) {
		try { await steam.cancel(o.steam_offer_id); } catch (err) { log(`  restart: cancel ${o.steam_offer_id}: ${err.message}`); }
		let again = (await steam.state(o.steam_offer_id)).state;
		if (OPEN.includes(again)) throw new Error(`offer ${o.steam_offer_id} is still ${again} after a cancel`);
		return settle(deps, o, again, 'as it was canceled');
	}
	ledger.setOffer(o.id, END[state] || 'failed', {detail: `restart check: Steam says ${state}`});
	log(`  restart: ${o.kind} offer ${o.id} ended ${state}`);
}

// A conversion's Steam state, recorded (no position, no asset id to read).
async function settleConversion(deps, c, state, why) {
	let {ledger, steam, log} = deps;
	if (OPEN.includes(state)) {
		try { await steam.cancel(c.steam_offer_id); } catch (err) { log(`  restart: cancel ${c.steam_offer_id}: ${err.message}`); }
		let again = (await steam.state(c.steam_offer_id)).state;
		if (OPEN.includes(again)) throw new Error(`offer ${c.steam_offer_id} is still ${again} after a cancel`);
		return settleConversion(deps, c, again, 'as it was canceled');
	}
	let to = state === 'Accepted' ? 'accepted' : state === 'InEscrow' ? 'escrow' : END[state] || 'failed';
	if (to !== c.state) ledger.setConversion(c.id, to, {detail: `restart check: Steam says ${state}`});
	log(`  restart: ${c.kind} conversion ${c.id} ${to === 'accepted' ? `was ACCEPTED ${why}` : `is ${state}`}`);
}

async function reconcile(deps, {mode = 'live', lookbackH = 72} = {}) {
	let {ledger, steam, log} = deps;
	let n = 0;
	// from the oldest unsent offer's own time (an hour early), never later than lookbackH ago:
	// a PC off for days still finds an offer that filled meanwhile
	let built = [...ledger.openOffers().filter(o => o.mode === mode), ...ledger.openConversions()]
		.filter(o => !o.steam_offer_id).map(o => Date.parse(o.created_at));
	let since = new Date(Math.min(Date.now() - lookbackH * 3600e3, ...built.map(t => t - 3600e3)));
	let recent = null;
	// the Steam offer an unsent row became, or null: same partner + assets, not in the ledger yet
	let find = async o => {
		recent = recent || await steam.sentSince(since);
		let known = new Set(ledger.db.prepare(`SELECT steam_offer_id FROM offers WHERE steam_offer_id IS NOT NULL
			UNION SELECT steam_offer_id FROM conversions WHERE steam_offer_id IS NOT NULL`).all().map(r => r.steam_offer_id));
		return recent.filter(x => !known.has(x.id) && sameTrade(o, x))
			.sort((a, b) => String(b.created).localeCompare(String(a.created)))[0] || null;
	};
	for (let o of ledger.openOffers()) {
		if (o.mode !== mode) continue;
		n++;
		if (!o.steam_offer_id) {
			let hit = await find(o);
			if (!hit) {
				ledger.setOffer(o.id, 'canceled', {detail: 'restart check: never reached Steam'});
				log(`  restart: ${o.kind} offer ${o.id} never reached Steam`);
				continue;
			}
			ledger.setOffer(o.id, 'sent', {steamOfferId: hit.id});
			o = ledger.offer(o.id);
		}
		let {state} = await steam.state(o.steam_offer_id);
		await settle(deps, {...o, state: ledger.offer(o.id).state}, state, 'while the agent was off');
	}
	for (let c of ledger.openConversions()) {
		n++;
		if (!c.steam_offer_id) {
			let hit = await find(c);
			if (!hit) {
				ledger.setConversion(c.id, 'canceled', {detail: 'restart check: never reached Steam'});
				log(`  restart: ${c.kind} conversion ${c.id} never reached Steam`);
				continue;
			}
			ledger.setConversion(c.id, 'sent', {steamOfferId: hit.id});
			c = ledger.conversion(c.id);
		}
		let {state} = await steam.state(c.steam_offer_id);
		await settleConversion(deps, c, state, 'while the agent was off');
	}
	// held items whose new asset id was not read at the fill
	for (let p of ledger.held()) {
		if (p.mode !== mode || p.state !== 'open' || p.asset_id) continue;
		let buyOffer = ledger.db.prepare(`SELECT * FROM offers WHERE position_id = ? AND kind = 'buy' AND state = 'accepted'`).get(p.id);
		let id = buyOffer && await newAssetId(deps, buyOffer.steam_offer_id, JSON.parse(buyOffer.receive_json)[0].assetid);
		if (id) {
			ledger.db.prepare('UPDATE positions SET asset_id = ?, updated_at = ? WHERE id = ?').run(id, new Date().toISOString(), p.id);
			log(`  restart: ${p.item} -- asset id ${id} read from its trade`);
		} else log(`  restart: ${p.item} -- asset id still unknown, it cannot be sold yet`);
	}
	// a position whose last offer ended but was not given up (the agent stopped in between)
	let open = new Set(ledger.openOffers().map(o => o.position_id));
	for (let p of ledger.held()) {
		if (p.mode === mode && ['opening', 'closing'].includes(p.state) && !open.has(p.id)) {
			ledger.giveUp(p.id, 'restart check: no offer out');
			log(`  restart: ${p.item} had no offer out -- ${p.state === 'opening' ? 'buy dropped' : 'back to held'}`);
		}
	}
	return n;
}

module.exports = {reconcile};

// Stage 6, Phase 5 -- sending one trade: an offer to the best bot, then the next ones if it
// does not answer (user, 2026-10-26).
//
//   tryOffers(deps, {kind, candidates, build, start, ...})
//
// For each candidate listing (best first, at most cfg.max_tries):
//   1. build the offer (offers.buyOffer / sellOffer); one that can't settle is skipped;
//   2. ask Steam how long it would hold the trade: our side > 0 days = something is wrong
//      with the account -> HoldError, the agent stops; their side > 0 -> the next bot;
//   3. send it, with no message (user, 2026-10-30: an offer never carries text; a restart
//      finds it by partner + exact asset ids), then
//      confirm it with the identity_secret (the user already approved the trade);
//   4. wait cfg.bot_wait_s (30 s) for the bot; Accepted -> done; declined / invalid -> next;
//   5. no answer: cancel, then read the offer again -- a cancel can lose the race with the
//      bot's accept, and an accepted offer is a fill, never a second buy.
// The ledger has every step (offers + offer_events); the position ends filled, or given up
// (a buy abandoned, a sale back to open) after the last try.
//
// deps: steam (lib/steam.js), ledger, log, cfg (limits.json), sleep(ms) (tests: instant).

class HoldError extends Error {}

const END = {Declined: 'declined', InvalidItems: 'failed', Canceled: 'canceled', Expired: 'expired',
	CanceledBySecondFactor: 'canceled', Invalid: 'failed', Countered: 'declined'};

const OPEN = ['Active', 'CreatedNeedsConfirmation'];

// The new asset id of the item a buy received (our inventory gives it a new one).
async function newAssetId(deps, steamOfferId, theirAssetId) {
	for (let i = 0; i < 4; i++) {
		try {
			let got = await deps.steam.received(steamOfferId);
			let hit = got.find(a => a.assetid === String(theirAssetId));
			if (hit) return hit.new_assetid;
			return null;
		} catch (err) {
			deps.log(`    received items not readable yet (${err.message})`);
			await deps.sleep(3000);
		}
	}
	return null;
}

async function filled(deps, kind, offerId, steamOfferId, listing, extra) {
	let assetId = kind === 'buy' ? await newAssetId(deps, steamOfferId, listing.asset_id) : null;
	deps.ledger.setOffer(offerId, 'accepted', {...extra, assetId, detail: kind === 'buy' && !assetId
		? 'accepted; new asset id not read yet (the restart check fills it)' : null});
	return {filled: true, offerId, listing};
}

// One offer already in the ledger as `built`. Returns {filled} or {next: reason}.
async function attempt(deps, kind, offerId, spec, listing, extra) {
	let {steam, ledger, log, cfg} = deps;
	let hold = await steam.holdDays(spec.trade_url);
	if (hold.me > 0) {
		ledger.setOffer(offerId, 'failed', {detail: `our trades would be held ${hold.me} days`, final: false});
		throw new HoldError(`Steam would hold our trades ${hold.me} days: nothing is sent until that is checked`);
	}
	if (hold.them > 0) {
		ledger.setOffer(offerId, 'failed', {detail: `their trades would be held ${hold.them} days`, final: false});
		return {next: `${listing.user}'s trades would be held ${hold.them} days`};
	}

	let sid;
	try {
		sid = await steam.send(spec);
	} catch (err) {
		ledger.setOffer(offerId, 'failed', {detail: `send: ${err.message}`, final: false});
		return {next: `send failed: ${err.message}`};
	}
	ledger.setOffer(offerId, 'sent', {steamOfferId: sid});
	try {
		await steam.confirm(sid);
	} catch (err) {
		// the confirmation may have gone through with only Steam's answer lost: cancel, then
		// Steam says what the offer is -- an accept is a fill, an offer still open is left to
		// the restart check, never marked failed while a bot can still take it
		log(`    confirm ${sid}: ${err.message} -- canceling it`);
		try { await steam.cancel(sid); } catch (e) { log(`    cancel ${sid}: ${e.message}`); }
		let state = (await steam.state(sid)).state;
		if (state === 'Accepted') {
			log(`    ${listing.user} accepted although the confirmation failed: a fill`);
			return filled(deps, kind, offerId, sid, listing, extra);
		}
		if (state === 'InEscrow') {
			ledger.setOffer(offerId, 'escrow', {detail: 'Steam is holding the trade'});
			throw new HoldError(`offer ${sid} went into a trade hold: nothing more is sent until that is checked`);
		}
		if (OPEN.includes(state)) {
			throw new Error(`offer ${sid} is still ${state} after a failed confirmation and a cancel: stopping (the restart check reads it)`);
		}
		ledger.setOffer(offerId, END[state] || 'failed', {detail: `confirm: ${err.message}`, final: false});
		return {next: `confirmation failed: ${err.message}`};
	}
	ledger.setOffer(offerId, 'active');
	log(`    sent offer ${sid} to ${listing.user}: waiting ${cfg.bot_wait_s} s`);

	let state = 'Active';
	for (let waited = 0; waited < cfg.bot_wait_s * 1000; waited += cfg.poll_ms) {
		await deps.sleep(cfg.poll_ms);
		try { state = (await steam.state(sid)).state; } catch (err) { continue; }
		if (state !== 'Active' && state !== 'CreatedNeedsConfirmation') break;
	}
	if (state === 'Active' || state === 'CreatedNeedsConfirmation') {
		try { await steam.cancel(sid); } catch (err) { log(`    cancel ${sid}: ${err.message}`); }
		state = (await steam.state(sid)).state;           // the race: did the bot accept meanwhile?
		if (state === 'Active' || state === 'CreatedNeedsConfirmation') {
			throw new Error(`offer ${sid} is still ${state} after a cancel: stopping (the restart check reads it)`);
		}
		if (state !== 'Accepted') {
			ledger.setOffer(offerId, END[state] || 'canceled', {detail: `no answer in ${cfg.bot_wait_s} s`, final: false});
			return {next: `${listing.user} did not answer in ${cfg.bot_wait_s} s`};
		}
		log(`    ${listing.user} accepted as we canceled: a fill`);
	}
	if (state === 'Accepted') return filled(deps, kind, offerId, sid, listing, extra);
	if (state === 'InEscrow') {
		ledger.setOffer(offerId, 'escrow', {detail: 'Steam is holding the trade'});
		throw new HoldError(`offer ${sid} went into a trade hold: nothing more is sent until that is checked`);
	}
	ledger.setOffer(offerId, END[state] || 'failed', {detail: `Steam: ${state}`, final: false});
	return {next: `${listing.user}: ${state}`};
}

// kind 'buy' | 'sell'; candidates = listings best first; build(listing) -> {ok, offer} |
// {ok: false, reason}; start(offer, listing) -> {positionId, offerId} opens the ledger row for
// the first offer (ledger.openBuy / openSell); later ones go to the same position. `extra`
// goes to setOffer on a fill (e.g. {exitReason}). Returns {filled, listing} or {filled: false,
// reasons}.
async function tryOffers(deps, {kind, candidates, build, start, watchId = null, extra = {}}) {
	let positionId = null, reasons = [], tries = 0;
	for (let listing of candidates) {
		if (tries >= deps.cfg.max_tries) break;
		let res = build(listing);
		if (!res.ok) { reasons.push(`${listing.user}: ${res.reason}`); continue; }
		tries++;
		let offerId;
		if (positionId === null) ({positionId, offerId} = start(res.offer, listing));
		else ({offerId} = deps.ledger.addOffer({positionId, offer: res.offer, watchId}));
		if (res.offer.change_scrap) {
			deps.log(`    change: ${res.offer.change_scrap} scrap ${kind === 'buy' ? 'comes back from' : 'of ours goes to'} ${listing.user}`);
		}
		let r;
		try {
			r = await attempt(deps, kind, offerId, res.offer, listing, extra);
		} catch (err) {
			// an offer that ended frees its position; one Steam still has open is left for the
			// restart check (lib/reconcile.js) to read
			if (!deps.ledger.openOffers().some(o => o.id === offerId)) deps.ledger.giveUp(positionId, err.message.slice(0, 300));
			throw err;
		}
		if (r.filled) return {filled: true, positionId, listing};
		deps.log(`    ${r.next}`);
		reasons.push(r.next);
	}
	if (positionId !== null) deps.ledger.giveUp(positionId, `no fill: ${reasons.join('; ').slice(0, 300)}`);
	return {filled: false, positionId, reasons};
}

module.exports = {tryOffers, attempt, newAssetId, HoldError, END};

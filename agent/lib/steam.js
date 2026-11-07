// The Steam side of the live agent: one logged-in trade-offer manager, as promises. Nothing
// here decides anything -- lib/send.js and lib/cycle.js do, over this interface (which the
// tests replace with a fake):
//
//   holdDays(trade_url)        -> {me, them}: days Steam would hold a trade with them (0 = none);
//                                 GetTradeHoldDurations (Web API), the trade-offer page as fallback
//   send(spec)                 -> steam offer id; spec = offers.buyOffer/sellOffer(...).offer; no message
//   confirm(offerId)           -> accept the mobile confirmation with the identity_secret
//   state(offerId)             -> {state: 'Active' | 'Accepted' | ..., tradeId}
//   cancel(offerId)
//   received(offerId)          -> [{assetid, new_assetid, ...}]: what we got, with its new ids
//   sentSince(date)            -> [{id, state, partner, give, receive, created}]: our offers still open
//                                 or changed since `date` (give / receive = asset ids)
//   incoming()                 -> [{id, partner, give, receive, message}]: offers others sent us, still
//                                 open (give / receive = item counts, from our side)
//   decline(offerId)           -> decline one of those; refuses an offer we sent (that would cancel it)
//   inventory()                -> [{assetid, market_hash_name, tradable}]: our tradable TF2 items
//                                 (the public endpoint when the logged-in read fails: lib/inventory.js)
//
// Logs in through lib/session.js (the saved login). Steam's own polling is off: the agent
// reads each offer when it needs to. The calls that only read wait and retry on a 429
// (lib/retry.js); send / confirm / cancel do not.
const TradeOfferManager = require('steam-tradeoffer-manager');
const {webLogin} = require('./session');
const {on429} = require('./retry');
const {holdInput, holdFromApi} = require('./hold');
const {withPublicFallback} = require('./inventory');

const {ETradeOfferState: S, EOfferFilter} = TradeOfferManager;
const TF2 = [440, 2];

const cb = fn => new Promise((resolve, reject) => fn((err, ...out) => err ? reject(err) : resolve(out)));

async function open(acc, {log = console.log} = {}) {
	let {community, cookies, steamid} = await webLogin(acc, {log});
	let manager = new TradeOfferManager({community, pollInterval: -1, dataDirectory: null, useAccessToken: true});
	await cb(done => manager.setCookies(cookies, done));
	log(`steam: logged in as ${steamid}`);
	const read = (what, fn) => on429(fn, {what, log});
	const getOffer = offerId => read('offer', () => cb(done => manager.getOffer(offerId, done)));

	return {
		steamid,

		// the Web API first (lib/hold.js); the trade-offer page only if that fails
		async holdDays(tradeUrl) {
			try {
				let [body] = await read('hold check', () => cb(done =>
					manager._apiCall('GET', 'GetTradeHoldDurations', 1, holdInput(tradeUrl), done)));
				return holdFromApi(body);
			} catch (err) {
				log(`    hold check: Web API failed (${err.message}) -- reading the trade-offer page`);
			}
			let [me, them] = await read('hold check (page)', () => cb(done => manager.createOffer(tradeUrl).getUserDetails(done)));
			return {me: me.escrowDays, them: them.escrowDays};
		},

		async send(spec) {
			let offer = manager.createOffer(spec.trade_url);
			offer.addMyItems(spec.give);
			offer.addTheirItems(spec.receive);
			await cb(done => offer.send(done));
			return String(offer.id);
		},

		async confirm(offerId) {
			await cb(done => community.acceptConfirmationForObject(acc.identity_secret, offerId, done));
		},

		async state(offerId) {
			let [offer] = await getOffer(offerId);
			return {state: S[offer.state], tradeId: offer.tradeID || null};
		},

		async cancel(offerId) {
			let [offer] = await getOffer(offerId);
			await cb(done => offer.cancel(done));
		},

		async received(offerId) {
			let [offer] = await getOffer(offerId);
			let [, , received] = await read('trade details', () => cb(done => offer.getExchangeDetails(done)));
			return received.map(a => ({assetid: String(a.assetid), new_assetid: String(a.new_assetid),
				classid: a.classid, instanceid: a.instanceid}));
		},

		async sentSince(date) {
			let [sent] = await read('sent offers', () => cb(done => manager.getOffers(EOfferFilter.ActiveOnly, date, done)));
			return sent.map(o => ({id: String(o.id), state: S[o.state], partner: o.partner.getSteamID64(),
				give: o.itemsToGive.map(a => String(a.assetid)), receive: o.itemsToReceive.map(a => String(a.assetid)),
				created: o.created ? o.created.toISOString() : null}));
		},

		async incoming() {
			let [, received] = await read('received offers', () => cb(done => manager.getOffers(EOfferFilter.ActiveOnly, done)));
			return received.filter(o => o.state === S.Active && !o.isOurOffer).map(o => ({id: String(o.id),
				partner: o.partner.getSteamID64(), give: o.itemsToGive.length, receive: o.itemsToReceive.length,
				message: o.message || ''}));
		},

		async decline(offerId) {
			let [offer] = await getOffer(offerId);
			if (offer.isOurOffer) throw new Error(`offer ${offerId} is ours: not declining it`);
			await cb(done => offer.decline(done));
		},

		async inventory() {
			return withPublicFallback(async () => {
				let [items] = await read('our inventory', () => cb(done => manager.getInventoryContents(...TF2, true, done)));
				return items.map(it => ({assetid: String(it.assetid), market_hash_name: it.market_hash_name,
					tradable: !!it.tradable}));
			}, steamid, {log});
		},

		close() { manager.shutdown(); },
	};
}

module.exports = {open};

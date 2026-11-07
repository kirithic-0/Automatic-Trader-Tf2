// Stage 6, steps 3.2-3.4 -- trade offers as data: what to request, what to give, to whom.
// Nothing here talks to Steam; sending is the runner's job (a trade-offer manager takes this
// spec as it is). Listings are rows of engine.listings (Python): they already passed the
// fence, bot + instant, untrusted, trade-URL, asset-id and payable checks.
//
//   buyOffer(sell listing, our stock, their stock)   -> request their item, pay the price
//   sellOffer(position, buy order, their stock, our stock) -> give our item, take the price
//   assetCheck(sell listing, their items, item, quality) -> null, or why the asset is not it
//
// Every asset is {appid 440, contextid 2}: an offer can only name TF2 items, whatever another
// game calls its own.
//
// The price settles exactly (currency.settle): the payer's exact pieces, or a little more
// with the other side's metal back as change -- so a missing small piece on either side
// does not stop a trade (3.4: change without smelting). A buy tries the seller's change
// first (we pay in refined, rounded up; user 2026-10-26: keep our small pieces) and our own
// small pieces only after; a sale takes the buyer's exact pieces first. Their stock is
// optional; without it only exact payment works.
//
// With a metal target ({rec, scrap}: limits.json target_rec / target_scrap, user 2026-10-30)
// both a buy and a sale instead pick the split that leaves our reclaimed and scrap nearest
// it (currency.settleToward), so the small pieces hold steady trade after trade.
const {toUnits, settle, settleToward, refString, EMPTY} = require('./currency');

const TF2 = {appid: 440, contextid: '2'};
const TRADE_URL = /^https:\/\/steamcommunity\.com\/tradeoffer\/new\/\?partner=\d+&token=[\w-]+$/;
const asset = assetid => ({...TF2, assetid: String(assetid)});

function check(listing, side) {
	if (listing.side !== side) return `not a ${side} listing`;
	if (listing.reason) return `listing not tradable: ${listing.reason}`;
	if (!listing.trade_url) return 'no trade URL';
	if (!TRADE_URL.test(listing.trade_url)) return `unexpected trade URL: ${listing.trade_url}`;
	if (side === 'sell' && !listing.asset_id) return 'no asset id';
	return null;
}

// Steam's market_hash_name for an engine item (2026-10-28, our own inventory): Strange items
// are `Strange <item>`, Unique items `<item>` or `The <item>` (`Bottle Cap` -> `The Bottle
// Cap`). Any other quality has no rule here: no names, the buy is refused.
const PREFIX = {6: [''], 11: ['Strange ']};
function marketNames(item, quality) {
	let p = PREFIX[Number(quality)] || [];
	return p.flatMap(q => q ? [q + item] : [item, 'The ' + item]);
}

// Before a buy: the asset the listing names is in the seller's TF2 inventory (440/2),
// tradable, and is the item we mean. `items` = their inventory (lib/inventory.js), null when
// it could not be read: then nothing is checked and the buy is refused. A name with anything
// more (Killstreak, Festivized, Australium) is a different good and fails too.
function assetCheck(listing, items, item, quality) {
	if (!items) return 'their inventory could not be read: the asset is not checked';
	let a = items.find(it => it.assetid === String(listing.asset_id));
	if (!a) return `asset ${listing.asset_id} is not in their TF2 inventory`;
	if (!a.tradable) return `asset ${listing.asset_id} is not tradable`;
	let want = marketNames(item, quality);
	if (!want.includes(a.market_hash_name)) {
		return `asset ${listing.asset_id} is "${a.market_hash_name}", not ${want.map(n => `"${n}"`).join(' / ') || `a quality-${quality} item`}`;
	}
	return null;
}

function units(listing) {
	return toUnits({keys: listing.keys, ref: listing.ref});
}

function spec(kind, listing, u, s, receive, give) {
	return {
		kind, partner: listing.steam_id, trade_url: listing.trade_url, listing_id: listing.listing_id,
		receive, give,
		price: {keys: u.keys, ref: refString(u.scrap), total_keys: listing.total_keys},
		change_scrap: s.change,
	};
}

// {ok, offer} or {ok: false, reason}. `have` / `theirs` = currency.stock(...).
// `target` = {rec, scrap} or null (metalTarget(cfg)).
function buyOffer(listing, have, theirs = EMPTY, target = null) {
	let bad = check(listing, 'sell');
	if (bad) return {ok: false, reason: bad};
	let u;
	try { u = units(listing); } catch (err) { return {ok: false, reason: err.message}; }
	let s = target ? settleToward(u, have, theirs, target, 'payer') : settle(u, have, theirs, 18, {preferChange: true});
	if (!s.ok) return s;
	return {ok: true, offer: spec('buy', listing, u, s,
		[asset(listing.asset_id), ...s.payee.map(asset)], s.payer.map(asset))};
}

// Sell the position's item (its asset id in our inventory) into a buy order.
function sellOffer(position, listing, theirs, have = EMPTY, target = null) {
	let bad = check(listing, 'buy');
	if (bad) return {ok: false, reason: bad};
	if (!position.asset_id) return {ok: false, reason: 'the position has no asset id (not received yet?)'};
	let u;
	try { u = units(listing); } catch (err) { return {ok: false, reason: err.message}; }
	let s = target ? settleToward(u, theirs, have, target, 'payee') : settle(u, theirs, have);
	if (!s.ok) return {ok: false, reason: `buyer can't pay: ${s.reason}`};
	return {ok: true, offer: spec('sell', listing, u, s,
		s.payer.map(asset), [asset(position.asset_id), ...s.payee.map(asset)])};
}

// limits.json's metal target, or null when it sets none.
function metalTarget(cfg) {
	if (!cfg || cfg.target_rec == null || cfg.target_scrap == null) return null;
	return {rec: cfg.target_rec, scrap: cfg.target_scrap};
}

module.exports = {TF2, buyOffer, sellOffer, marketNames, assetCheck, metalTarget};

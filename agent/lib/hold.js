// The trade-hold check without the trade-offer page (2026-10-28). getUserDetails loads
// steamcommunity.com/tradeoffer/new/ and scrapes it; Steam rate-limits that page hard since
// ~May 2025 (dev.doctormckay.com topics 5692 / 5696 / 5903) and it 429'd twice live. The
// Web API method IEconService/GetTradeHoldDurations answers the same question from the
// partner's SteamID + the token in their trade URL -- what tf2autobot (v5.18.1) does for
// offers it sends to a trade URL. Pure helpers; lib/steam.js makes the call.
const STEAMID64_BASE = 0x0110000100000000n;   // Steam's SteamID64 base (account id 0)

// steamid_target + trade_offer_access_token from https://steamcommunity.com/tradeoffer/new/?partner=<accountid>&token=<t>
function holdInput(tradeUrl) {
	let q = new URL(tradeUrl).searchParams;
	let partner = q.get('partner');
	if (!/^\d+$/.test(partner || '')) throw new Error(`trade URL has no partner id: ${tradeUrl}`);
	return {steamid_target: String(STEAMID64_BASE + BigInt(partner)), trade_offer_access_token: q.get('token') || ''};
}

const days = e => Math.ceil(Number((e && e.escrow_end_duration_seconds) || 0) / 86400);

// {me, them} in days from the API's {response: {my_escrow, their_escrow, both_escrow}}. A
// hold only `both_escrow` names is put on them: it skips that bot rather than stopping ours.
function holdFromApi(body) {
	let r = body && body.response;
	if (!r || !(r.my_escrow || r.their_escrow || r.both_escrow)) throw new Error('GetTradeHoldDurations: no escrow data in the answer');
	let me = days(r.my_escrow), them = days(r.their_escrow);
	if (!me && !them) them = days(r.both_escrow);
	return {me, them};
}

module.exports = {holdInput, holdFromApi};

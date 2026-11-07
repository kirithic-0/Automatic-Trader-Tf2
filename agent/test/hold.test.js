// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const {holdInput, holdFromApi} = require('../lib/hold');

test('the API input comes from the trade URL: account id -> SteamID64, the token as is', () => {
	// made-up partner ids and token: SteamID64 = Steam's base + the partner (account) id
	assert.deepEqual(holdInput('https://steamcommunity.com/tradeoffer/new/?partner=1234567&token=fakeTok1'),
		{steamid_target: String(0x0110000100000000n + 1234567n), trade_offer_access_token: 'fakeTok1'});
	assert.deepEqual(holdInput('https://steamcommunity.com/tradeoffer/new/?partner=7654321'),
		{steamid_target: String(0x0110000100000000n + 7654321n), trade_offer_access_token: ''});
	assert.throws(() => holdInput('https://steamcommunity.com/tradeoffer/new/?token=x'), /no partner id/);
});

test('no hold on either side', () => {
	let body = {response: {my_escrow: {escrow_end_duration_seconds: 0}, their_escrow: {escrow_end_duration_seconds: 0},
		both_escrow: {escrow_end_duration_seconds: 0}}};
	assert.deepEqual(holdFromApi(body), {me: 0, them: 0});
});

test('holds in seconds become whole days, per side', () => {
	let body = {response: {my_escrow: {escrow_end_duration_seconds: 0}, their_escrow: {escrow_end_duration_seconds: 15 * 86400},
		both_escrow: {escrow_end_duration_seconds: 15 * 86400}}};
	assert.deepEqual(holdFromApi(body), {me: 0, them: 15});
	body.response.my_escrow.escrow_end_duration_seconds = 3600;   // part of a day still counts
	assert.deepEqual(holdFromApi(body), {me: 1, them: 15});
});

test('a hold only both_escrow names goes on them (skip the bot), never on us', () => {
	let body = {response: {my_escrow: {}, their_escrow: {}, both_escrow: {escrow_end_duration_seconds: 2 * 86400}}};
	assert.deepEqual(holdFromApi(body), {me: 0, them: 2});
});

test('an answer without escrow data is an error (the page check takes over)', () => {
	assert.throws(() => holdFromApi({response: {}}), /no escrow data/);
	assert.throws(() => holdFromApi(null), /no escrow data/);
});

// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const {withPublicFallback} = require('../lib/inventory');

const REFRESH = new Error('EYldRefreshAppIfNecessary(440) failed with EResult 55');   // live 2026-11-01
const PUBLIC = [{assetid: '1', market_hash_name: 'Refined Metal', tradable: true},
	{assetid: '2', market_hash_name: 'Mercenary', tradable: false}];

test('a working read is returned and the public endpoint is not touched', async () => {
	let asked = 0;
	let got = await withPublicFallback(async () => ['ours'], '765', {fallback: async () => { asked++; return []; }});
	assert.deepEqual(got, ['ours']);
	assert.equal(asked, 0);
});

test('the refresh error reads the public inventory, tradable items only, and logs it', async () => {
	let lines = [], asked = [];
	let got = await withPublicFallback(async () => { throw REFRESH; }, '765',
		{log: l => lines.push(l), fallback: async id => { asked.push(id); return PUBLIC; }});
	assert.deepEqual(got, [PUBLIC[0]]);
	assert.deepEqual(asked, ['765']);
	assert.match(lines[0], /EResult 55 -- reading the public inventory instead/);
});

test('any failure falls back -- the duplicate-request reply too; a failing fallback throws', async () => {
	let asked = 0;
	let got = await withPublicFallback(async () => {
		throw new Error('The request is a duplicate and the action has already occurred in the past, ignored this time');
	}, '765', {fallback: async () => { asked++; return PUBLIC; }});                   // live 2026-11-01 11:56
	assert.deepEqual(got, [PUBLIC[0]]);
	assert.equal(asked, 1);
	await assert.rejects(withPublicFallback(async () => { throw REFRESH; }, '765',
		{fallback: async () => { throw new Error('inventory HTTP 500'); }}), /inventory HTTP 500/);
});

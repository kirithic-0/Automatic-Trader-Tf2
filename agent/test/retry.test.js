// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const {on429, is429} = require('../lib/retry');

// fails with `errs` in turn, then returns 'ok'; sleeps are recorded, not waited
function flaky(errs) {
	let calls = 0, slept = [], lines = [];
	let fn = async () => { if (calls < errs.length) throw errs[calls++]; calls++; return 'ok'; };
	let opts = {waits: [20000, 40000], sleep: async ms => { slept.push(ms); }, log: l => lines.push(l)};
	return {fn, opts, slept, lines, calls: () => calls};
}

test('is429 knows the messages Steam and the inventory loader give', () => {
	assert.ok(is429(new Error('HTTP error 429')));          // steamcommunity / tradeoffer-manager (live 10-28)
	assert.ok(is429(new Error('inventory HTTP 429')));      // lib/inventory.js
	assert.ok(is429(Object.assign(new Error('x'), {statusCode: 429})));
	assert.ok(!is429(new Error('HTTP error 503')));
	assert.ok(!is429(new Error('asset 14290 gone')));
	assert.ok(!is429(null));
});

test('a 429 waits and retries, then returns', async () => {
	let f = flaky([new Error('HTTP error 429')]);
	assert.equal(await on429(f.fn, {...f.opts, what: 'hold check'}), 'ok');
	assert.deepEqual(f.slept, [20000]);
	assert.equal(f.calls(), 2);
	assert.match(f.lines[0], /hold check: .*429.*waiting 20 s/);
});

test('two 429s use both waits; a third gives up with the error', async () => {
	let f = flaky([new Error('HTTP error 429'), new Error('HTTP error 429')]);
	assert.equal(await on429(f.fn, f.opts), 'ok');
	assert.deepEqual(f.slept, [20000, 40000]);

	let g = flaky([1, 2, 3].map(() => new Error('HTTP error 429')));
	await assert.rejects(on429(g.fn, g.opts), /429/);
	assert.deepEqual(g.slept, [20000, 40000]);
	assert.equal(g.calls(), 3);
});

test('any other error is not retried', async () => {
	let f = flaky([new Error('HTTP error 503')]);
	await assert.rejects(on429(f.fn, f.opts), /503/);
	assert.deepEqual(f.slept, []);
	assert.equal(f.calls(), 1);
});

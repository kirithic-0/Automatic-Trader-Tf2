// Steam answers HTTP 429 (too many requests) when the agent makes several community calls
// within seconds -- live 2026-10-27 and 10-28: the hold-days check right after loading two
// inventories, and the whole cycle failed (the buy was dropped; nothing had been sent).
// on429 waits and tries again. Only for calls that read: send / confirm / cancel are never
// retried here (a change must not be sent twice); the restart check reads those back.
const WAITS_MS = [20000, 40000];

const is429 = err => !!err && (err.statusCode === 429 || err.status === 429 || /\b429\b/.test(String(err.message)));

async function on429(fn, {what = 'steam', waits = WAITS_MS, log = () => {},
	sleep = ms => new Promise(res => setTimeout(res, ms))} = {}) {
	for (let i = 0; ; i++) {
		try {
			return await fn();
		} catch (err) {
			if (!is429(err) || i >= waits.length) throw err;
			log(`    ${what}: Steam says too many requests (429) -- waiting ${Math.round(waits[i] / 1000)} s`);
			await sleep(waits[i]);
		}
	}
}

module.exports = {on429, is429, WAITS_MS};

// Requests to the running agent from outside it (`agent restart` / `shutdown` / `cycle`, the
// panel's buttons): a file per request in the secrets folder, taken by the run loop between
// cycles -- never in the middle of one, so no trade is cut off (user, 2026-10-30).
//
//   RESTART    exit after the cycle with code 75; run.cmd / run-auto.cmd start it again in
//              the same window and mode (new code, a fresh login, limits re-read)
//   SHUTDOWN   exit after the cycle, and stay off
//   CYCLE_NOW  stop waiting: the next cycle starts now
//
//   request(name, reason)   pending()   cancel(name)   take(name) -> reason | null
//   wait(ms, sleep) -> 'restart' | 'shutdown' | 'cycle' | 'timeout'   (checks every 2 s)
const fs = require('fs');
const path = require('path');
const secrets = require('./secrets');

const NAMES = {restart: 'RESTART', shutdown: 'SHUTDOWN', cycle: 'CYCLE_NOW'};
const RESTART_CODE = 75;
const file = (name, dir = secrets.DIR) => {
	if (!NAMES[name]) throw new Error(`unknown request ${name}: ${Object.keys(NAMES).join(' / ')}`);
	return path.join(dir, NAMES[name]);
};

function request(name, reason = 'by hand', dir = secrets.DIR) {
	fs.mkdirSync(dir, {recursive: true});
	fs.writeFileSync(file(name, dir), `${new Date().toISOString()} ${reason}\n`);
}

// {restart: '<when> <why>', ...} for the requests not taken yet.
function pending(dir = secrets.DIR) {
	let out = {};
	for (let name of Object.keys(NAMES)) {
		try { out[name] = fs.readFileSync(file(name, dir), 'utf8').trim(); } catch (err) { /* none */ }
	}
	return out;
}

function cancel(name, dir = secrets.DIR) {
	try { fs.unlinkSync(file(name, dir)); return true; } catch (err) { return false; }
}

function take(name, dir = secrets.DIR) {
	let f = file(name, dir);
	try {
		let reason = fs.readFileSync(f, 'utf8').trim();
		fs.unlinkSync(f);
		return reason;
	} catch (err) { return null; }
}

// The wait between cycles, cut short by a request: shutdown first, then restart, then cycle.
async function wait(ms, sleep, dir = secrets.DIR, step = 2000) {
	for (let left = ms; ; ) {
		for (let name of ['shutdown', 'restart', 'cycle']) if (fs.existsSync(file(name, dir))) return name;
		if (left <= 0) return 'timeout';
		let s = Math.min(step, left);
		await sleep(s);
		left -= s;
	}
}

module.exports = {NAMES, RESTART_CODE, request, pending, cancel, take, wait};

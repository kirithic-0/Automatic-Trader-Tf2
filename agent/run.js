// Stage 6, Phase 5 -- the live agent: real offers, every one approved by the user here.
//
//   run.cmd                           double-click: the loop, until the window is closed
//   node run.js                       the same (VM settings from %USERPROFILE%\.steam-bot\agent.json)
//   node run.js --once                one cycle, then exit
//   run-auto.cmd / node run.js --auto no approvals: every trade inside the limits goes out on its
//                                     own (user, 2026-10-27: the first overnight run)
//   node run.js --ssh azureuser@<vm-ip> --key <pem>   settings on the command line instead
//
// At start: take the account's lock (lib/lock.js: a second agent refuses to start), log in with the saved login (the password only the first time, lib/session.js).
// Every --every seconds (default 600): pull the VM's collection, read back from Steam every
// offer still open (lib/reconcile.js), sell what the exit rule says to, and on a new pass buy
// what the picker picks (forecast.AGENT_RULE) inside the limits (limits.json, STOP file).
// Each trade asks here first: y within 15 min, or it is skipped. The ledger is
// backpack/agent.db, the log backpack/logs/agent.log; `node report.js` = the results so far.
//
// Between cycles it takes the requests of lib/control.js (`agent restart` / `shutdown` / `cycle`,
// the panel's buttons): restart = exit code 75, which run.cmd / run-auto.cmd start again in the
// same window; shutdown = exit and stay off; cycle = stop waiting. limits.json is re-read at
// the start of every cycle (a change applies from the next one).
//
// Stop it any way (close the window, Ctrl+C, switch off): the next start reads every offer
// that was out back from Steam before it sends anything. While it is off nothing is bought
// or sold.
const fs = require('fs');
const path = require('path');
const {Ledger, DEFAULT_PATH} = require('./lib/ledger');
const {engine, pull, BACKPACK} = require('./lib/python');
const {tf2Inventory} = require('./lib/inventory');
const tunnel = require('./lib/tunnel');
const limits = require('./lib/limits');
const control = require('./lib/control');
const secrets = require('./lib/secrets');
const lock = require('./lib/lock');
const steamLib = require('./lib/steam');
const {approve} = require('./lib/approve');
const {cycle, MODE} = require('./lib/cycle');
const {summary} = require('./lib/report');
const {DatabaseSync} = require('node:sqlite');

const SETTINGS = path.join(secrets.DIR, 'agent.json');   // {"ssh": "user@vm", "key": "<pem path>"}

// The newest pass in trading_app.db (read-only): no newer pass, no picks to run.
function latestPass() {
	let db = new DatabaseSync(path.join(BACKPACK, 'trading_app.db'), {readOnly: true});
	try { return db.prepare('SELECT max(id) AS p FROM passes').get().p; } finally { db.close(); }
}

function args(argv) {
	let a = {every: 600, once: false, ledger: DEFAULT_PATH, pull: true, auto: false};
	for (let i = 0; i < argv.length; i++) {
		let k = argv[i];
		if (k === '--once') a.once = true;
		else if (k === '--auto') a.auto = true;
		else if (k === '--no-pull') a.pull = false;
		else if (['--ssh', '--key', '--every', '--ledger', '--account'].includes(k)) a[k.slice(2)] = argv[++i];
		else throw new Error(`unknown argument ${k}`);
	}
	if (!a.ssh) {
		if (!fs.existsSync(SETTINGS)) {
			throw new Error(`no --ssh and no ${SETTINGS}: write {"ssh": "azureuser@<vm-ip>", "key": "<pem path>"} there`);
		}
		let s = JSON.parse(fs.readFileSync(SETTINGS, 'utf8'));
		a.ssh = s.ssh;
		a.key = a.key || s.key;
	}
	a.every = Number(a.every);
	return a;
}

function logger() {
	let dir = path.join(BACKPACK, 'logs');
	fs.mkdirSync(dir, {recursive: true});
	let file = path.join(dir, 'agent.log');
	return line => {
		let s = `${new Date().toISOString()} ${line}`;
		console.log(s);
		fs.appendFileSync(file, s + '\n');
	};
}

async function main() {
	let a = args(process.argv.slice(2));
	if (!a.auto && !process.stdin.isTTY) throw new Error('run it in a console window: every trade asks for your approval here');
	let acc = secrets.pick(a.account);
	let release = lock.acquire(path.join(secrets.DIR, `${acc.account_name}.lock`));   // one agent per account
	process.on('exit', release);
	let log = logger();
	// --auto: the limits are the only gate; each trade is logged as approved by the mode
	let ok = a.auto ? async q => { log(`  auto: ${q}`); return true; } : approve;
	if (a.auto) log('AUTO MODE: no approvals -- every trade inside limits.json goes out on its own');
	for (let name of Object.keys(control.NAMES)) {             // left from before this start: not for us
		let old = control.take(name);
		if (old) log(`dropped a ${name} request from before this start (${old})`);
	}
	let ledger = new Ledger(a.ledger);
	let cfg = limits.load();
	log(`agent: ledger ${a.ledger}, every ${a.every} s, limits ${JSON.stringify(Object.fromEntries(
		Object.entries(cfg).filter(([k]) => !k.startsWith('_'))))}`);
	for (let line of summary(ledger, MODE)) log(line);

	let steam = await steamLib.open(acc, {log});
	let state = {lastPassId: ledger.lastPass(MODE)};   // survives a restart
	let t = null;
	let quit = () => { if (t) t.close(); steam.close(); ledger.close(); process.exit(130); };
	process.on('SIGINT', quit);
	process.on('SIGBREAK', quit);   // the console window closed (Windows)

	let code = 0;
	for (;;) {
		let started = Date.now();
		cfg = limits.reloaded(cfg, log);
		try {
			if (!t || !t.alive()) {
				t = await tunnel.open(a.ssh, a.key);
				log(`tunnel: ${t.target}${t.reused ? ' (reused from an earlier run)' : ''}`);
			}
			if (a.pull) log(`pull: ${await pull(a.ssh, a.key)}`);
			let r = await cycle({engine, steam, inventory: id => tf2Inventory(id, {log}), ledger, ledgerPath: a.ledger, target: t.target,
				cfg, log, approve: ok, latestPass, sleep: ms => new Promise(res => setTimeout(res, ms))}, state);
			let ex = ledger.exposure(MODE);
			log(`cycle: sold ${r.sold}, bought ${r.bought}${r.converted ? `, ${r.converted} key(s) converted` : ""}; holding ${ex.open} (${ex.keys.toFixed(4)} keys)`);
		} catch (err) {
			log(`cycle failed: ${err.message} -- retrying next cycle`);
			if (t) t.close();
			t = null;               // re-probe the tunnel next time (the VM or the network may be back)
		}
		if (a.once) break;
		let wait = Math.max(0, a.every * 1000 - (Date.now() - started));
		log(`next cycle in ${Math.round(wait / 1000)} s (close the window to stop)`);
		let why = await control.wait(wait, ms => new Promise(r => setTimeout(r, ms)));
		if (why === 'cycle') log(`cycle now: ${control.take('cycle')}`);
		if (why === 'restart' || why === 'shutdown') {
			log(`${why.toUpperCase()} between cycles, nothing in flight: ${control.take(why)}`);
			code = why === 'restart' ? control.RESTART_CODE : 0;
			break;
		}
	}
	if (t) t.close();
	steam.close();
	ledger.close();
	process.exitCode = code;
}

main().catch(err => { console.error(`Stopped: ${err.message}`); process.exitCode = 1; });

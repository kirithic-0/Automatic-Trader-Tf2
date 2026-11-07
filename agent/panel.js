// The live agent as one JSON document for `engine panel`'s Agent tab (lib/snapshot.js):
// state (running, mode, kill switch, buys paused, requests waiting), limits, results, every
// trade, the bots, the offer funnel, changes made by hand, the log's last lines. Read-only.
//   node panel.js            (the panel runs it; `--pretty` to read it yourself)
const fs = require('fs');
const path = require('path');
const limits = require('./lib/limits');
const secrets = require('./lib/secrets');
const control = require('./lib/control');
const {alive} = require('./lib/lock');
const {BACKPACK} = require('./lib/python');
const {Ledger} = require('./lib/ledger');
const {logTail} = require('./lib/trades');
const {snapshot} = require('./lib/snapshot');

const text = f => { try { return fs.readFileSync(f, 'utf8').trim(); } catch (err) { return null; } };

let pid = null;
try {
	let acc = secrets.pick(process.argv.find(a => !a.startsWith('-') && a !== process.argv[0] && a !== process.argv[1]));
	pid = Number(text(path.join(secrets.DIR, `${acc.account_name}.lock`))) || null;
} catch (err) { /* no account set up here: not running */ }
let cfg = null;
try { cfg = limits.load(); } catch (err) { /* shown as missing */ }
let on = limits.stopped();
let ledger = new Ledger();
let out = snapshot({ledger, lines: logTail(path.join(BACKPACK, 'logs', 'agent.log'), 2e6), cfg, pid, isAlive: alive,
	kill: on ? text(on.match(/\((.*)\)/)[1]) : null, paused: text(limits.PAUSED), resumedAt: limits.resumedAt(),
	pending: control.pending()});
ledger.close();
process.stdout.write(JSON.stringify(out, null, process.argv.includes('--pretty') ? 1 : 0));

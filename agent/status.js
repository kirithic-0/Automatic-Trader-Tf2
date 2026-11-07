// Everything at a glance: is the agent running (its lock, its log), in which mode, the kill
// switch and the win-rate stop, what it holds, and how the last cycle went. Read-only: it
// never logs in to Steam.   node status.js   (agent.cmd status)
const fs = require('fs');
const path = require('path');
const limits = require('./lib/limits');
const secrets = require('./lib/secrets');
const {alive} = require('./lib/lock');
const {BACKPACK} = require('./lib/python');
const {Ledger} = require('./lib/ledger');
const {summary} = require('./lib/report');
const {logTail} = require('./lib/trades');
const {agentState} = require('./lib/snapshot');
const control = require('./lib/control');

const ago = iso => {
	let s = Math.round((Date.now() - Date.parse(iso)) / 1000);
	return s < 120 ? `${s} s ago` : s < 7200 ? `${Math.round(s / 60)} min ago` : `${(s / 3600).toFixed(1)} h ago`;
};

let acc = secrets.pick(process.argv[2]);
let lockFile = path.join(secrets.DIR, `${acc.account_name}.lock`);
let pid = fs.existsSync(lockFile) ? Number(fs.readFileSync(lockFile, 'utf8')) : null;
let lines = logTail(path.join(BACKPACK, 'logs', 'agent.log'), 200e3);
let st = agentState(lines, pid, alive);
let mode = {auto: 'AUTO (no approvals)', manual: 'approving each trade'}[st.mode] || '?';
if (st.running) console.log(`agent: RUNNING (process ${pid}), ${mode}, started ${st.started_at ? ago(st.started_at) : '?'}`);
else console.log(`agent: not running by its lock${pid ? ` (stale lock, process ${pid} is gone)` : ''}; the log last wrote ${st.last_line_at ? ago(st.last_line_at) : 'never'}`);

let on = limits.stopped();
console.log(on ? `kill switch: ON -- ${fs.readFileSync(on.match(/\((.*)\)/)[1], 'utf8').trim()}` : 'kill switch: off');
if (fs.existsSync(limits.PAUSED)) console.log(`buys: PAUSED -- ${fs.readFileSync(limits.PAUSED, 'utf8').trim()}`);

let ledger = new Ledger();
for (let l of summary(ledger, 'live')) console.log(l);
ledger.close();

if (st.last_cycle) console.log(`last cycle (${ago(st.last_cycle.at)}): ${st.last_cycle.text}`);
if (st.cycle_since) console.log(`a cycle is running, since ${ago(st.cycle_since)}`);
if (st.next_cycle_at) {
	let s = Math.round((Date.parse(st.next_cycle_at) - Date.now()) / 1000);
	console.log(s >= 0 ? `next cycle in ${s} s` : `next cycle was due ${-s} s ago`);
}
for (let [k, v] of Object.entries(control.pending())) console.log(`waiting for the agent: ${k} (${v})`);

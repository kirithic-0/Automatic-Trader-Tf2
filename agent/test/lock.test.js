// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {spawnSync} = require('child_process');
const {acquire} = require('../lib/lock');

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'lock-')), 'acct.lock');

test('one agent per account: a live holder refuses a second, release frees it', () => {
	let file = tmp();
	fs.writeFileSync(file, String(process.ppid));   // a process that is running: our parent
	assert.throws(() => acquire(file), /another agent is running/);
	fs.unlinkSync(file);
	let release = acquire(file);
	assert.equal(fs.readFileSync(file, 'utf8'), String(process.pid));
	release();
	assert.equal(fs.existsSync(file), false);
});

test('a lock whose agent is gone is taken over', () => {
	let file = tmp();
	let dead = spawnSync(process.execPath, ['-e', 'console.log(process.pid)']).stdout.toString().trim();
	fs.writeFileSync(file, dead);
	let release = acquire(file);
	assert.equal(fs.readFileSync(file, 'utf8'), String(process.pid));
	release();
});

test('a lock whose id now belongs to another program (a restart reused it) is taken over', () => {
	let file = tmp();
	// a live non-node process: on Windows the system's own (pid 4 = System); elsewhere pid 1 (init)
	let sys = process.platform === 'win32' ? 4 : 1;
	fs.writeFileSync(file, String(sys));
	let release = acquire(file);
	assert.equal(fs.readFileSync(file, 'utf8'), String(process.pid));
	release();
});

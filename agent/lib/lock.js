// One live agent at a time. Two (run.cmd and run-auto.cmd both open) would share one ledger:
// each one's restart check cancels the other's offers in flight, and both buy from the same
// pass. The lock is a file holding the running agent's process id; a file whose process is
// gone (the PC switched off, a crash) is taken over -- and so is one whose id now belongs to a
// process that is not node (2026-10-31: after a restart Windows gave the old agent's id to
// svchost.exe, and the stale lock stopped every start).
//
//   acquire(file) -> release()   throws when another live agent holds it
const fs = require('fs');
const path = require('path');
const {spawnSync} = require('child_process');

function alive(pid) {
	try { process.kill(pid, 0); return true; } catch (err) { return err.code === 'EPERM'; }
}

// The process's program name (node.exe, svchost.exe, ...), or null when it can't be read.
function imageName(pid) {
	try {
		if (process.platform === 'win32') {
			let r = spawnSync('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'], {encoding: 'utf8', timeout: 10e3});
			let m = /^"([^"]+)","(\d+)"/m.exec(r.stdout || '');
			return m && Number(m[2]) === pid ? m[1] : null;
		}
		let r = spawnSync('ps', ['-p', String(pid), '-o', 'comm='], {encoding: 'utf8', timeout: 10e3});
		return (r.stdout || '').trim() || null;
	} catch (err) { return null; }
}

// Is `pid` an agent? Alive and running node; a name that can't be read counts as one (refuse
// rather than run two).
function agentAlive(pid) {
	if (!alive(pid)) return false;
	let name = imageName(pid);
	return name === null || /^node(\.exe)?$/i.test(path.basename(name));
}

function acquire(file) {
	fs.mkdirSync(path.dirname(file), {recursive: true});
	for (let i = 0; i < 2; i++) {
		try {
			fs.writeFileSync(file, String(process.pid), {flag: 'wx'});
			return () => { try { if (fs.readFileSync(file, 'utf8') === String(process.pid)) fs.unlinkSync(file); } catch (err) { /* gone */ } };
		} catch (err) {
			if (err.code !== 'EEXIST') throw err;
		}
		let pid = Number(fs.readFileSync(file, 'utf8'));
		if (pid && pid !== process.pid && agentAlive(pid)) {
			throw new Error(`another agent is running (process ${pid}, ${file}): close it first; ` +
				`if none is, delete that file`);
		}
		fs.unlinkSync(file);   // its agent is gone
	}
	throw new Error(`could not take the lock ${file}`);
}

module.exports = {acquire, alive, agentAlive, imageName};

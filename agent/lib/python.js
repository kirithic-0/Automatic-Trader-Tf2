// Run one engine screen (`python -m engine <screen> ... --json <tmp>`) from backpack/ and
// return {code, data}: the exit code and the JSON it wrote. Python decides (picks, recheck,
// exits); Node acts on what it says.
const fs = require('fs');
const os = require('os');
const path = require('path');
const {execFile} = require('child_process');

const BACKPACK = path.join(__dirname, '..', '..', 'backpack');
const PYTHON = process.env.AGENT_PYTHON || 'python';   // the engine's Python (3.10, CLAUDE handoff)

function engine(screen, args, {timeoutMs = 600e3} = {}) {
	let out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'engine-')), 'out.json');
	return new Promise((resolve, reject) => {
		execFile(PYTHON, ['-W', 'ignore', '-m', 'engine', screen, ...args, '--json', out],
			{cwd: BACKPACK, timeout: timeoutMs, maxBuffer: 16 << 20, env: {...process.env, PYTHONIOENCODING: 'utf-8'}},
			(err, stdout, stderr) => {
				let code = err ? (typeof err.code === 'number' ? err.code : -1) : 0;
				let data = null;
				try { data = JSON.parse(fs.readFileSync(out, 'utf8')); } catch (e) { /* no JSON written */ }
				fs.rmSync(path.dirname(out), {recursive: true, force: true});
				if (data === null && code !== 0) {
					return reject(new Error(`engine ${screen} failed (${code}): ${(stderr || stdout || String(err)).trim().slice(-500)}`));
				}
				resolve({code, data, stdout});
			});
	});
}

// Pull the VM's collection into trading_app.db (sync/pull.py), so picks see the newest pass.
function pull(ssh, key, {timeoutMs = 600e3} = {}) {
	let args = [path.join(BACKPACK, '..', 'sync', 'pull.py'), '--ssh', ssh];
	if (key) args.push('--key', key);
	return new Promise((resolve, reject) => {
		execFile(PYTHON, args, {cwd: BACKPACK, timeout: timeoutMs, maxBuffer: 16 << 20,
			env: {...process.env, PYTHONIOENCODING: 'utf-8'}}, (err, stdout, stderr) => {
			if (err) return reject(new Error(`pull failed: ${(stderr || stdout || String(err)).trim().slice(-500)}`));
			resolve(stdout.trim().split('\n').pop());
		});
	});
}

module.exports = {engine, pull, BACKPACK};

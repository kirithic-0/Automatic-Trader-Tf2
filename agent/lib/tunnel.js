// One long-lived SSH tunnel to the VM's book server (127.0.0.1:50052 there), as
// sync/pull.py opens for the sync server. Returns {target: '127.0.0.1:<port>', close(), alive()}.
//
// The agent may be stopped any way (Ctrl+C, the window closed, the PC switched off), and
// on Windows its `ssh` child can outlive it. So a port that already answers the book
// server's /health is reused as it is; a port taken by something else is skipped.
const net = require('net');
const {spawn} = require('child_process');

const REMOTE_PORT = 50052;            // = book_server.DEFAULT_PORT on the VM
const PORTS = [50062, 50063, 50064, 50065, 50066];

function canConnect(port) {
	return new Promise(resolve => {
		let s = net.connect(port, '127.0.0.1', () => { s.destroy(); resolve(true); });
		s.on('error', () => resolve(false));
	});
}

async function healthy(port) {
	try {
		let r = await fetch(`http://127.0.0.1:${port}/health`, {signal: AbortSignal.timeout(5000)});
		return r.ok && (await r.json()).ok === true;
	} catch (err) {
		return false;
	}
}

async function spawnOn(ssh, key, port, waitMs) {
	let args = ['-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'BatchMode=yes',
		'-L', `${port}:127.0.0.1:${REMOTE_PORT}`];
	if (key) args.push('-i', key);
	args.push(ssh);
	let proc = spawn('ssh', args, {stdio: ['ignore', 'ignore', 'pipe']});
	let err = '';
	proc.stderr.on('data', d => { err += d; });
	let exited = null;
	proc.on('exit', code => { exited = code; });
	for (let t = 0; t < waitMs; t += 250) {
		if (exited !== null) throw new Error(`ssh tunnel exited (${exited}): ${err.trim()}`);
		if (await canConnect(port)) {
			return {target: `127.0.0.1:${port}`, close: () => proc.kill(), alive: () => exited === null, reused: false};
		}
		await new Promise(r => setTimeout(r, 250));
	}
	proc.kill();
	throw new Error(`ssh tunnel: port ${port} not up in ${waitMs} ms`);
}

async function open(ssh, key, {ports = PORTS, waitMs = 20000} = {}) {
	for (let port of ports) {
		if (await canConnect(port)) {
			if (await healthy(port)) {   // a tunnel left by an earlier run: use it
				return {target: `127.0.0.1:${port}`, close: () => {}, alive: () => true, reused: true};
			}
			continue;                    // taken by something else
		}
		return spawnOn(ssh, key, port, waitMs);
	}
	throw new Error(`ssh tunnel: ports ${ports.join(', ')} all taken`);
}

module.exports = {open, healthy};

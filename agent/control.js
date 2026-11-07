// Ask the running agent to do something between cycles (lib/control.js): it never cuts a
// cycle short, so no trade is left half-done.
//   node control.js restart [why]    exit after the cycle and start again (new code, limits)
//   node control.js shutdown [why]   exit after the cycle and stay off
//   node control.js cycle [why]      start the next cycle now instead of waiting
//   node control.js cancel <name>    take back a request not acted on yet
//   node control.js status           the requests waiting
const control = require('./lib/control');

let [cmd, ...rest] = process.argv.slice(2);
if (['restart', 'shutdown', 'cycle'].includes(cmd)) {
	control.request(cmd, rest.join(' ') || 'by hand');
	console.log(`${cmd} asked for: the agent does it between cycles`);
} else if (cmd === 'cancel') {
	console.log(control.cancel(rest[0]) ? `${rest[0]} request taken back` : `no ${rest[0]} request waiting`);
} else if (cmd !== 'status') {
	console.error('usage: node control.js restart|shutdown|cycle [why] | cancel <name> | status');
	process.exit(2);
}
let p = control.pending();
console.log(Object.keys(p).length ? Object.entries(p).map(([k, v]) => `waiting: ${k} (${v})`).join('\n') : 'no requests waiting');

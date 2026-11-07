// The kill switch.  node stop.js        -> nothing is sent until turned off
//                   node stop.js off    -> sending allowed again
//                   node stop.js resume -> after the win-rate stop: start a new window of
//                                          sales (only sales from now on count)
//                   node stop.js status -> show the state
const fs = require('fs');
const limits = require('./lib/limits');

let arg = process.argv[2];
if (arg === 'off') {
	for (let f of limits.STOP_FILES) if (fs.existsSync(f)) fs.unlinkSync(f);
} else if (arg === 'resume') {
	limits.resume();
	console.log(`win-rate window restarted ${limits.resumedAt()}: only sales from now on count`);
} else if (arg !== 'status') {
	limits.stop('by hand (node stop.js)');
}
let on = limits.stopped();
console.log(on ? `${on}: ${fs.readFileSync(on.match(/\((.*)\)/)[1], 'utf8').trim()}` : 'kill switch off: sending allowed');
if (limits.resumedAt()) console.log(`win-rate window counts sales since ${limits.resumedAt()}`);
if (fs.existsSync(limits.PAUSED)) console.log(`buys paused: ${fs.readFileSync(limits.PAUSED, 'utf8').trim()}`);

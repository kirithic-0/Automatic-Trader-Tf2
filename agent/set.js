// Set one limit in limits.json (lib/limits.js set: checked whole before it replaces the
// file). The running agent reads it at its next cycle.
//   node set.js <limit> <value>     e.g. node set.js max_keys_in_play 3
//   node set.js                     the limits now
const limits = require('./lib/limits');

let [key, value] = process.argv.slice(2);
try {
	if (key) {
		let r = limits.set(key, value);
		console.log(`${r.key}: ${r.old} -> ${r.value} (the agent uses it from its next cycle)`);
	} else {
		for (let [k, v] of Object.entries(limits.load())) if (!k.startsWith('_')) console.log(`${k} ${v}`);
	}
} catch (err) { console.error(err.message); process.exitCode = 1; }

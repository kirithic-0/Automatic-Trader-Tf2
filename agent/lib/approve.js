// The user's approval of one trade, in the agent's own window (user, 2026-10-26: every buy
// and sell is approved by hand at first; a dashboard on 127.0.0.1 comes later). No answer
// within `waitS` seconds = no.
//
//   await approve('BUY Hat (Unique) from bot X at 0.5000 keys?', 900)   -> true | false
const readline = require('readline/promises');

async function approve(question, waitS) {
	let rl = readline.createInterface({input: process.stdin, output: process.stdout});
	rl.on('SIGINT', () => process.emit('SIGINT'));      // Ctrl+C at a prompt still stops the agent
	process.stdout.write('\x07');                         // the console's bell: a trade is waiting
	try {
		let a = await rl.question(`\n${question}\n  approve? [y/N] (${Math.round(waitS / 60)} min) `,
			{signal: AbortSignal.timeout(waitS * 1000)});
		return a.trim().toLowerCase() === 'y';
	} catch (err) {
		if (err.name !== 'AbortError') throw err;
		process.stdout.write('\n  no answer: skipped\n');
		return null;                                       // null = timed out (the user is away)
	} finally {
		rl.close();
	}
}

module.exports = {approve};

// Terminal prompts. `hidden` echoes nothing (for the password).
const readline = require('readline');

function ask(question, {hidden = false} = {}) {
	return new Promise(resolve => {
		let rl = readline.createInterface({input: process.stdin, output: process.stdout, terminal: true});
		if (hidden) {
			rl._writeToOutput = s => { if (s.includes(question)) rl.output.write(s); };
		}
		rl.question(question, answer => {
			if (hidden) process.stdout.write('\n');
			rl.close();
			resolve(hidden ? answer : answer.trim());   // a password may start or end with a space
		});
	});
}

module.exports = {ask};

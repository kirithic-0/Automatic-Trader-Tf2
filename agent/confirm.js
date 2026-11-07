// List the bot account's pending mobile confirmations (trade offers, market
// listings, the API key) and accept or cancel each one by hand. Logs in with the saved
// login (lib/session.js): the password only when there is none yet.
//   node confirm.js [account]
const SteamTotp = require('steam-totp');
const {ask} = require('./lib/prompt');
const {timeOffset} = require('./lib/login');
const {webLogin} = require('./lib/session');
const secrets = require('./lib/secrets');

function call(fn) {
	return new Promise((resolve, reject) => fn((err, res) => err ? reject(err) : resolve(res)));
}

async function main() {
	let acc = secrets.pick(process.argv[2]);
	let {community} = await webLogin(acc);
	let offset = await timeOffset();
	let key = tag => {
		let time = Math.floor(Date.now() / 1000) + offset;
		return {time, key: {tag, key: SteamTotp.getConfirmationKey(acc.identity_secret, time, tag)}};
	};

	let {time, key: listKey} = key('list');
	let confs = await call(cb => community.getConfirmations(time, listKey, cb));
	if (!confs.length) { console.log('No pending confirmations.'); process.exit(0); }

	for (let c of confs) {
		console.log(`\n${c.title}`);
		if (c.sending) console.log(`  you give:    ${c.sending}`);
		if (c.receiving) console.log(`  you receive: ${c.receiving}`);
		console.log(`  created ${c.time}${c.offerID ? `, trade offer ${c.offerID}` : ''}`);
		let a = (await ask('  accept (a) / cancel (c) / skip (enter)? ')).toLowerCase();
		if (a !== 'a' && a !== 'c') continue;
		let {time: t, key: k} = key(a === 'a' ? 'accept' : 'reject');
		await call(cb => c.respond(t, k, a === 'a', cb));
		console.log(a === 'a' ? '  accepted' : '  cancelled');
	}
	process.exit(0);
}

main().catch(err => { console.error(`Stopped: ${err.message}`); process.exit(1); });

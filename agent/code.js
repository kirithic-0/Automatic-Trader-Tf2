// The Steam Guard login code for the bot account, from its shared_secret: what the
// phone app would show.  node code.js [account]
const SteamTotp = require('steam-totp');
const {timeOffset} = require('./lib/login');
const secrets = require('./lib/secrets');

async function main() {
	let acc = secrets.pick(process.argv[2]);
	let offset = await timeOffset();
	let left = 30 - (Math.floor(Date.now() / 1000) + offset) % 30;
	console.log(`${acc.account_name}: ${SteamTotp.generateAuthCode(acc.shared_secret, offset)}  (${left} s left)`);
}

main().catch(err => { console.error(err.message); process.exit(1); });

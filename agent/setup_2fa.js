// Turn on the Steam Guard Mobile Authenticator for the bot account, on this PC
// (no phone): the same AddAuthenticator / FinalizeAddAuthenticator calls the
// official app makes, through node-steamcommunity.
//
//   node setup_2fa.js <account name>
//
// The secrets are written to %USERPROFILE%\.steam-bot BEFORE the activation step,
// so a failure after Steam hands them out never loses them. Run it in your own
// terminal: it asks for the password, which is never stored.
const fs = require('fs');
const {ask} = require('./lib/prompt');
const {mobileLogin} = require('./lib/login');
const secrets = require('./lib/secrets');

const EResult = {Fail: 2, DuplicateRequest: 29, RateLimitExceeded: 84};

function enable(community) {
	return new Promise((resolve, reject) => {
		community.enableTwoFactor((err, res) => err ? reject(err) : resolve(res));
	});
}

function finalize(community, sharedSecret, code) {
	return new Promise((resolve, reject) => {
		community.finalizeTwoFactor(sharedSecret, code, err => err ? reject(err) : resolve());
	});
}

async function main() {
	let account = process.argv[2] || await ask('Steam account name (the login name): ');
	if (!account) throw new Error('no account name');

	// An earlier run that stopped before activation: finish it, or set it aside.
	let pending = secrets.load(account);
	if (pending && pending.finalized_at) {
		throw new Error(`${secrets.fileFor(account)} is already a finished authenticator`);
	}
	if (pending) {
		let a = await ask(`An unfinished setup from ${pending.enabled_at} exists. Finish it (f) or start over (s)? `);
		if (a.toLowerCase() === 's') {
			let aside = secrets.fileFor(account).replace(/\.json$/, `.abandoned-${Date.now()}.json`);
			fs.renameSync(secrets.fileFor(account), aside);
			console.log(`Moved it to ${aside}.`);
			pending = null;
		}
	}

	let community = await mobileLogin(account);
	console.log(`Logged in as ${community.steamID.getSteamID64()}.`);

	let data = pending;
	if (!data) {
		let res;
		try {
			res = await enable(community);
		} catch (err) {
			if (err.eresult === EResult.Fail) throw new Error('Steam refused (Fail): it may want a phone number on the account');
			if (err.eresult === EResult.DuplicateRequest) throw new Error('Steam says an authenticator already exists on this account');
			if (err.eresult === EResult.RateLimitExceeded) throw new Error('rate limited: wait an hour and retry');
			throw err;
		}
		data = {
			...res,
			account_name: account,
			steamid: community.steamID.getSteamID64(),
			enabled_at: new Date().toISOString(),
		};
		let f = secrets.save(account, data, {fresh: true});
		console.log(`\nSecrets saved to ${f}`);
		console.log('\n================================================================');
		console.log(`  REVOCATION CODE:  ${data.revocation_code}`);
		console.log('  Write it on paper now. With it you can remove the authenticator');
		console.log('  if this file is lost; without both, only Steam Support can help.');
		console.log('================================================================\n');
		for (;;) {
			let typed = await ask('Type the revocation code back to confirm you wrote it down: ');
			if (typed.toUpperCase() === data.revocation_code.toUpperCase()) break;
			console.log('That does not match. Check what you wrote.');
		}
	}

	if (data.phone_number_hint) console.log(`Steam texted an activation code to the phone ending ${data.phone_number_hint}.`);
	else console.log('Steam emailed an activation code (check the account\'s email, spam too).');

	for (;;) {
		let code = await ask('Activation code: ');
		try {
			await finalize(community, data.shared_secret, code);
			break;
		} catch (err) {
			if (err.message === 'Invalid activation code') { console.log('Invalid code, try again.'); continue; }
			throw err;
		}
	}

	data.finalized_at = new Date().toISOString();
	secrets.save(account, data);
	console.log(`\nAuthenticator ON (${data.finalized_at}). The 7-day trade clock starts now.`);
	console.log('Next:');
	console.log(`  1. Back up ${secrets.fileFor(account)} (USB / password manager).`);
	console.log('  2. node code.js      -> the login code Steam now asks for at every login.');
	console.log('  3. node confirm.js   -> accept confirmations (trades, the API key).');
	process.exit(0);
}

main().catch(err => { console.error(`\nStopped: ${err.message}`); process.exit(1); });

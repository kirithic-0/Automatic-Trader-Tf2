// Log a SteamCommunity session in as the mobile app (the platform the authenticator
// and confirmations calls need). The password is typed, never stored.
const SteamCommunity = require('steamcommunity');
const SteamTotp = require('steam-totp');
const {ask} = require('./prompt');

// Seconds this PC's clock is off Steam's; codes are time-based, so they use Steam's time.
function timeOffset() {
	return new Promise((resolve, reject) => {
		SteamTotp.getTimeOffset((err, offset) => err ? reject(err) : resolve(offset));
	});
}

function login(community, details) {
	return new Promise((resolve, reject) => {
		community.login({...details, disableMobile: false}, err => err ? reject(err) : resolve());
	});
}

// sharedSecret given: the 2FA code is generated. Not given: Steam may email a code.
async function mobileLogin(accountName, {sharedSecret} = {}) {
	let community = new SteamCommunity();
	let password = await ask(`Steam password for ${accountName}: `, {hidden: true});
	let details = {accountName, password};
	if (sharedSecret) details.twoFactorCode = SteamTotp.generateAuthCode(sharedSecret, await timeOffset());

	for (;;) {
		try {
			await login(community, details);
			return community;
		} catch (err) {
			if (err.message === 'SteamGuard') {
				details.authCode = await ask(`Steam emailed a login code to ...@${err.emaildomain}. Code: `);
				continue;
			}
			if (err.message === 'SteamGuardMobile' && !sharedSecret) {
				throw new Error('this account already has a mobile authenticator');
			}
			throw err;
		}
	}
}

module.exports = {mobileLogin, timeOffset};

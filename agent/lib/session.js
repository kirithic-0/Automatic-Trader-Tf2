// The bot account's Steam login, kept as a refresh token (user, 2026-10-26: the password
// typed once, not at every start). The token lives beside the authenticator file, in
// %USERPROFILE%\.steam-bot\<account>.token.json -- never the repo, never a log. Anyone who
// copies it can trade from the account until it is revoked (Steam: "Deauthorize all
// devices"); the authenticator file next to it already allows about as much.
//
//   let {community, cookies, steamid} = await webLogin(acc)   // acc = secrets.pick()
//
// With a saved token: no password, no code; Steam renews the token now and then, and the
// new one replaces the old (the old stops working). No token, or Steam refuses it: the
// password is asked once, the Steam Guard code comes from the shared_secret, and the new
// token is saved. MobileApp is the platform the confirmations calls need.
const fs = require('fs');
const path = require('path');
const SteamCommunity = require('steamcommunity');
const SteamTotp = require('steam-totp');
const {LoginSession, EAuthTokenPlatformType} = require('steam-session');
const {ask} = require('./prompt');
const {timeOffset} = require('./login');
const secrets = require('./secrets');

const tokenFile = account => path.join(secrets.DIR, `${account.toLowerCase()}.token.json`);

function loadToken(account) {
	let f = tokenFile(account);
	if (!fs.existsSync(f)) return null;
	try { return JSON.parse(fs.readFileSync(f, 'utf8')).refresh_token || null; } catch (err) { return null; }
}

function saveToken(account, token) {
	fs.mkdirSync(secrets.DIR, {recursive: true});
	fs.writeFileSync(tokenFile(account), JSON.stringify({refresh_token: token, saved_at: new Date().toISOString()}, null, '\t'),
		{mode: 0o600});
}

function forgetToken(account) {
	let f = tokenFile(account);
	if (fs.existsSync(f)) fs.unlinkSync(f);
}

// A new token from the password (asked here) and the authenticator's code.
async function passwordLogin(acc, log) {
	let session = new LoginSession(EAuthTokenPlatformType.MobileApp);
	let password = await ask(`Steam password for ${acc.account_name} (asked once; the login is then saved): `, {hidden: true});
	let code = SteamTotp.generateAuthCode(acc.shared_secret, await timeOffset());
	let done = new Promise((resolve, reject) => {
		session.once('authenticated', resolve);
		session.once('error', reject);
		session.once('timeout', () => reject(new Error('Steam login timed out')));
	});
	let r = await session.startWithCredentials({accountName: acc.account_name, password, steamGuardCode: code});
	if (r.actionRequired) {
		session.cancelLoginAttempt();
		throw new Error(`Steam wants more to log in (${r.validActions.map(a => a.type).join(', ')}): the authenticator code was not accepted`);
	}
	await done;
	saveToken(acc.account_name, session.refreshToken);
	log(`login: password accepted, the login is saved in ${tokenFile(acc.account_name)}`);
	return session;
}

// A session from the saved token, renewed when Steam offers a new one; null if refused.
async function tokenLogin(acc, log) {
	let token = loadToken(acc.account_name);
	if (!token) return null;
	let session = new LoginSession(EAuthTokenPlatformType.MobileApp);
	try {
		session.refreshToken = token;
		if (await session.renewRefreshToken()) {
			saveToken(acc.account_name, session.refreshToken);
			log('login: Steam renewed the saved login');
		}
		return session;
	} catch (err) {
		log(`login: the saved login was refused (${err.message}) -- asking for the password`);
		forgetToken(acc.account_name);
		return null;
	}
}

// Logged-in web cookies for the account, and a SteamCommunity on them.
async function webLogin(acc, {log = console.log} = {}) {
	let session = (await tokenLogin(acc, log)) || (await passwordLogin(acc, log));
	let cookies = await session.getWebCookies();
	let community = new SteamCommunity();
	community.setCookies(cookies);
	let steamid = community.steamID.getSteamID64();
	if (acc.steamid && steamid !== acc.steamid) throw new Error(`logged in as ${steamid}, not ${acc.steamid}`);
	return {community, cookies, steamid, session};
}

module.exports = {webLogin, loadToken, saveToken, forgetToken, tokenFile};

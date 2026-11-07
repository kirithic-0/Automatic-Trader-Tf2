// Where the bot account's Steam secrets live: outside the repo, on this PC only.
// One file per account: <account>.maFile.json, the whole AddAuthenticator response
// plus account_name, steamid, enabled_at and (once Steam accepts it) finalized_at.
const fs = require('fs');
const os = require('os');
const path = require('path');

const DIR = process.env.STEAM_BOT_DIR || path.join(os.homedir(), '.steam-bot');

function fileFor(account) {
	return path.join(DIR, `${account.toLowerCase()}.maFile.json`);
}

function load(account) {
	let f = fileFor(account);
	return fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, 'utf8')) : null;
}

// Never overwrite a secrets file by accident: `fresh` refuses if one exists.
function save(account, data, {fresh = false} = {}) {
	fs.mkdirSync(DIR, {recursive: true});
	let f = fileFor(account);
	fs.writeFileSync(f, JSON.stringify(data, null, '\t'), {mode: 0o600, flag: fresh ? 'wx' : 'w'});
	return f;
}

// The accounts with a finished (finalized) authenticator.
function accounts() {
	if (!fs.existsSync(DIR)) return [];
	return fs.readdirSync(DIR)
		.filter(n => n.endsWith('.maFile.json'))
		.map(n => JSON.parse(fs.readFileSync(path.join(DIR, n), 'utf8')))
		.filter(d => d.finalized_at);
}

// The one account to act on: the name given, else the only finished one.
function pick(name) {
	let all = accounts();
	if (name) {
		let d = all.find(a => a.account_name.toLowerCase() === name.toLowerCase());
		if (!d) throw new Error(`no finished authenticator for "${name}" in ${DIR}`);
		return d;
	}
	if (all.length === 1) return all[0];
	if (!all.length) throw new Error(`no finished authenticator in ${DIR}: run setup_2fa.js first`);
	throw new Error(`several accounts in ${DIR}: name one (${all.map(a => a.account_name).join(', ')})`);
}

module.exports = {DIR, fileFor, load, save, accounts, pick};

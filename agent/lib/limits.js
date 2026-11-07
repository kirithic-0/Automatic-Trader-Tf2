// Stage 6 -- the limits every new buy must pass, the kill switch, and the agent's timings.
//
// agent/limits.json (the live agent's; user, 2026-10-26; AGENT_LIMITS points elsewhere, for tests).
// The running agent re-reads it at the start of every cycle (reloaded(), 2026-10-30): a change
// applies from the next cycle, no restart; a file that will not read keeps the last good one.
//   max_open_positions     held positions (opening / open / closing) at once
//   max_keys_in_play       keys the held positions cost, plus this buy (first run: 2)
//   max_trade_keys         one buy's price (the rule already keeps it under 1 key)
//   max_new_buys_per_pass  buys a pass may start (the model's own k is 10)
//   win_window, min_win_rate  no new buys once fewer than min_win_rate of the last
//                          win_window sales won (20 and 0.75). It stays stopped (the file
//                          BUYS_PAUSED) until `node stop.js resume`, which starts a new window
//                          (only sales after it count)
//   max_tries, bot_wait_s, poll_ms   bots tried per trade (3), seconds each one gets once the
//                          offer is delivered (30), how often its state is read (ms)
//   approve_wait_s         how long an approval waits for the user (900 = 15 min)
//   target_rec, target_scrap  optional: the reclaimed / scrap pieces every trade's metal
//                          tends to (15 and 10, user 2026-10-30; offers.metalTarget); without
//                          them the old rule (buys pay refined, rounded up) stands
//   key_band_low, key_band_high, max_conversions, max_metal_keys  optional: the key<->metal
//                          rebalance (lib/convert.js; 0.5 / 1.5 keys, 3 a cycle, 3 keys,
//                          user 2026-10-30): metal kept between need - low and need + high
//                          keys, need = what buys may still spend, at most max_metal_keys;
//                          without the bands nothing is converted
// Sells are never blocked by a limit -- getting out is always allowed -- only by the kill
// switch.
//
// Kill switch: a file named STOP in the secrets folder (%USERPROFILE%\.steam-bot\STOP) or
// in agent/. While it exists nothing is sent, buy or sell. `node stop.js` / `node stop.js off`.
const fs = require('fs');
const path = require('path');
const secrets = require('./secrets');

const CONFIG = process.env.AGENT_LIMITS || path.join(__dirname, '..', 'limits.json');
const STOP_FILES = [path.join(secrets.DIR, 'STOP'), path.join(__dirname, '..', 'STOP')];
const RESUMED = path.join(secrets.DIR, 'RESUMED');      // when the win-rate window last restarted
const PAUSED = path.join(secrets.DIR, 'BUYS_PAUSED');   // the win-rate stop tripped: no buys until resumed
const KEYS = ['max_open_positions', 'max_keys_in_play', 'max_trade_keys',
	'max_new_buys_per_pass', 'win_window', 'min_win_rate', 'max_tries', 'bot_wait_s', 'poll_ms', 'approve_wait_s'];
const OPTIONAL = ['target_rec', 'target_scrap', 'key_band_low', 'key_band_high', 'max_conversions', 'max_metal_keys'];
const WHOLE = ['target_rec', 'target_scrap', 'max_conversions'];
const ALL = [...KEYS, ...OPTIONAL];

function load(file = CONFIG) {
	let cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
	for (let k of KEYS) {
		if (!(typeof cfg[k] === 'number' && cfg[k] >= 0)) throw new Error(`limits.json: ${k} must be a number >= 0`);
	}
	for (let k of OPTIONAL) {
		if (!(k in cfg)) continue;
		if (!(typeof cfg[k] === 'number' && cfg[k] >= 0)) throw new Error(`limits.json: ${k} must be a number >= 0`);
		if (WHOLE.includes(k) && !Number.isInteger(cfg[k])) throw new Error(`limits.json: ${k} must be a whole number`);
	}
	return cfg;
}

// The limits for the next cycle: limits.json as it is now, or `cfg` when it will not read.
// Logs what changed.
function reloaded(cfg, log, file = CONFIG) {
	let next;
	try { next = load(file); } catch (err) {
		log(`limits.json not read (${err.message}) -- keeping the last good limits`);
		return cfg;
	}
	let moved = ALL.filter(k => next[k] !== cfg[k]).map(k => `${k} ${cfg[k]} -> ${next[k]}`);
	if (moved.length) log(`limits changed: ${moved.join(', ')}`);
	return next;
}

// Set one limit in limits.json (the panel, `agent set`): the file is checked whole before it
// replaces the old one, and its other keys (the _note) are kept.
function set(key, value, file = CONFIG) {
	if (!ALL.includes(key)) throw new Error(`unknown limit ${key}: ${ALL.join(', ')}`);
	let v = Number(value);
	if (!Number.isFinite(v) || v < 0) throw new Error(`${key} must be a number >= 0, not ${value}`);
	if (key === 'min_win_rate' && v > 1) throw new Error('min_win_rate is a share: 0 to 1');
	let raw = JSON.parse(fs.readFileSync(file, 'utf8'));
	let old = raw[key];
	raw[key] = v;
	let tmp = `${file}.${process.pid}.tmp`;
	fs.writeFileSync(tmp, JSON.stringify(raw, null, '\t') + '\n');
	try { load(tmp); fs.renameSync(tmp, file); } catch (err) { try { fs.unlinkSync(tmp); } catch (e) { /* gone */ } throw err; }
	return {key, old, value: v};
}

function stopped(files = STOP_FILES) {
	let f = files.find(p => fs.existsSync(p));
	return f ? `kill switch on (${f})` : null;
}

// Turn the kill switch on with a reason (the agent does this itself on a trade hold).
function stop(reason, files = STOP_FILES) {
	fs.mkdirSync(path.dirname(files[0]), {recursive: true});
	fs.writeFileSync(files[0], `stopped ${new Date().toISOString()}: ${reason}\n`);
}

function resumedAt(file = RESUMED) {
	try { return fs.readFileSync(file, 'utf8').trim(); } catch (err) { return ''; }
}

function resume(file = RESUMED, at = new Date(), pausedFile = PAUSED) {
	fs.mkdirSync(path.dirname(file), {recursive: true});
	fs.writeFileSync(file, at.toISOString());
	if (fs.existsSync(pausedFile)) fs.unlinkSync(pausedFile);
}

const pausedFor = file => path.join(path.dirname(file), 'BUYS_PAUSED');

// The rolling win-rate stop: {ok} or {ok: false, reason}. Once tripped it stays tripped
// (BUYS_PAUSED beside the RESUMED file) until resume().
function winRate({ledger, mode, cfg, resumedFile = RESUMED}) {
	let paused = pausedFor(resumedFile);
	if (fs.existsSync(paused)) return {ok: false, reason: fs.readFileSync(paused, 'utf8').trim()};
	if (!cfg.win_window) return {ok: true};
	let sales = ledger.recentSales(mode, cfg.win_window, resumedAt(resumedFile));
	if (sales.length < cfg.win_window) return {ok: true, n: sales.length};
	let won = sales.filter(s => s.pnl_keys > 0).length / sales.length;
	if (won < cfg.min_win_rate) {
		let reason = `only ${(won * 100).toFixed(0)}% of the last ${sales.length} sales won ` +
			`(< ${(cfg.min_win_rate * 100).toFixed(0)}%): buying stopped ${new Date().toISOString()} -- look, then \`node stop.js resume\``;
		fs.mkdirSync(path.dirname(paused), {recursive: true});
		fs.writeFileSync(paused, reason + '\n');
		return {ok: false, reason};
	}
	return {ok: true, n: sales.length, won};
}

// May this buy (price in keys) start now? `boughtThisPass` = buys already started this pass.
function allowBuy({ledger, mode, priceKeys, cfg, boughtThisPass = 0, stopFiles, resumedFile}) {
	let stop = stopped(stopFiles);
	if (stop) return {ok: false, reason: stop};
	if (boughtThisPass >= cfg.max_new_buys_per_pass) return {ok: false, reason: `max ${cfg.max_new_buys_per_pass} new buys a pass`};
	if (priceKeys > cfg.max_trade_keys) return {ok: false, reason: `price ${priceKeys} > max_trade_keys ${cfg.max_trade_keys}`};
	let ex = ledger.exposure(mode);
	if (ex.open >= cfg.max_open_positions) return {ok: false, reason: `${ex.open} open >= max_open_positions ${cfg.max_open_positions}`};
	if (ex.keys + priceKeys > cfg.max_keys_in_play + 1e-9) {
		return {ok: false, reason: `${(ex.keys + priceKeys).toFixed(4)} keys in play > max_keys_in_play ${cfg.max_keys_in_play}`};
	}
	let w = winRate({ledger, mode, cfg, resumedFile});
	if (!w.ok) return w;
	return {ok: true};
}

function allowSell({stopFiles} = {}) {
	let stop = stopped(stopFiles);
	return stop ? {ok: false, reason: stop} : {ok: true};
}

module.exports = {CONFIG, STOP_FILES, RESUMED, PAUSED, KEYS, OPTIONAL, load, reloaded, set, stopped, stop, resume, resumedAt, winRate, allowBuy, allowSell};

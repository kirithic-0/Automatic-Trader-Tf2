// Stage 6, step 3.6 -- the position ledger and the offer log (gap 5), in one SQLite file
// beside the collection: backpack/agent.db (gitignored with backpack/*.db). Node writes it
// (Steam's offer events arrive here); Python reads it (`engine picks --ledger`: what is held).
//
//   offers        one row per offer the agent built: what, to whom, at what price, the book
//                 it was priced against (watch_id), its Steam id once sent, its current state.
//                 A position can have several: a bot that does not answer in 30 s is
//                 canceled and the next one tried (lib/send.js)
//   offer_events  every state an offer went through, with the time: sent -> accepted is the
//                 fill time the backtest never had
//   positions     one row per item bought: entry, the model's numbers at entry, the
//                 take-profit and horizon it exits by, then the exit and its profit
//   position_events  every change made by hand to a held position (the panel, `agent
//                 position`): sell at the next cycle, a new take-profit, a new horizon --
//                 the exit rule (`engine exits`) reads take_profit_at / horizon_at every
//                 cycle, so these are the whole override (2026-10-30)
//   conversions   one row per key<->metal offer of the rebalance (lib/convert.js, 2026-10-30):
//                 not a position -- no take-profit, no P&L, never in the win rate or the keys
//                 in play -- but the same offer states, and the restart check reads them too
//   conversion_events  every state a conversion offer went through
//
// mode 'live' ('paper' rows are the deleted paper agent's, docs/extra/paper-agent.md). Every
// change goes through the functions below, which only allow the moves in OFFER_MOVES /
// POSITION_MOVES.
const path = require('path');
const {DatabaseSync} = require('node:sqlite');

const DEFAULT_PATH = process.env.AGENT_DB || path.join(__dirname, '..', '..', 'backpack', 'agent.db');

// built: made, not sent. sent: on Steam, waiting for our mobile confirmation. active:
// confirmed, delivered to the partner (the 30 s clock runs from here). The rest are Steam's
// end states. would_send was the paper agent's stand-in for sent (old rows only).
const OFFER_MOVES = {
	built: ['sent', 'failed', 'canceled'],
	sent: ['active', 'accepted', 'declined', 'expired', 'canceled', 'failed', 'escrow'],
	active: ['accepted', 'declined', 'expired', 'canceled', 'failed', 'escrow'],
	escrow: ['accepted', 'canceled', 'failed'],
	would_send: ['accepted', 'declined', 'expired', 'canceled'],
	accepted: [], declined: [], expired: [], canceled: [], failed: [],
};
const OPEN_OFFER = ['built', 'sent', 'active', 'escrow', 'would_send'];

// opening: the buy offer is out. open: we hold it. closing: a sell offer is out.
const POSITION_MOVES = {
	opening: ['open', 'abandoned'],
	open: ['closing'],
	closing: ['open', 'closed'],          // a declined sell puts it back to open
	closed: [], abandoned: [],
};
const HELD = ['opening', 'open', 'closing'];

const SCHEMA = `
CREATE TABLE IF NOT EXISTS positions (
	id INTEGER PRIMARY KEY,
	mode TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
	item TEXT NOT NULL, quality INTEGER NOT NULL, variant INTEGER NOT NULL DEFAULT 0, label TEXT,
	state TEXT NOT NULL,
	opened_at TEXT NOT NULL,          -- the buy offer was built
	pass_id INTEGER, exp_ret REAL, p_up REAL, ask1 REAL,   -- the model at entry
	entry_keys REAL,                  -- what the buy offer pays (total keys)
	take_profit_at REAL,              -- sell the first time a tradable bid is >= this
	horizon_at TEXT,                  -- else sell to the best bid after this time
	asset_id TEXT,                    -- the item's asset id in OUR inventory once held
	closed_at TEXT, exit_keys REAL, exit_reason TEXT, pnl_keys REAL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS positions_state ON positions (state);
CREATE TABLE IF NOT EXISTS offers (
	id INTEGER PRIMARY KEY,
	mode TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
	kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell')),
	position_id INTEGER NOT NULL REFERENCES positions (id),
	state TEXT NOT NULL,
	created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
	partner TEXT NOT NULL, trade_url TEXT, listing_id TEXT,
	price_keys INTEGER, price_ref TEXT, total_keys REAL,
	receive_json TEXT NOT NULL, give_json TEXT NOT NULL,
	watch_id INTEGER,                 -- the VM's watch_snapshots row the price was checked on
	steam_offer_id TEXT,              -- once sent
	reason TEXT                       -- why it ended (Steam's message, a decline, ...)
);
CREATE INDEX IF NOT EXISTS offers_state ON offers (state);
CREATE TABLE IF NOT EXISTS position_events (
	id INTEGER PRIMARY KEY,
	position_id INTEGER NOT NULL REFERENCES positions (id),
	at TEXT NOT NULL,
	what TEXT NOT NULL,               -- sell_now | take_profit | horizon
	old_value TEXT, new_value TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS offer_events (
	id INTEGER PRIMARY KEY,
	offer_id INTEGER NOT NULL REFERENCES offers (id),
	at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS conversions (
	id INTEGER PRIMARY KEY,
	kind TEXT NOT NULL CHECK (kind IN ('buy_key', 'sell_key')),
	state TEXT NOT NULL,
	created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
	partner TEXT NOT NULL, trade_url TEXT, listing_id TEXT,
	price_ref TEXT NOT NULL,          -- one key's price in ref
	receive_json TEXT NOT NULL, give_json TEXT NOT NULL,
	watch_id INTEGER, steam_offer_id TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS conversion_events (
	id INTEGER PRIMARY KEY,
	conversion_id INTEGER NOT NULL REFERENCES conversions (id),
	at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT
);
`;

const now = () => new Date().toISOString();

class Ledger {
	constructor(file = DEFAULT_PATH) {
		this.db = new DatabaseSync(file);
		this.db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;');
		this.db.exec(SCHEMA);
	}

	close() { this.db.close(); }

	_tx(fn) {
		this.db.exec('BEGIN IMMEDIATE');
		try {
			let out = fn();
			this.db.exec('COMMIT');
			return out;
		} catch (err) {
			this.db.exec('ROLLBACK');
			throw err;
		}
	}

	_event(offerId, state, detail, at) {
		this.db.prepare('INSERT INTO offer_events (offer_id, at, state, detail) VALUES (?, ?, ?, ?)')
			.run(offerId, at, state, detail == null ? null : String(detail));
	}

	_movePosition(id, to, at, extra = {}) {
		let p = this.position(id);
		if (!p) throw new Error(`no position ${id}`);
		if (!POSITION_MOVES[p.state].includes(to)) throw new Error(`position ${id}: ${p.state} -> ${to} not allowed`);
		let cols = Object.keys(extra);
		this.db.prepare(`UPDATE positions SET state = ?, updated_at = ?${cols.map(c => `, ${c} = ?`).join('')} WHERE id = ?`)
			.run(to, at, ...cols.map(c => extra[c]), id);
	}

	// A buy: opens the position (state opening) and logs its offer (state built).
	// `pick` = one row of `engine picks --json`; `offer` = offers.buyOffer(...).offer.
	openBuy({mode, pick, offer, watchId = null, takeProfit = 0.04, horizonH = 48}) {
		return this._tx(() => {
			if (this.isHeld(pick.item, pick.quality, pick.variant ?? 0)) {
				throw new Error(`already held: ${pick.item} q${pick.quality}`);
			}
			let at = now();
			let entry = offer.price.total_keys;
			let pos = this.db.prepare(`INSERT INTO positions (mode, item, quality, variant, label, state, opened_at,
				pass_id, exp_ret, p_up, ask1, entry_keys, take_profit_at, horizon_at, updated_at)
				VALUES (?, ?, ?, ?, ?, 'opening', ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
				mode, pick.item, pick.quality, pick.variant ?? 0, pick.label ?? null, at,
				pick.pass_id ?? null, pick.exp_ret ?? null, pick.p_up ?? null, pick.ask1 ?? null,
				entry, entry * (1 + takeProfit), new Date(Date.parse(at) + horizonH * 3600e3).toISOString(), at);
			let offerId = this._insertOffer(mode, 'buy', Number(pos.lastInsertRowid), offer, watchId, at);
			return {positionId: Number(pos.lastInsertRowid), offerId};
		});
	}

	// Another offer for a position that is still opening (a buy) or closing (a sell): the
	// next bot after one that did not answer.
	addOffer({positionId, offer, watchId = null}) {
		return this._tx(() => {
			let p = this.position(positionId);
			if (!p) throw new Error(`no position ${positionId}`);
			if (!['opening', 'closing'].includes(p.state)) throw new Error(`position ${positionId} is ${p.state}: no new offer`);
			if (this.openOffers().some(o => o.position_id === positionId)) throw new Error(`position ${positionId} has an open offer`);
			return {offerId: this._insertOffer(p.mode, p.state === 'opening' ? 'buy' : 'sell', positionId, offer, watchId, now())};
		});
	}

	// The position's last offer ended and no other bot is tried: a buy is abandoned, a sale
	// goes back to open.
	giveUp(positionId, reason) {
		return this._tx(() => {
			let p = this.position(positionId);
			let at = now();
			if (p.state === 'opening') this._movePosition(positionId, 'abandoned', at, {closed_at: at, exit_reason: reason});
			else if (p.state === 'closing') this._movePosition(positionId, 'open', at);
			else throw new Error(`position ${positionId} is ${p.state}: nothing to give up`);
		});
	}

	// A sell for an open position: the position goes to closing, the offer is logged.
	openSell({positionId, offer, watchId = null}) {
		return this._tx(() => {
			let at = now();
			let p = this.position(positionId);
			if (!p) throw new Error(`no position ${positionId}`);
			this._movePosition(positionId, 'closing', at);
			return {offerId: this._insertOffer(p.mode, 'sell', positionId, offer, watchId, at)};
		});
	}

	_insertOffer(mode, kind, positionId, offer, watchId, at) {
		let r = this.db.prepare(`INSERT INTO offers (mode, kind, position_id, state, created_at, updated_at, partner,
			trade_url, listing_id, price_keys, price_ref, total_keys, receive_json, give_json, watch_id)
			VALUES (?, ?, ?, 'built', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
			mode, kind, positionId, at, at, offer.partner, offer.trade_url ?? null, offer.listing_id ?? null,
			offer.price.keys, String(offer.price.ref), offer.price.total_keys,
			JSON.stringify(offer.receive), JSON.stringify(offer.give), watchId);
		let id = Number(r.lastInsertRowid);
		this._event(id, 'built', null, at);
		return id;
	}

	// Move an offer to its next state; its position follows:
	//   buy accepted -> open (extra.assetId = the item's new asset id in our inventory); the
	//     entry, take-profit and horizon are set from this offer's price and the fill time
	//   buy declined / expired / canceled / failed -> abandoned
	//   sell accepted -> closed (exit_keys = the offer's price, pnl against entry)
	//   sell declined / expired / canceled / failed -> back to open
	// `final: false` on a failed end leaves the position where it is (another bot is tried
	// next; giveUp() ends it).
	setOffer(offerId, to, {detail = null, steamOfferId = null, assetId = null, exitReason = null, final = true,
		takeProfit = 0.04, horizonH = 48} = {}) {
		return this._tx(() => {
			let at = now();
			let o = this.offer(offerId);
			if (!o) throw new Error(`no offer ${offerId}`);
			if (!OFFER_MOVES[o.state].includes(to)) throw new Error(`offer ${offerId}: ${o.state} -> ${to} not allowed`);
			this.db.prepare(`UPDATE offers SET state = ?, updated_at = ?, steam_offer_id = COALESCE(?, steam_offer_id),
				reason = COALESCE(?, reason) WHERE id = ?`).run(to, at, steamOfferId, OPEN_OFFER.includes(to) ? null : detail, offerId);
			this._event(offerId, to, detail, at);

			if (OPEN_OFFER.includes(to)) return;
			let p = this.position(o.position_id);
			if (to !== 'accepted' && !final) return;
			if (o.kind === 'buy') {
				if (to === 'accepted') {
					this._movePosition(p.id, 'open', at, {asset_id: assetId, entry_keys: o.total_keys,
						take_profit_at: o.total_keys * (1 + takeProfit),
						horizon_at: new Date(Date.parse(at) + horizonH * 3600e3).toISOString()});
				} else this._movePosition(p.id, 'abandoned', at, {closed_at: at, exit_reason: `buy ${to}`});
			} else if (to === 'accepted') {
				this._movePosition(p.id, 'closed', at, {closed_at: at, exit_keys: o.total_keys,
					exit_reason: exitReason, pnl_keys: o.total_keys - p.entry_keys});
			} else {
				this._movePosition(p.id, 'open', at);
			}
		});
	}

	// By hand, on a held (open) position: a sale already out is left alone. The next cycle's
	// exit rule acts on it. sellNow = the horizon moved to now: it sells into the best
	// tradable bid, whatever it is, and the sale is recorded as exit 'manual'.
	sellNow(positionId, detail = null) {
		return this._adjust(positionId, 'sell_now', 'horizon_at', now(), detail);
	}

	// A take-profit `pct` over the price paid (0.04 = +4%, the model's own).
	setTakeProfit(positionId, pct, detail = null) {
		if (!(Number.isFinite(pct) && pct > 0 && pct <= 1)) throw new Error(`take-profit ${pct}: a share over 0 and up to 1 (100%)`);
		let p = this.position(positionId);
		if (!p) throw new Error(`no position ${positionId}`);
		return this._adjust(positionId, 'take_profit', 'take_profit_at', p.entry_keys * (1 + pct), detail);
	}

	// The horizon `hours` from now (0-336), or back to the model's own (bought + 48 h) with null.
	setHorizon(positionId, hours, detail = null) {
		let p = this.position(positionId);
		if (!p) throw new Error(`no position ${positionId}`);
		let at;
		if (hours == null) at = new Date(Date.parse(p.opened_at) + 48 * 3600e3).toISOString();
		else if (Number.isFinite(hours) && hours >= 0 && hours <= 336) at = new Date(Date.now() + hours * 3600e3).toISOString();
		else throw new Error(`horizon ${hours} h: 0 to 336 hours from now`);
		return this._adjust(positionId, 'horizon', 'horizon_at', at, detail);
	}

	_adjust(positionId, what, col, value, detail) {
		return this._tx(() => {
			let p = this.position(positionId);
			if (!p) throw new Error(`no position ${positionId}`);
			if (p.state !== 'open') throw new Error(`position ${positionId} (${p.item}) is ${p.state}: only a held item can be changed`);
			let at = now();
			this.db.prepare(`UPDATE positions SET ${col} = ?, updated_at = ? WHERE id = ?`).run(value, at, positionId);
			this.db.prepare(`INSERT INTO position_events (position_id, at, what, old_value, new_value, detail)
				VALUES (?, ?, ?, ?, ?, ?)`).run(positionId, at, what, String(p[col]), String(value), detail);
			return this.position(positionId);
		});
	}

	positionEvents(positionId) {
		return this.db.prepare('SELECT * FROM position_events WHERE position_id = ? ORDER BY id').all(positionId);
	}

	// Is this position's sale the user's sell-now? Its last horizon change was a sell_now.
	soldByHand(positionId) {
		let e = this.db.prepare(`SELECT what FROM position_events WHERE position_id = ? AND what IN ('sell_now', 'horizon')
			ORDER BY id DESC LIMIT 1`).get(positionId);
		return !!e && e.what === 'sell_now';
	}

	offer(id) { return this.db.prepare('SELECT * FROM offers WHERE id = ?').get(id); }
	position(id) { return this.db.prepare('SELECT * FROM positions WHERE id = ?').get(id); }

	// A key<->metal offer of the rebalance (kind buy_key | sell_key), state built.
	addConversion({kind, offer, watchId = null}) {
		let at = now();
		let r = this.db.prepare(`INSERT INTO conversions (kind, state, created_at, updated_at, partner, trade_url, listing_id,
			price_ref, receive_json, give_json, watch_id) VALUES (?, 'built', ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
			kind, at, at, offer.partner, offer.trade_url ?? null, offer.listing_id ?? null, String(offer.price.ref),
			JSON.stringify(offer.receive), JSON.stringify(offer.give), watchId);
		let id = Number(r.lastInsertRowid);
		this.db.prepare('INSERT INTO conversion_events (conversion_id, at, state) VALUES (?, ?, ?)').run(id, at, 'built');
		return id;
	}

	// Its next state, by the offers' own moves (OFFER_MOVES). Same options as setOffer; the
	// position ones are ignored (a conversion has none).
	setConversion(id, to, {detail = null, steamOfferId = null} = {}) {
		return this._tx(() => {
			let at = now(), c = this.conversion(id);
			if (!c) throw new Error(`no conversion ${id}`);
			if (!OFFER_MOVES[c.state].includes(to)) throw new Error(`conversion ${id}: ${c.state} -> ${to} not allowed`);
			this.db.prepare(`UPDATE conversions SET state = ?, updated_at = ?, steam_offer_id = COALESCE(?, steam_offer_id),
				reason = COALESCE(?, reason) WHERE id = ?`).run(to, at, steamOfferId, OPEN_OFFER.includes(to) ? null : detail, id);
			this.db.prepare('INSERT INTO conversion_events (conversion_id, at, state, detail) VALUES (?, ?, ?, ?)')
				.run(id, at, to, detail == null ? null : String(detail));
		});
	}

	conversion(id) { return this.db.prepare('SELECT * FROM conversions WHERE id = ?').get(id); }

	openConversions() {
		return this.db.prepare(`SELECT * FROM conversions WHERE state IN (${OPEN_OFFER.map(() => '?').join(',')}) ORDER BY id`).all(...OPEN_OFFER);
	}

	// The ledger as lib/send.js's attempt() sees it, for one conversion: its offer moves are
	// the conversion's.
	conversionView() {
		return {setOffer: (id, to, opts) => this.setConversion(id, to, opts)};
	}
	events(offerId) { return this.db.prepare('SELECT * FROM offer_events WHERE offer_id = ? ORDER BY id').all(offerId); }

	held() {
		return this.db.prepare(`SELECT * FROM positions WHERE state IN (${HELD.map(() => '?').join(',')}) ORDER BY id`).all(...HELD);
	}

	isHeld(item, quality, variant = 0) {
		return !!this.db.prepare(`SELECT 1 FROM positions WHERE item = ? AND quality = ? AND variant = ?
			AND state IN (${HELD.map(() => '?').join(',')})`).get(item, quality, variant, ...HELD);
	}

	openOffers() {
		return this.db.prepare(`SELECT * FROM offers WHERE state IN (${OPEN_OFFER.map(() => '?').join(',')}) ORDER BY id`).all(...OPEN_OFFER);
	}

	// The newest pass `mode` bought on: a restarted agent does not buy that pass again.
	lastPass(mode) {
		return this.db.prepare('SELECT max(pass_id) AS p FROM positions WHERE mode = ?').get(mode).p ?? null;
	}

	// What `mode` has in play: held positions and the keys they cost (entry_keys).
	exposure(mode) {
		let r = this.db.prepare(`SELECT count(*) AS n, COALESCE(sum(entry_keys), 0) AS keys FROM positions
			WHERE mode = ? AND state IN (${HELD.map(() => '?').join(',')})`).get(mode, ...HELD);
		return {open: r.n, keys: r.keys};
	}

	// The last `n` sales of `mode` closed at or after `sinceIso` (newest first): the rolling
	// win-rate stop reads them (lib/limits.js).
	recentSales(mode, n, sinceIso = '') {
		return this.db.prepare(`SELECT id, item, pnl_keys, closed_at FROM positions WHERE mode = ? AND state = 'closed'
			AND closed_at >= ? ORDER BY closed_at DESC LIMIT ?`).all(mode, sinceIso, n);
	}

	// Asset ids promised in open offers and conversions -- and given in ones accepted in the
	// last `recentMin` minutes, which a lagging inventory read may still list (2026-10-30: a
	// buy right after a key sale must not pay with the key just sold): currency.stock(items,
	// reserved) leaves them out.
	reservedAssets(recentMin = 30) {
		let ids = new Set(), since = new Date(Date.now() - recentMin * 60e3).toISOString();
		let rows = [...this.openOffers(), ...this.openConversions(),
			...this.db.prepare(`SELECT give_json FROM offers WHERE state = 'accepted' AND updated_at >= ?
				UNION ALL SELECT give_json FROM conversions WHERE state = 'accepted' AND updated_at >= ?`).all(since, since)];
		for (let o of rows) for (let a of JSON.parse(o.give_json)) ids.add(String(a.assetid));
		return ids;
	}
}

module.exports = {Ledger, DEFAULT_PATH, OFFER_MOVES, POSITION_MOVES, HELD, OPEN_OFFER};

// node --test   (from agent/)
const test = require('node:test');
const assert = require('node:assert/strict');
const {toUnits, refString, stock, pay, settle, settleToward, SCRAP} = require('../lib/currency');
const {buyOffer, sellOffer, marketNames, assetCheck, metalTarget} = require('../lib/offers');

const inv = (counts) => {
	let names = {key: 'Mann Co. Supply Crate Key', ref: 'Refined Metal', rec: 'Reclaimed Metal', scrap: 'Scrap Metal'};
	let items = [], id = 100;
	for (let [k, n] of Object.entries(counts)) {
		for (let i = 0; i < n; i++) items.push({assetid: String(id++), market_hash_name: names[k], tradable: true});
	}
	return items;
};

test('toUnits: keys + ref to whole keys + scrap', () => {
	assert.deepEqual(toUnits({keys: 1, ref: 41.77}), {keys: 1, scrap: 376});   // 41 ref + 7 scrap
	assert.deepEqual(toUnits({keys: 0, ref: 0.11}), {keys: 0, scrap: 1});
	assert.deepEqual(toUnits({keys: 0, ref: 5.44}), {keys: 0, scrap: 49});
	assert.deepEqual(toUnits({keys: 2, ref: 0}), {keys: 2, scrap: 0});
	assert.throws(() => toUnits({keys: 1.5, ref: 0}), /whole keys/);
	assert.throws(() => toUnits({keys: 0, ref: 0.15}), /whole scrap/);
});

test('refString round-trips every scrap count', () => {
	for (let s = 0; s < 200; s++) assert.equal(toUnits({keys: 0, ref: Number(refString(s))}).scrap, s);
	assert.equal(refString(376), '41.77');
});

test('stock: only tradable currency, minus reserved', () => {
	let items = [...inv({key: 2, ref: 1}), {assetid: '9', market_hash_name: 'Refined Metal', tradable: false},
		{assetid: '8', market_hash_name: 'Team Captain', tradable: true}];
	let s = stock(items, new Set(['100']));
	assert.deepEqual(s.key, ['101']);
	assert.deepEqual(s.ref, ['102']);
});

test('pay: exact change whenever any exists (vs brute force)', () => {
	for (let r = 0; r <= 3; r++) for (let c = 0; c <= 4; c++) for (let s = 0; s <= 4; s++) {
		let have = stock(inv({ref: r, rec: c, scrap: s}));
		for (let need = 0; need <= 40; need++) {
			let exists = false;
			for (let a = 0; a <= r; a++) for (let b = 0; b <= c; b++) for (let d = 0; d <= s; d++) {
				if (a * 9 + b * 3 + d === need) exists = true;
			}
			let got = pay({keys: 0, scrap: need}, have);
			assert.equal(got.ok, exists, `ref ${r} rec ${c} scrap ${s} need ${need}`);
			if (got.ok) {
				let v = got.counts.ref * SCRAP.ref + got.counts.rec * SCRAP.rec + got.counts.scrap;
				assert.equal(v, need);
			}
		}
	}
});

test('pay: keys first, short keys refused', () => {
	assert.match(pay({keys: 2, scrap: 0}, stock(inv({key: 1}))).reason, /short 1 key/);
	let got = pay({keys: 1, scrap: 13}, stock(inv({key: 1, ref: 2, rec: 1, scrap: 1})));
	assert.deepEqual(got.counts, {key: 1, ref: 1, rec: 1, scrap: 1});
});

const L = {side: 'sell', reason: null, asset_id: '17656752805', steam_id: 'S0000000000000001',
	trade_url: 'https://steamcommunity.com/tradeoffer/new/?partner=1000001&token=AbC-12_x',
	listing_id: '440_17656752805', keys: 1, ref: 41.77, total_keys: 1.6487, user: 'Pizza Bot'};

test('buyOffer: request their asset, give exact currency', () => {
	let res = buyOffer(L, stock(inv({key: 1, ref: 41, scrap: 7})));
	assert.ok(res.ok, res.reason);
	assert.deepEqual(res.offer.receive, [{appid: 440, contextid: '2', assetid: '17656752805'}]);
	assert.equal(res.offer.give.length, 1 + 41 + 7);
	assert.equal(res.offer.change_scrap, 0);
	assert.equal(res.offer.price.ref, '41.77');
	assert.equal(res.offer.partner, L.steam_id);
});

test('buyOffer: refuses what it cannot do exactly', () => {
	assert.match(buyOffer(L, stock(inv({key: 1, ref: 42}))).reason, /no exact change/);
	assert.match(buyOffer({...L, reason: 'outside the fence'}, stock(inv({key: 9}))).reason, /not tradable/);
	assert.match(buyOffer({...L, side: 'buy'}, stock(inv({key: 9}))).reason, /not a sell/);
	assert.match(buyOffer({...L, asset_id: null}, stock(inv({key: 9}))).reason, /asset id/);
	assert.match(buyOffer({...L, trade_url: 'https://evil.example/x'}, stock(inv({key: 9}))).reason, /unexpected trade URL/);
});

test('settle: change from the other side when the payer has no small pieces', () => {
	// 5 scrap owed, payer has only refined: pays 1 ref (9), gets 4 scrap back (1 rec + 1 scrap)
	let got = settle({keys: 0, scrap: 5}, stock(inv({ref: 3})), stock(inv({rec: 2, scrap: 3})));
	assert.ok(got.ok, got.reason);
	assert.equal(got.change, 4);
	assert.equal(got.payer.length, 1);
	assert.equal(got.payee.length, 2);
	// smallest change first: with scrap on hand, exact wins
	assert.equal(settle({keys: 0, scrap: 5}, stock(inv({ref: 1, scrap: 5})), stock(inv({scrap: 9}))).change, 0);
	// nobody has the pieces
	assert.equal(settle({keys: 0, scrap: 5}, stock(inv({ref: 1})), stock(inv({ref: 5}))).ok, false);
	// keys never take change
	assert.match(settle({keys: 1, scrap: 0}, stock(inv({ref: 99})), stock(inv({key: 5}))).reason, /short 1 key/);
});

test('settle preferChange (our buys): change from the payee first, our small pieces last', () => {
	let theirs = stock(inv({rec: 3, scrap: 3}).map(x => ({...x, assetid: 't' + x.assetid})));
	let mine = stock(inv({ref: 3, rec: 3, scrap: 3}));
	let got = settle({keys: 0, scrap: 5}, mine, theirs, 18, {preferChange: true});
	assert.deepEqual([got.counts, got.change], [{key: 0, ref: 1, rec: 0, scrap: 0}, 4], '1 refined, 4 scrap back');
	assert.equal(settle({keys: 0, scrap: 5}, mine, theirs).change, 0, 'without it: our exact pieces');
	let noScrap = stock(inv({rec: 3}).map(x => ({...x, assetid: 't' + x.assetid})));
	got = settle({keys: 0, scrap: 5}, mine, noScrap, 18, {preferChange: true});
	assert.deepEqual([got.counts, got.change], [{key: 0, ref: 0, rec: 1, scrap: 2}, 0],
		'they can return no change (reclaimed only, 4 or 1 scrap owed back): our exact pieces');
	got = settle({keys: 0, scrap: 6}, stock(inv({ref: 3, rec: 3})), noScrap, 18, {preferChange: true});
	assert.deepEqual([got.counts, got.change], [{key: 0, ref: 1, rec: 0, scrap: 0}, 3], '6 scrap: 1 refined, 1 reclaimed back');
});

test('settle: the net is always the price (random stocks)', () => {
	let seed = 7, rnd = n => (seed = (seed * 16807) % 2147483647) % n;
	for (let i = 0; i < 400; i++) {
		let need = rnd(60);
		let a = inv({ref: rnd(4), rec: rnd(3), scrap: rnd(3)}), b = inv({ref: rnd(3), rec: rnd(4), scrap: rnd(5)});
		b.forEach(x => { x.assetid = 'b' + x.assetid; });
		let got = settle({keys: 0, scrap: need}, stock(a), stock(b));
		if (!got.ok) continue;
		let val = ids => ids.reduce((v, id) => v + SCRAP[{
			'Refined Metal': 'ref', 'Reclaimed Metal': 'rec', 'Scrap Metal': 'scrap'}[[...a, ...b].find(x => x.assetid === id).market_hash_name]], 0);
		assert.equal(val(got.payer) - val(got.payee), need);
	}
});

test('buyOffer: their change comes back in the same offer', () => {
	let res = buyOffer(L, stock(inv({key: 1, ref: 42})), stock(inv({rec: 1, scrap: 2}).map(x => ({...x, assetid: 't' + x.assetid}))));
	assert.ok(res.ok, res.reason);
	assert.equal(res.offer.change_scrap, 2);                 // 41.88 = 41 ref + 8 scrap; paid 42 ref
	assert.equal(res.offer.give.length, 1 + 42);
	assert.deepEqual(res.offer.receive.map(a => a.assetid), ['17656752805', 't101', 't102']);
});

const BID = {side: 'buy', reason: null, asset_id: null, steam_id: 'S0000000000000002',
	trade_url: 'https://steamcommunity.com/tradeoffer/new/?partner=1&token=x',
	listing_id: '440_S0000000000000002_abc', keys: 1, ref: 38.11, total_keys: 1.5903, user: 'bot'};

test('sellOffer: give our item, take their exact keys + metal', () => {
	let theirs = stock(inv({key: 3, ref: 50, rec: 2, scrap: 2}).map(x => ({...x, assetid: 't' + x.assetid})));
	let res = sellOffer({asset_id: 'our-1'}, BID, theirs);
	assert.ok(res.ok, res.reason);
	assert.deepEqual(res.offer.give, [{appid: 440, contextid: '2', assetid: 'our-1'}]);
	assert.equal(res.offer.receive.length, 1 + 38 + 1);       // 38.11 = 38 ref + 1 scrap
	assert.equal(res.offer.kind, 'sell');
});

test('sellOffer: refusals', () => {
	let theirs = stock(inv({key: 3, ref: 50, scrap: 2}));
	assert.match(sellOffer({asset_id: null}, BID, theirs).reason, /no asset id/);
	assert.match(sellOffer({asset_id: 'x'}, {...BID, side: 'sell'}, theirs).reason, /not a buy/);
	assert.match(sellOffer({asset_id: 'x'}, BID, stock(inv({ref: 50}))).reason, /buyer can't pay/);
	// the buyer has no scrap: we give a scrap back from ours
	let res = sellOffer({asset_id: 'x'}, {...BID, ref: 38.22}, stock(inv({key: 1, ref: 39})),
		stock(inv({scrap: 9}).map(x => ({...x, assetid: 'o' + x.assetid}))));
	assert.ok(res.ok, res.reason);
	assert.equal(res.offer.change_scrap, 7);                  // 38.22 = 38 ref + 2 scrap; they pay 39 ref, we return 7 scrap
	assert.equal(res.offer.give.length, 1 + 7);
});

test("marketNames: Steam's names for an engine item (measured on our inventory, 2026-10-28)", () => {
	assert.deepEqual(marketNames('Bottle Cap', 6), ['Bottle Cap', 'The Bottle Cap']);
	assert.deepEqual(marketNames('Hot Dogger', 11), ['Strange Hot Dogger']);
	assert.deepEqual(marketNames('Hot Dogger', 1), [], 'Genuine: no rule, no names');
});

test('assetCheck: the listed asset, tradable, by its exact TF2 name', () => {
	let l = {asset_id: '55'};
	let inv = name => [{assetid: '9', market_hash_name: 'Refined Metal', tradable: true},
		{assetid: '55', market_hash_name: name, tradable: true}];
	assert.equal(assetCheck(l, inv('The Bottle Cap'), 'Bottle Cap', 6), null);
	assert.equal(assetCheck(l, inv('Bottle Cap'), 'Bottle Cap', 6), null);
	assert.equal(assetCheck(l, inv('Strange Hot Dogger'), 'Hot Dogger', 11), null);
	assert.match(assetCheck(l, inv('Strange Bottle Cap'), 'Bottle Cap', 6), /is "Strange Bottle Cap", not "Bottle Cap" \/ "The Bottle Cap"/);
	assert.match(assetCheck(l, inv('Hot Dogger'), 'Hot Dogger', 11), /not "Strange Hot Dogger"/);
	assert.match(assetCheck(l, inv('Killstreak Hot Dogger'), 'Hot Dogger', 6), /is "Killstreak Hot Dogger"/);
	assert.match(assetCheck(l, inv('Genuine X'), 'X', 1), /not a quality-1 item/);
	assert.match(assetCheck({asset_id: '56'}, inv('The Bottle Cap'), 'Bottle Cap', 6), /not in their TF2 inventory/);
	assert.match(assetCheck(l, [{assetid: '55', market_hash_name: 'The Bottle Cap', tradable: false}], 'Bottle Cap', 6), /not tradable/);
	assert.match(assetCheck(l, null, 'Bottle Cap', 6), /could not be read/);
});

const T = {rec: 15, scrap: 10};
const theirs = counts => stock(inv(counts).map(x => ({...x, assetid: 't' + x.assetid})));
const VAL = {'Refined Metal': 9, 'Reclaimed Metal': 3, 'Scrap Metal': 1};

test('settleToward: a buy at the target keeps our small pieces, change makes up the rest', () => {
	// at 15 rec / 10 scrap, 5 scrap owed: 1 refined out, 1 rec + 1 scrap back (+1 / +1), never 5 of our scrap
	let got = settleToward({keys: 0, scrap: 5}, stock(inv({ref: 5, rec: 15, scrap: 10})), theirs({rec: 5, scrap: 5}), T, 'payer');
	assert.ok(got.ok, got.reason);
	assert.deepEqual([got.counts, got.change_counts], [{key: 0, ref: 1, rec: 0, scrap: 0}, {key: 0, ref: 0, rec: 1, scrap: 1}]);
});

test('settleToward: short on scrap, a buy asks for scrap back; heavy on scrap, it spends it', () => {
	let got = settleToward({keys: 0, scrap: 9}, stock(inv({ref: 5, rec: 15, scrap: 2})), theirs({ref: 2, rec: 5, scrap: 9}), T, 'payer');
	assert.equal(got.counts.scrap, 0, 'none of our 2 scrap goes out');
	assert.ok(got.change_counts.scrap > 0, 'scrap comes back');
	got = settleToward({keys: 0, scrap: 9}, stock(inv({ref: 5, rec: 15, scrap: 20})), theirs({rec: 5, scrap: 9}), T, 'payer');
	assert.equal(got.counts.scrap, 9, '9 of our extra scrap pays it');
});

test('settleToward: a sale takes the pieces we are short of, gives the ones we have too many of', () => {
	let buyer = theirs({key: 1, ref: 20, rec: 20, scrap: 20});
	let got = settleToward({keys: 0, scrap: 12}, buyer, stock(inv({rec: 10, scrap: 10})), T, 'payee');
	assert.deepEqual(got.counts, {key: 0, ref: 0, rec: 4, scrap: 0}, 'short 5 rec: take 4 (12 scrap)');
	got = settleToward({keys: 0, scrap: 9}, buyer, stock(inv({rec: 25, scrap: 10})), T, 'payee');
	assert.ok(got.change_counts && got.change_counts.rec > 0, 'too many rec: some go back as change');
});

test('settleToward: the net is the price, the change capped, and trades walk to the target', () => {
	let seed = 11, rnd = n => (seed = (seed * 16807) % 2147483647) % n;
	let us = {ref: 60, rec: 2, scrap: 30};
	for (let i = 0; i < 300; i++) {
		let need = rnd(200), buy = rnd(2) === 0;
		let mine = inv(us), other = inv({ref: 30, rec: rnd(20), scrap: rnd(20)});
		other.forEach(x => { x.assetid = 'b' + x.assetid; });
		let [payer, payee] = buy ? [mine, other] : [other, mine];
		let got = settleToward({keys: 0, scrap: need}, stock(payer), stock(payee), T, buy ? 'payer' : 'payee');
		if (!got.ok) continue;
		let all = [...mine, ...other], val = ids => ids.reduce((v, id) => v + VAL[all.find(x => x.assetid === id).market_hash_name], 0);
		assert.equal(val(got.payer) - val(got.payee), need);
		assert.ok(val(got.payee) <= 18);
		let sgn = buy ? -1 : 1, c = got.counts, b = got.change_counts || {ref: 0, rec: 0, scrap: 0};
		for (let k of ['ref', 'rec', 'scrap']) us[k] += sgn * (c[k] - b[k]);
		if (us.ref < 10) us.ref += 30;
		if (us.ref > 100) us.ref -= 30;
	}
	assert.ok(Math.abs(us.rec - 15) <= 3 && Math.abs(us.scrap - 10) <= 3, `ended at ${us.rec} rec / ${us.scrap} scrap`);
});

test('buyOffer / sellOffer use the target when limits.json sets one', () => {
	assert.equal(metalTarget({}), null);
	assert.deepEqual(metalTarget({target_rec: 15, target_scrap: 10}), T);
	let res = buyOffer(L, stock(inv({key: 1, ref: 45, rec: 15, scrap: 10})), theirs({rec: 5, scrap: 5}), T);
	assert.ok(res.ok, res.reason);
	assert.equal(res.offer.give.length, 1 + 42, '1 key + 41.77: 42 refined out, 2 scrap back, our reclaimed untouched');
	assert.equal(res.offer.change_scrap, 2);
});

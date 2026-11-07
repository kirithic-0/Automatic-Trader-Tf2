// Build -- never send -- the buy offer for an item's cheapest tradable sell listing,
// paid from the bot account's real inventory.
//
//   cd backpack && python -m engine listings "Mr. Juice" --json ../agent/listing.json
//   node dry_buy.js listing.json [account]
//
// Prints the offer it would send, or why it can't (no tradable seller, no exact change).
const fs = require('fs');
const secrets = require('./lib/secrets');
const {tf2Inventory} = require('./lib/inventory');
const {stock} = require('./lib/currency');
const {buyOffer} = require('./lib/offers');

async function main() {
	let [file, account] = process.argv.slice(2);
	if (!file) throw new Error('usage: node dry_buy.js <listings.json from engine listings --json> [account]');
	let data = JSON.parse(fs.readFileSync(file, 'utf8'));
	let sells = data.listings.filter(l => l.side === 'sell' && l.reason === null)
		.sort((a, b) => a.total_keys - b.total_keys);
	console.log(`${data.item} q${data.quality}: ${sells.length} tradable sell listing(s), book from ${data.source} at ${data.at}`);
	if (!sells.length) return;
	let listing = sells[0];
	console.log(`cheapest: ${listing.total_keys} keys (${listing.keys} keys + ${listing.ref} ref) from ${listing.user} (${listing.steam_id})`);

	let acc = secrets.pick(account);
	let have = stock(await tf2Inventory(acc.steamid));
	console.log(`our tradable currency: ${have.key.length} keys, ${have.ref.length} ref, ${have.rec.length} rec, ${have.scrap.length} scrap`);

	let res = buyOffer(listing, have);
	if (!res.ok) { console.log(`no offer: ${res.reason}`); return; }
	console.log('\nWOULD SEND (dry run, nothing sent):');
	console.log(JSON.stringify(res.offer, null, 2));
}

main().catch(err => { console.error(`Stopped: ${err.message}`); process.exitCode = 1; });   // exitCode, not exit(): exit() mid-fetch trips a libuv assert on Windows

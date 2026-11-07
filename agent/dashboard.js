// The live agent as a page: profit over time, every sale's return, what is held against its
// take-profit and horizon, and every trade. Built from the ledger + log (lib/trades.js) into
// one self-contained HTML file (no network), then opened in the browser.
//   node dashboard.js [--no-open]   (agent dashboard)   -> backpack/logs/dashboard.html
// Read-only; run it again for fresh numbers.
const fs = require('fs');
const path = require('path');
const {spawn} = require('child_process');
const {Ledger} = require('./lib/ledger');
const {BACKPACK} = require('./lib/python');
const {trades, logTail} = require('./lib/trades');
const limits = require('./lib/limits');
const secrets = require('./lib/secrets');
const {alive} = require('./lib/lock');
const {BACKTEST} = require('./lib/report');

const OUT = path.join(BACKPACK, 'logs', 'dashboard.html');

function state() {
	let cfg = limits.load();
	let acc = secrets.pick();
	let lockFile = path.join(secrets.DIR, `${acc.account_name}.lock`);
	let pid = fs.existsSync(lockFile) ? Number(fs.readFileSync(lockFile, 'utf8')) : null;
	let stop = limits.stopped();
	return {running: !!(pid && alive(pid)), kill: stop ? fs.readFileSync(stop.match(/\((.*)\)/)[1], 'utf8').trim() : null,
		paused: fs.existsSync(limits.PAUSED) ? fs.readFileSync(limits.PAUSED, 'utf8').trim() : null,
		max_keys: cfg.max_keys_in_play, win_window: cfg.win_window, min_win: cfg.min_win_rate};
}

let ledger = new Ledger();
let rows = trades(ledger, logTail(path.join(BACKPACK, 'logs', 'agent.log')));
ledger.close();
let data = {generated: new Date().toISOString(), state: state(), backtest: BACKTEST, rows};
const esc = s => JSON.stringify(s).replace(/</g, '\\u003c');

let html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TF2 agent</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
 --border:rgba(11,11,11,.10);--up:#2a78d6;--down:#e34948;--up-text:#1c5cab;--down-text:#b8302f;--track:#cde2fb;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
 --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--up:#3987e5;--down:#e66767;--up-text:#86b6ef;--down-text:#f09a9a;--track:#184f95}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;
 --border:rgba(255,255,255,.10);--up:#3987e5;--down:#e66767;--up-text:#86b6ef;--down-text:#f09a9a;--track:#184f95}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0}h2{font-size:15px;margin:0 0 2px}
.sub{color:var(--ink2);font-size:13px}.muted{color:var(--muted)}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between;margin-bottom:16px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:999px;padding:2px 10px;font-size:12px;background:var(--surface)}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:16px}
.tile .label{color:var(--ink2);font-size:13px}.tile .value{font-size:28px;font-weight:600;margin-top:2px}
.tile.hero .value{font-size:48px;line-height:1.1}
.tile .delta{font-size:13px;color:var(--ink2);margin-top:2px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media (max-width:820px){.row2{grid-template-columns:1fr}}
.row2 .card{margin-bottom:0}.row2{margin-bottom:16px}
svg{display:block;width:100%;overflow:visible}
svg text{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:14px;font-size:12px;color:var(--ink2);margin:6px 0 4px}
.key{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--ink2);font-weight:500;border-bottom:1px solid var(--axis);padding:6px 8px;white-space:nowrap}
td{border-bottom:1px solid var(--grid);padding:6px 8px;white-space:nowrap;font-variant-numeric:tabular-nums}
td.num,th.num{text-align:right}
tr.gone td{color:var(--muted)}
.pos{color:var(--up-text)}.neg{color:var(--down-text)}
.state{font-size:12px;border:1px solid var(--border);border-radius:6px;padding:1px 6px}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);border-radius:8px;
 padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;z-index:9;max-width:280px}
#tip b{font-weight:600}
.held-name{font-weight:500}
</style></head>
<body><main>
<header><div><h1>TF2 agent</h1><div class="sub" id="gen"></div></div><div class="chips" id="chips"></div></header>
<div class="tiles" id="tiles"></div>
<div class="row2">
 <section class="card"><h2>Realised profit over time</h2><div class="sub">keys, summed over every sale</div><div id="cum"></div></section>
 <section class="card"><h2>Return per sale</h2><div class="sub">% of the price paid, oldest sale first</div>
  <div class="legend"><span><span class="key" style="background:var(--up)"></span>profit</span><span><span class="key" style="background:var(--down)"></span>loss</span></div>
  <div id="ret"></div></section>
</div>
<section class="card"><h2>Holding now</h2><div class="sub">latest best bid (from the agent's last cycle) between what we paid and the +4% take-profit; the bar under it is time used of the 48 h horizon</div>
 <div class="scroll"><table id="held"></table></div></section>
<section class="card"><h2>Every trade</h2><div class="sub">newest first; times UTC</div><div class="scroll"><table id="all"></table></div></section>
</main><div id="tip"></div>
<script>
const D = ${esc(data)};
const $ = id => document.getElementById(id);
const k4 = x => x == null ? '–' : x.toFixed(4);
const pct = x => x == null ? '–' : (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + '%';
const sgn = x => (x >= 0 ? '+' : '') + x.toFixed(4);
const when = s => s ? s.slice(5, 16).replace('T', ' ') : '–';
const h = (s, ...v) => s.reduce((a, p, i) => a + p + (i < v.length ? String(v[i]).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]) : ''), '');
const R = D.rows, held = R.filter(r => ['opening','open','closing'].includes(r.state));
const sold = R.filter(r => r.state === 'closed').sort((a, b) => a.sold_at.localeCompare(b.sold_at));
const gone = R.filter(r => r.state === 'abandoned');
const tip = $('tip');
function showTip(e, html) { tip.innerHTML = html; tip.style.display = 'block';
	let x = e.clientX + 14, y = e.clientY + 14, w = tip.offsetWidth, hh = tip.offsetHeight;
	if (x + w > innerWidth - 8) x = e.clientX - w - 14; if (y + hh > innerHeight - 8) y = e.clientY - hh - 14;
	tip.style.left = x + 'px'; tip.style.top = y + 'px'; }
const hideTip = () => { tip.style.display = 'none'; };

// header
$('gen').textContent = 'built ' + D.generated.slice(0, 16).replace('T', ' ') + ' UTC · run agent dashboard again to refresh';
let chips = [];
chips.push(D.state.running ? ['var(--good)', 'running'] : ['var(--muted)', 'not running (no live lock)']);
chips.push(D.state.kill ? ['var(--crit)', 'kill switch ON'] : ['var(--good)', 'kill switch off']);
if (D.state.paused) chips.push(['var(--warn)', 'buys paused (win-rate stop)']);
$('chips').innerHTML = chips.map(([c, t]) => h\`<span class="chip"><span class="dot" style="background:\${c}"></span>\${t}</span>\`).join('');

// tiles
const pnl = sold.reduce((s, r) => s + r.pnl_keys, 0), won = sold.filter(r => r.pnl_keys > 0).length;
const avg = sold.length ? sold.reduce((s, r) => s + r.ret, 0) / sold.length : null;
const inPlay = held.reduce((s, r) => s + r.entry_keys, 0);
const hours = sold.length ? sold.reduce((s, r) => s + r.held_h, 0) / sold.length : null;
const last20 = sold.slice(-D.state.win_window), won20 = last20.filter(r => r.pnl_keys > 0).length;
$('tiles').innerHTML = [
	['hero', 'Realised profit (keys)', sgn(pnl), sold.length + ' sales · ' + pct(avg) + ' a sale on average (backtest ' + pct(D.backtest.per_trade) + ')'],
	['', 'Win rate', sold.length ? Math.round(won / sold.length * 100) + '%' : '–', won + ' of ' + sold.length + ' sales made a profit (backtest ' + Math.round(D.backtest.win * 100) + '%). Buying stops under ' + Math.round(D.state.min_win * 100) + '% of the last ' + D.state.win_window + (last20.length < D.state.win_window ? ' (only ' + last20.length + ' so far)' : ' (now ' + Math.round(won20 / last20.length * 100) + '%)')],
	['', 'Keys in play', inPlay.toFixed(4), held.length + ' items held · limit ' + D.state.max_keys + ' keys'],
	['', 'Average hold', hours == null ? '–' : hours.toFixed(1) + ' h', 'buy fill to sale · ' + gone.length + ' buys never filled'],
].map(([c, l, v, d]) => h\`<div class="card tile \${c}" style="margin:0"><div class="label">\${l}</div><div class="value">\${v}</div><div class="delta">\${d}</div></div>\`).join('');

const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs, parent) => { let n = document.createElementNS(NS, tag); for (let a in attrs) n.setAttribute(a, attrs[a]); if (parent) parent.appendChild(n); return n; };
function ticks(lo, hi, n) { let span = hi - lo || 1, step = Math.pow(10, Math.floor(Math.log10(span / n))), e = span / n / step;
	step *= e >= 5 ? 10 : e >= 2 ? 5 : e >= 1 ? 2 : 1; let out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(+v.toFixed(10)); return out; }

// cumulative profit: a step line, a dot per sale
function cum() {
	let box = $('cum'); box.innerHTML = '';
	if (!sold.length) { box.innerHTML = '<p class="muted">No sales yet.</p>'; return; }
	let W = box.clientWidth, H = 220, m = {l: 52, r: 12, t: 12, b: 26};
	let t0 = Date.parse(R[0].opened_at), t1 = Date.now();
	let pts = [], c = 0; for (let r of sold) { c += r.pnl_keys; pts.push({t: Date.parse(r.sold_at), v: c, r}); }
	let lo = Math.min(0, ...pts.map(p => p.v)), hi = Math.max(0, ...pts.map(p => p.v)) * 1.1 || 0.01;
	let x = t => m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r), y = v => H - m.b - (v - lo) / (hi - lo) * (H - m.t - m.b);
	let s = el('svg', {viewBox: \`0 0 \${W} \${H}\`, height: H, role: 'img', 'aria-label': 'Realised profit over time'}, box);
	for (let v of ticks(lo, hi, 4)) { el('line', {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? 'var(--axis)' : 'var(--grid)'}, s);
		el('text', {x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end'}, s).textContent = v.toFixed(2); }
	for (let day = Math.ceil(t0 / 864e5) * 864e5; day <= t1; day += 864e5) {   // UTC midnights
		el('line', {x1: x(day), x2: x(day), y1: H - m.b, y2: H - m.b + 4, stroke: 'var(--axis)'}, s);
		el('text', {x: x(day), y: H - 8, 'text-anchor': 'middle'}, s).textContent = new Date(day).toISOString().slice(5, 10); }
	let dPath = \`M\${x(t0)},\${y(0)}\`; let prev = 0; for (let p of pts) { dPath += \`H\${x(p.t)}V\${y(p.v)}\`; prev = p.v; } dPath += \`H\${x(t1)}\`;
	el('path', {d: dPath + \`V\${y(0)}H\${x(t0)}Z\`, fill: 'var(--up)', 'fill-opacity': .1, stroke: 'none'}, s);
	el('path', {d: dPath, fill: 'none', stroke: 'var(--up)', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, s);
	let last = pts[pts.length - 1];
	el('text', {x: W - m.r, y: y(last.v) - 8, 'text-anchor': 'end', style: 'fill:var(--ink);font-weight:600'}, s).textContent = sgn(last.v);
	for (let p of pts) { let g = el('g', {}, s);
		el('circle', {cx: x(p.t), cy: y(p.v), r: 12, fill: 'transparent'}, g);
		el('circle', {cx: x(p.t), cy: y(p.v), r: 4, fill: p.r.pnl_keys >= 0 ? 'var(--up)' : 'var(--down)', stroke: 'var(--surface)', 'stroke-width': 2}, g);
		g.addEventListener('mousemove', e => showTip(e, h\`<b>\${p.r.label}</b><br>sold \${when(p.r.sold_at)} · \${p.r.exit_reason}<br>this sale \${sgn(p.r.pnl_keys)} keys (\${pct(p.r.ret)})<br>total \${sgn(p.v)} keys\`));
		g.addEventListener('mouseleave', hideTip); }
}

// return per sale: diverging columns from a zero baseline
function ret() {
	let box = $('ret'); box.innerHTML = '';
	if (!sold.length) { box.innerHTML = '<p class="muted">No sales yet.</p>'; return; }
	let W = box.clientWidth, H = 200, m = {l: 44, r: 8, t: 16, b: 16};
	let vals = sold.map(r => r.ret * 100), lo = Math.min(0, ...vals) * 1.15, hi = Math.max(0, ...vals) * 1.15 || 1;
	let y = v => m.t + (hi - v) / (hi - lo) * (H - m.t - m.b);
	let band = (W - m.l - m.r) / sold.length, bw = Math.min(24, band * 0.7);
	let s = el('svg', {viewBox: \`0 0 \${W} \${H}\`, height: H, role: 'img', 'aria-label': 'Return per sale'}, box);
	for (let v of ticks(lo, hi, 4)) { el('line', {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? 'var(--axis)' : 'var(--grid)'}, s);
		el('text', {x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end'}, s).textContent = v + '%'; }
	let best = vals.indexOf(Math.max(...vals)), worst = vals.indexOf(Math.min(...vals));
	sold.forEach((r, i) => {
		let v = vals[i], cx = m.l + band * (i + .5), x0 = cx - bw / 2, y0 = y(0), y1 = y(v), up = v >= 0, rr = Math.min(4, Math.abs(y1 - y0));
		let d = up ? \`M\${x0},\${y0}V\${y1 + rr}Q\${x0},\${y1} \${x0 + rr},\${y1}H\${x0 + bw - rr}Q\${x0 + bw},\${y1} \${x0 + bw},\${y1 + rr}V\${y0}Z\`
			: \`M\${x0},\${y0}V\${y1 - rr}Q\${x0},\${y1} \${x0 + rr},\${y1}H\${x0 + bw - rr}Q\${x0 + bw},\${y1} \${x0 + bw},\${y1 - rr}V\${y0}Z\`;
		let g = el('g', {}, s);
		el('rect', {x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b, fill: 'transparent'}, g);
		el('path', {d, fill: up ? 'var(--up)' : 'var(--down)'}, g);
		if (i === best || i === worst) el('text', {x: cx, y: up ? y1 - 5 : y1 + 13, 'text-anchor': 'middle', style: 'fill:var(--ink2)'}, s).textContent = pct(r.ret);
		g.addEventListener('mousemove', e => showTip(e, h\`<b>\${r.label}</b><br>paid \${k4(r.entry_keys)} → sold \${k4(r.exit_keys)}<br>\${sgn(r.pnl_keys)} keys (\${pct(r.ret)}) · \${r.exit_reason}<br>held \${r.held_h.toFixed(1)} h · sold \${when(r.sold_at)}\`));
		g.addEventListener('mouseleave', hideTip);
	});
}

// holding: the bid on a track from min(paid, bid) to the take-profit, and the horizon used
function heldTable() {
	if (!held.length) { $('held').innerHTML = '<tr><td class="muted">Nothing held.</td></tr>'; return; }
	let rows = held.map(r => {
		let tp = r.take_profit_at, lo = Math.min(r.entry_keys, r.bid ?? r.entry_keys) * 0.98, hi = tp * 1.005;
		let px = v => ((v - lo) / (hi - lo) * 100).toFixed(1) + '%';
		let used = Math.min(1, Math.max(0, (Date.now() - Date.parse(r.bought_at)) / (Date.parse(r.horizon_at) - Date.parse(r.bought_at))));
		let left = (Date.parse(r.horizon_at) - Date.now()) / 3600e3;
		let vs = r.bid != null ? r.bid / r.entry_keys - 1 : null;
		let track = \`<div style="position:relative;height:18px;min-width:180px" title="paid \${k4(r.entry_keys)} · bid \${k4(r.bid)} · sell at \${k4(tp)}">
			<div style="position:absolute;top:8px;left:0;right:0;height:2px;background:var(--grid)"></div>
			<div style="position:absolute;top:3px;left:\${px(r.entry_keys)};width:2px;height:12px;background:var(--axis)"></div>
			<div style="position:absolute;top:3px;left:\${px(tp)};width:2px;height:12px;background:var(--ink2)"></div>
			\${r.bid != null ? \`<div style="position:absolute;top:4px;left:calc(\${px(r.bid)} - 5px);width:10px;height:10px;border-radius:50%;background:\${vs >= 0 ? 'var(--up)' : 'var(--down)'};box-shadow:0 0 0 2px var(--surface)"></div>\` : ''}</div>
			<div style="display:flex;justify-content:space-between;font-size:11px" class="muted"><span>paid</span><span>sell at</span></div>\`;
		let meter = \`<div style="height:6px;border-radius:3px;background:var(--track);min-width:120px"><div style="height:6px;border-radius:3px;width:\${(used * 100).toFixed(0)}%;background:var(--up)"></div></div>
			<div class="muted" style="font-size:11px">\${left >= 0 ? left.toFixed(1) + ' h left' : (-left).toFixed(1) + ' h past: selling into the best bid'}</div>\`;
		return h\`<tr><td><div class="held-name">\${r.label}</div><div class="muted" style="font-size:12px">from \${r.seller} · \${when(r.bought_at)}</div></td>
			<td class="num">\${k4(r.entry_keys)}</td><td class="num">\${k4(r.bid)}</td>\` +
			\`<td class="num \${vs == null ? '' : vs >= 0 ? 'pos' : 'neg'}">\${pct(vs)}</td>\` + h\`<td class="num">\${k4(tp)}</td>\` +
			\`<td style="width:30%">\${track}</td><td style="width:18%">\${meter}</td></tr>\`;
	});
	$('held').innerHTML = '<tr><th>item</th><th class="num">paid</th><th class="num">bid now</th><th class="num">vs paid</th><th class="num">sell at</th><th>bid vs target</th><th>48 h horizon</th></tr>' + rows.join('');
}

function allTable() {
	let order = [...R].sort((a, b) => (b.sold_at || b.bought_at || b.opened_at).localeCompare(a.sold_at || a.bought_at || a.opened_at));
	let rows = order.map(r => {
		let cls = r.pnl_keys == null ? '' : r.pnl_keys >= 0 ? 'pos' : 'neg';
		let st = r.state === 'closed' ? 'sold' : r.state === 'abandoned' ? 'never filled' : 'held';
		return \`<tr class="\${r.state === 'abandoned' ? 'gone' : ''}">\` + h\`<td>\${r.label}</td><td><span class="state">\${st}</span></td>
			<td>\${when(r.bought_at || r.opened_at)}</td><td class="num">\${k4(r.entry_keys)}</td><td class="num">\${r.p_up == null ? '–' : r.p_up.toFixed(2)}</td><td>\${r.seller ?? '–'}</td>
			<td>\${when(r.sold_at)}</td><td class="num">\${k4(r.exit_keys)}</td><td>\${r.buyer ?? '–'}</td><td>\${r.exit_reason ?? '–'}</td>\` +
			\`<td class="num \${cls}">\${r.pnl_keys == null ? '–' : sgn(r.pnl_keys)}</td><td class="num \${cls}">\${pct(r.ret)}</td>\` +
			h\`<td class="num">\${r.held_h == null ? '–' : r.held_h.toFixed(1) + ' h'}</td></tr>\`;
	});
	$('all').innerHTML = '<tr><th>item</th><th>state</th><th>bought</th><th class="num">paid</th><th class="num">p_up</th><th>from</th><th>sold</th><th class="num">sold at</th><th>to</th><th>exit</th><th class="num">keys</th><th class="num">%</th><th class="num">held</th></tr>' + rows.join('');
}

function draw() { cum(); ret(); }
heldTable(); allTable(); draw();
let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 100); });
</script></body></html>
`;

fs.mkdirSync(path.dirname(OUT), {recursive: true});
fs.writeFileSync(OUT, html);
console.log(`dashboard: ${OUT}`);
if (!process.argv.includes('--no-open')) spawn('cmd', ['/c', 'start', '', OUT], {detached: true, stdio: 'ignore'}).unref();

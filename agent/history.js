// One item's price history: the top buy order, the lowest sell and the bid the agent can
// sell into, from every stored book (engine history: the hourly passes since 2026-09-17,
// plus the ~10-min watch books of what the agent holds). A summary here, then a chart page.
//   node history.js <item...> [--strange | --quality N] [--days N] [--no-chart | --no-open]
//   agent history socked and loaded
// The name can be loose (any case, a unique part). Without a quality: the agent's own
// position's, else Unique. What the agent paid and its take-profit are drawn when it holds it.
const fs = require('fs');
const path = require('path');
const {spawn} = require('child_process');
const {engine, BACKPACK} = require('./lib/python');
const {Ledger} = require('./lib/ledger');

function args(argv) {
	let a = {words: [], quality: null, days: null, chart: true, open: true};
	for (let i = 0; i < argv.length; i++) {
		let k = argv[i];
		if (k === '--strange') a.quality = 11;
		else if (k === '--unique') a.quality = 6;
		else if (k === '--quality') a.quality = Number(argv[++i]);
		else if (k === '--days') a.days = Number(argv[++i]);
		else if (k === '--no-chart') a.chart = false;
		else if (k === '--no-open') a.open = false;   // write the chart, don't open it
		else a.words.push(k);
	}
	a.item = a.words.join(' ').trim();
	if (!a.item) throw new Error('usage: agent history <item> [--strange] [--days N] [--no-chart]');
	return a;
}

const k4 = x => x == null ? '-' : x.toFixed(4);
const pct = (a, b) => a == null || b == null || !b ? '' : `${a >= b ? '+' : ''}${((a / b - 1) * 100).toFixed(1)}%`;
const when = iso => iso.slice(5, 16).replace('T', ' ');

// The value at or just before time t.
function at(pts, t, key) {
	let v = null;
	for (let p of pts) { if (Date.parse(p.t) > t) break; if (p[key] != null) v = p[key]; }
	return v;
}

async function main() {
	let a = args(process.argv.slice(2));
	let ledger = new Ledger();
	let mine = ledger.db.prepare(`SELECT * FROM positions WHERE mode = 'live' AND lower(item) = lower(?)
		ORDER BY state IN ('opening', 'open', 'closing') DESC, id DESC`).all(a.item);
	ledger.close();
	let quality = a.quality ?? (mine[0] ? mine[0].quality : 6);
	let r = await engine('history', [a.item, '--quality', String(quality), '--old',
		...(a.days ? ['--days', String(a.days)] : [])]);
	let d = r.data;
	if (!d || !d.item) {
		console.log(`no item "${a.item}"${d && d.matches.length ? `; did you mean: ${d.matches.join(', ')}` : ''}`);
		process.exitCode = 1;
		return;
	}
	let pts = d.points;
	if (!pts.length) { console.log(`${d.item}: no books at quality ${quality}${quality === 6 ? ' (try --strange)' : ''}`); return; }
	let held = mine.find(p => p.quality === quality && ['opening', 'open', 'closing'].includes(p.state)) || null;
	let label = `${d.item} (${quality === 11 ? 'Strange' : quality === 6 ? 'Unique' : 'q' + quality})`;

	let last = pts[pts.length - 1], now = Date.parse(last.t);
	console.log(`${label}: ${pts.length} books, ${when(pts[0].t)} -> ${when(last.t)} UTC`);
	console.log(`  top buy order ${k4(last.bid1)}${last.bid1_user ? ` (${last.bid1_user})` : ''}   lowest sell ${k4(last.ask1)}   ` +
		`best bid the agent can sell into ${k4(last.tbid)}${last.tbid_user ? ` (${last.tbid_user})` : ''}`);
	let ago = [['1 h', 3600e3], ['24 h', 864e5], ['7 days', 7 * 864e5], ['30 days', 30 * 864e5]]
		.map(([n, ms]) => [n, at(pts, now - ms, 'bid1')]).filter(([, v]) => v != null);
	console.log('  top buy order then: ' + ago.map(([n, v]) => `${n} ago ${k4(v)} (${pct(last.bid1, v)})`).join(' · '));
	if (held) {
		console.log(`  the agent holds it: paid ${k4(held.entry_keys)}, sells at ${k4(held.take_profit_at)} ` +
			`(the tradable bid is ${pct(last.tbid, held.entry_keys) || '-'} vs paid, needs ${last.tbid ? pct(held.take_profit_at, last.tbid) : '-'} more)`);
	}

	let changes = [];
	for (let i = 1; i < pts.length; i++) {
		let p = pts[i - 1].bid1, q = pts[i].bid1;
		if (p != null && q != null && p !== q) changes.push([pts[i], p, q]);
	}
	console.log(`\n  last top-buy-order moves (of ${changes.length}):`);
	for (let [p, from, to] of changes.slice(-10)) {
		console.log(`    ${when(p.t)}  ${k4(from)} -> ${k4(to)}  ${pct(to, from).padStart(6)}  ${to > from ? 'up  ' : 'down'}  ${p.bid1_user || ''}`);
	}

	let days = new Map();
	for (let p of pts) {
		if (p.bid1 == null) continue;
		let k = p.t.slice(0, 10), e = days.get(k) || {open: p.bid1, lo: p.bid1, hi: p.bid1, asks: []};
		e.close = p.bid1; e.lo = Math.min(e.lo, p.bid1); e.hi = Math.max(e.hi, p.bid1);
		if (p.ask1 != null) e.asks.push(p.ask1);
		days.set(k, e);
	}
	console.log(`\n  by day (top buy order; UTC):   ${'open'.padStart(7)} ${'close'.padStart(7)} ${'low'.padStart(7)} ${'high'.padStart(7)}  lowest sell (median)`);
	for (let [k, e] of [...days].slice(-14)) {
		let s = e.asks.sort((x, y) => x - y), med = s.length ? s[Math.floor(s.length / 2)] : null;
		console.log(`    ${k}                  ${k4(e.open).padStart(7)} ${k4(e.close).padStart(7)} ${k4(e.lo).padStart(7)} ${k4(e.hi).padStart(7)}  ${k4(med)}`);
	}

	if (!a.chart) return;
	let out = path.join(BACKPACK, 'logs', `history-${d.item.replace(/[^\w]+/g, '_')}-q${quality}.html`);
	fs.writeFileSync(out, page(label, pts, held));
	console.log(`\nchart: ${out}`);
	if (a.open) spawn('cmd', ['/c', 'start', '', out], {detached: true, stdio: 'ignore'}).unref();
}

function page(label, pts, held) {
	let data = {label, built: new Date().toISOString(), held: held && {entry: held.entry_keys, tp: held.take_profit_at, bought: held.opened_at},
		p: pts.map(p => [Date.parse(p.t), p.bid1, p.ask1, p.tbid, p.src, p.bid1_user, p.tbid_user])};
	let json = JSON.stringify(data).replace(/</g, '\\u003c');
	return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${label.replace(/[<&]/g, '')} history</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0}.sub{color:var(--ink2);font-size:13px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;margin-top:16px}
.bar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;justify-content:space-between}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2)}.key{display:inline-block;width:14px;height:2px;margin-right:6px;vertical-align:3px}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--ink2);padding:4px 10px;font:inherit;font-size:12px;cursor:pointer}
.seg button.on{background:var(--grid);color:var(--ink)}
svg{display:block;width:100%;overflow:visible}svg text{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
#tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:12px;
 box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;z-index:9;font-variant-numeric:tabular-nums}
#tip .row{display:flex;align-items:center;gap:6px}
</style></head><body><main>
<h1 id="h"></h1><div class="sub" id="s"></div>
<section class="card"><div class="bar"><div class="legend" id="lg"></div>
<div class="seg" id="rg"><button data-d="1">24 h</button><button data-d="3">3 days</button><button data-d="7">7 days</button><button data-d="30">30 days</button><button data-d="0">all</button></div></div>
<div id="c" style="margin-top:8px"></div></section></main><div id="tip"></div>
<script>
const D=${json};
const $=id=>document.getElementById(id),k4=x=>x==null?'–':x.toFixed(4),NS='http://www.w3.org/2000/svg';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
$('h').textContent=D.label+' — price history';
$('s').textContent=D.p.length+' books · built '+D.built.slice(0,16).replace('T',' ')+' UTC · keys';
let lines=[['var(--s1)','top buy order','solid'],['var(--s2)','lowest sell','solid']];
if(D.held)lines.push(['var(--ink2)','we paid','dashed'],['var(--ink)','take-profit (+4%)','dashed']);
$('lg').innerHTML=lines.map(([c,t,st])=>'<span><span class="key" style="border-top:2px '+st+' '+c+';height:0"></span>'+t+'</span>').join('');
const el=(t,a,p)=>{let n=document.createElementNS(NS,t);for(let k in a)n.setAttribute(k,a[k]);if(p)p.appendChild(n);return n};
function ticks(lo,hi,n){let sp=hi-lo||1,st=Math.pow(10,Math.floor(Math.log10(sp/n))),e=sp/n/st;st*=e>=5?10:e>=2?5:e>=1?2:1;let o=[];for(let v=Math.ceil(lo/st)*st;v<=hi+1e-12;v+=st)o.push(+v.toFixed(10));return o}
let days=7;
function draw(){
 document.querySelectorAll('#rg button').forEach(b=>b.classList.toggle('on',+b.dataset.d===days));
 let box=$('c');box.innerHTML='';let tEnd=D.p[D.p.length-1][0],t0=days?tEnd-days*864e5:D.p[0][0];
 let P=D.p.filter(p=>p[0]>=t0);if(P.length<2){box.innerHTML='<p class="sub">Too few books in this range.</p>';return}
 t0=P[0][0];let W=box.clientWidth,H=Math.max(260,Math.min(420,W*.45)),m={l:56,r:16,t:12,b:28};
 let vals=P.flatMap(p=>[p[1],p[2]]).filter(v=>v!=null);if(D.held)vals.push(D.held.entry,D.held.tp);
 let lo=Math.min(...vals),hi=Math.max(...vals),pad=(hi-lo)*.08||.005;lo-=pad;hi+=pad;
 let x=t=>m.l+(t-t0)/(tEnd-t0||1)*(W-m.l-m.r),y=v=>m.t+(hi-v)/(hi-lo)*(H-m.t-m.b);
 let s=el('svg',{viewBox:'0 0 '+W+' '+H,height:H,role:'img','aria-label':D.label+' price history'},box);
 for(let v of ticks(lo,hi,5)){el('line',{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v),stroke:'var(--grid)'},s);el('text',{x:m.l-6,y:y(v)+4,'text-anchor':'end'},s).textContent=v.toFixed(3)}
 let span=tEnd-t0,step=span>20*864e5?7*864e5:span>4*864e5?864e5:span>36e5*30?6*36e5:36e5*3;
 for(let t=Math.ceil(t0/step)*step;t<=tEnd;t+=step){el('line',{x1:x(t),x2:x(t),y1:H-m.b,y2:H-m.b+4,stroke:'var(--axis)'},s);
  let d=new Date(t).toISOString();el('text',{x:x(t),y:H-8,'text-anchor':'middle'},s).textContent=step>=864e5?d.slice(5,10):d.slice(5,10)+' '+d.slice(11,13)+'h'}
 el('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b,stroke:'var(--axis)'},s);
 if(D.held)for(let [v,c,t,dy] of [[D.held.entry,'var(--ink2)','paid ',13],[D.held.tp,'var(--ink)','sell at ',-5]])if(v>=lo&&v<=hi){
  el('line',{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v),stroke:c,'stroke-width':1.5,'stroke-dasharray':'5 4'},s);
  el('text',{x:m.l+6,y:y(v)+dy,style:'fill:var(--ink2)'},s).textContent=t+k4(v)}
 for(let [i,c] of [[2,'var(--s2)'],[1,'var(--s1)']]){let d='',pen=false;
  for(let p of P){if(p[i]==null){pen=false;continue}d+=pen?'H'+x(p[0])+'V'+y(p[i]):'M'+x(p[0])+','+y(p[i]);pen=true}
  el('path',{d,fill:'none',stroke:c,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'},s)}
 let L=P[P.length-1];for(let [i,c] of [[1,'var(--s1)'],[2,'var(--s2)']])if(L[i]!=null){el('circle',{cx:x(L[0]),cy:y(L[i]),r:4,fill:c,stroke:'var(--surface)','stroke-width':2},s)}
 let cross=el('line',{y1:m.t,y2:H-m.b,stroke:'var(--axis)',visibility:'hidden'},s),dots=[1,2].map(i=>el('circle',{r:4,fill:i==1?'var(--s1)':'var(--s2)',stroke:'var(--surface)','stroke-width':2,visibility:'hidden'},s));
 let hit=el('rect',{x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b,fill:'transparent'},s),tip=$('tip');
 hit.addEventListener('mousemove',e=>{let r=s.getBoundingClientRect(),tx=t0+(e.clientX-r.left-m.l)/(W-m.l-m.r)*(tEnd-t0),q=P[0];
  for(let p of P){if(p[0]>tx)break;q=p}cross.setAttribute('x1',x(q[0]));cross.setAttribute('x2',x(q[0]));cross.setAttribute('visibility','visible');
  [1,2].forEach((i,j)=>{if(q[i]==null)return dots[j].setAttribute('visibility','hidden');dots[j].setAttribute('cx',x(q[0]));dots[j].setAttribute('cy',y(q[i]));dots[j].setAttribute('visibility','visible')});
  tip.innerHTML='<b>'+new Date(q[0]).toISOString().slice(5,16).replace('T',' ')+' UTC</b> <span class="sub">'+(q[4]=='watch'?'10-min watch':q[4]=='old'?'old pass':'hourly pass')+'</span>'+
   '<div class="row"><span class="key" style="border-top:2px solid var(--s1)"></span>top buy order '+k4(q[1])+' <span class="sub">'+esc(q[5])+'</span></div>'+
   '<div class="row"><span class="key" style="border-top:2px solid var(--s2)"></span>lowest sell '+k4(q[2])+'</div>'+
   '<div class="row sub">best bid the agent can sell into '+k4(q[3])+(q[6]?' ('+esc(q[6])+')':'')+'</div>';
  tip.style.display='block';let X=e.clientX+14,Y=e.clientY+14;if(X+tip.offsetWidth>innerWidth-8)X=e.clientX-tip.offsetWidth-14;if(Y+tip.offsetHeight>innerHeight-8)Y=e.clientY-tip.offsetHeight-14;tip.style.left=X+'px';tip.style.top=Y+'px'});
 hit.addEventListener('mouseleave',()=>{tip.style.display='none';cross.setAttribute('visibility','hidden');dots.forEach(d=>d.setAttribute('visibility','hidden'))});
}
$('rg').addEventListener('click',e=>{let b=e.target.closest('button');if(!b)return;days=+b.dataset.d;try{localStorage.setItem('hist-days',days)}catch(_){}draw()});
try{days=+(localStorage.getItem('hist-days')??7)}catch(_){days=7}
draw();let rt;addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(draw,100)});
</script></body></html>`;
}

main().catch(err => { console.error(err.message); process.exitCode = 1; });

"""One self-contained HTML viewer: fenced bid/ask + pricedb per item over time.

For every series it plots four lines over wall-clock time: the fenced top-5 bid
and ask, and the pricedb suggested buy/sell (snapshot_baseline) dashed alongside.
Search an item and its chart draws (vanilla SVG, no network).

    python -m engine timeseries --out item_timeseries.html --min-points 5
"""
import json
import os

from engine.panel import SERIES, load_panel
from engine.screens._common import parser, qualities


def _r4(x):
    return None if x != x else round(float(x), 4)   # NaN -> null


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--out", default="item_timeseries.html")
    ap.add_argument("--min-points", type=int, default=5, dest="min_points",
                    help="drop items with fewer passes that have a bid or ask")
    args = ap.parse_args(argv)
    qs = qualities(args)

    p = load_panel(args.db, use_cache=not args.no_cache)
    p = p[p.quality.isin(qs)]
    p = p[p[["bid5", "ask5", "pdb_buy", "pdb_sell"]].notna().any(axis=1)]

    items = []
    for _, s in p.groupby(SERIES, sort=False):
        if (s.bid5.notna() | s.ask5.notna()).sum() < args.min_points:
            continue
        pts = [[int(r.t.timestamp()), _r4(r.bid5), _r4(r.ask5), _r4(r.pdb_buy),
                _r4(r.pdb_sell)] for r in s.itertuples()]
        items.append({"l": s.label.iloc[0].replace(" (", "  (", 1), "q": int(s.quality.iloc[0]),
                      "p": pts})
    items.sort(key=lambda it: it["l"].lower())

    html = _TEMPLATE.replace("/*DATA*/", json.dumps({"items": items}, separators=(",", ":")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {args.out}  ({len(items)} items, {os.path.getsize(args.out)/1e6:.2f} MB)")


_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Item time series</title>
<style>
  :root { color-scheme: light dark; --bg:#faf9f5; --fg:#1a1a19; --mut:#88857c;
    --card:#fff; --line:#e1e0d9; --sel:#e6f1fb; }
  @media (prefers-color-scheme: dark) { :root { --bg:#1a1a19; --fg:#f0efec;
    --mut:#8a887f; --card:#242422; --line:#34342f; --sel:#123; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font-family:system-ui,
    -apple-system,Segoe UI,Roboto,sans-serif; display:flex; height:100vh; }
  #side { width:300px; flex:none; border-right:0.5px solid var(--line);
    display:flex; flex-direction:column; }
  #side h1 { font-size:15px; font-weight:500; margin:0; padding:14px 14px 8px; }
  #q { margin:0 14px 8px; padding:8px 10px; font-size:13px; border:0.5px solid
    var(--line); border-radius:8px; background:var(--card); color:var(--fg); }
  #list { overflow-y:auto; flex:1; }
  .row { padding:6px 14px; font-size:12.5px; cursor:pointer; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; border-left:3px solid transparent; }
  .row:hover { background:var(--sel); }
  .row.on { background:var(--sel); border-left-color:#2a78d6; font-weight:500; }
  #main { flex:1; display:flex; flex-direction:column; padding:16px 20px; min-width:0; }
  #title { font-size:16px; font-weight:500; margin:0 0 2px; }
  #meta { font-size:12px; color:var(--mut); margin:0 0 10px; }
  #legend { display:flex; gap:16px; flex-wrap:wrap; font-size:12px;
    color:var(--mut); margin-bottom:8px; }
  #legend span { display:flex; align-items:center; gap:6px; }
  .sw { width:16px; height:0; border-top-width:2.5px; border-top-style:solid; }
  #wrap { flex:1; min-height:0; position:relative; }
  svg { width:100%; height:100%; display:block; }
  .ax { stroke:var(--line); stroke-width:1; }
  .gl { stroke:var(--line); stroke-width:1; opacity:.5; }
  .axt { fill:var(--mut); font-size:11px; }
  #tip { position:absolute; pointer-events:none; background:var(--card);
    border:0.5px solid var(--line); border-radius:6px; padding:6px 8px;
    font-size:11.5px; display:none; white-space:nowrap; }
  #controls { font-size:12px; color:var(--mut); margin-top:6px; }
  #count { padding:0 14px 8px; font-size:11px; color:var(--mut); }
</style></head><body>
<div id="side">
  <h1>Item time series</h1>
  <input id="q" placeholder="Search item...">
  <div id="count"></div>
  <div id="list"></div>
</div>
<div id="main">
  <div id="title">Pick an item</div>
  <div id="meta"></div>
  <div id="legend">
    <span><span class="sw" style="border-color:#2a78d6"></span>buy bid</span>
    <span><span class="sw" style="border-color:#eb6834"></span>sell ask</span>
    <span><span class="sw" style="border-color:#2a78d6;border-top-style:dashed"></span>pricedb buy</span>
    <span><span class="sw" style="border-color:#eb6834;border-top-style:dashed"></span>pricedb sell</span>
  </div>
  <div id="wrap"><svg id="chart" viewBox="0 0 900 460" preserveAspectRatio="none"></svg>
    <div id="tip"></div></div>
  <label id="controls"><input type="checkbox" id="logy"> log scale y</label>
</div>
<script>
const DB = /*DATA*/;
const ITEMS = DB.items;
const SER = [ {k:1,c:"#2a78d6",d:0}, {k:2,c:"#eb6834",d:0},
              {k:3,c:"#2a78d6",d:1}, {k:4,c:"#eb6834",d:1} ];
const listEl=document.getElementById("list"), qEl=document.getElementById("q"),
  countEl=document.getElementById("count"), svg=document.getElementById("chart"),
  titleEl=document.getElementById("title"), metaEl=document.getElementById("meta"),
  tip=document.getElementById("tip"), logEl=document.getElementById("logy");
let cur=-1, filtered=ITEMS.map((_,i)=>i);

function renderList(){
  const term=qEl.value.toLowerCase();
  filtered=ITEMS.map((_,i)=>i).filter(i=>ITEMS[i].l.toLowerCase().includes(term));
  countEl.textContent=filtered.length+" / "+ITEMS.length+" items";
  listEl.innerHTML=filtered.slice(0,800).map(i=>
    `<div class="row${i===cur?' on':''}" data-i="${i}">${ITEMS[i].l}</div>`).join("");
}
listEl.addEventListener("click",e=>{const r=e.target.closest(".row");
  if(r){select(+r.dataset.i);}});
qEl.addEventListener("input",renderList);

const W=900,H=460,ML=64,MR=16,MT=14,MB=34;
function fmt(t){const d=new Date(t*1000);const p=n=>String(n).padStart(2,"0");
  return `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;}
function fmtDay(t){const d=new Date(t*1000);const p=n=>String(n).padStart(2,"0");
  return `${p(d.getMonth()+1)}-${p(d.getDate())}`;}
function niceNum(v){return v>=100?v.toFixed(0):v>=10?v.toFixed(1):
  v>=1?v.toFixed(2):v.toFixed(3);}

function select(i){cur=i; renderList(); draw();}

function draw(){
  if(cur<0){svg.innerHTML="";return;}
  const it=ITEMS[cur], pts=it.p;
  titleEl.textContent=it.l;
  const log=logEl.checked;
  let vmin=Infinity,vmax=-Infinity,tmin=Infinity,tmax=-Infinity;
  for(const p of pts){tmin=Math.min(tmin,p[0]);tmax=Math.max(tmax,p[0]);
    for(let s=1;s<=4;s++){const v=p[s];
      if(v!=null&&v>0){vmin=Math.min(vmin,v);vmax=Math.max(vmax,v);}}}
  if(!isFinite(vmin)){svg.innerHTML='<text x="60" y="40" class="axt">no data</text>';return;}
  metaEl.textContent=`${pts.length} passes  ·  ${fmtDay(tmin)} → ${fmtDay(tmax)}  ·  keys`;
  const pad=(vmax-vmin)*0.08||vmax*0.1||1;
  let lo=vmin-pad, hi=vmax+pad;
  if(log){lo=Math.log10(vmin); hi=Math.log10(vmax); const lp=(hi-lo)*0.08||0.1; lo-=lp;hi+=lp;}
  const X=t=>ML+(tmax===tmin?0:(t-tmin)/(tmax-tmin))*(W-ML-MR);
  const Y=v=>{const u=log?Math.log10(v):v; return MT+(1-(u-lo)/(hi-lo))*(H-MT-MB);};
  let g="";
  // y gridlines + labels
  for(let k=0;k<=4;k++){const u=lo+(hi-lo)*k/4; const val=log?Math.pow(10,u):u;
    const y=MT+(1-k/4)*(H-MT-MB);
    g+=`<line class="gl" x1="${ML}" y1="${y}" x2="${W-MR}" y2="${y}"/>`;
    g+=`<text class="axt" x="${ML-6}" y="${y+3}" text-anchor="end">${niceNum(val)}</text>`;}
  // x ticks (~6)
  const nt=Math.min(6,pts.length);
  for(let k=0;k<nt;k++){const t=tmin+(tmax-tmin)*k/(nt-1||1);
    const x=X(t); g+=`<text class="axt" x="${x}" y="${H-MB+16}" text-anchor="middle">${fmtDay(t)}</text>`;}
  g+=`<line class="ax" x1="${ML}" y1="${H-MB}" x2="${W-MR}" y2="${H-MB}"/>`;
  g+=`<line class="ax" x1="${ML}" y1="${MT}" x2="${ML}" y2="${H-MB}"/>`;
  // series polylines (skip nulls with gaps)
  for(const s of SER){let seg=[],paths=[];
    for(const p of pts){const v=p[s.k];
      if(v!=null&&v>0){seg.push(`${X(p[0]).toFixed(1)},${Y(v).toFixed(1)}`);}
      else{if(seg.length>1)paths.push(seg.join(" "));seg=[];}}
    if(seg.length>1)paths.push(seg.join(" "));
    const dash=s.d?'stroke-dasharray="5 4"':"";
    for(const pth of paths)
      g+=`<polyline points="${pth}" fill="none" stroke="${s.c}" stroke-width="${s.d?1.4:1.9}" ${dash} stroke-linejoin="round"/>`;
  }
  svg.innerHTML=g;
  svg._pts=pts; svg._X=X; svg._Y=Y;
}

svg.addEventListener("mousemove",e=>{
  if(cur<0||!svg._pts)return;
  const r=svg.getBoundingClientRect();
  const px=(e.clientX-r.left)/r.width*W;
  const pts=svg._pts; let best=null,bd=1e9;
  for(const p of pts){const dx=Math.abs(svg._X(p[0])-px); if(dx<bd){bd=dx;best=p;}}
  if(!best){tip.style.display="none";return;}
  const nm=["buy bid","sell ask","pricedb buy","pricedb sell"];
  let rows=`<b>${fmt(best[0])}</b>`;
  for(let s=1;s<=4;s++) if(best[s]!=null) rows+=`<br>${nm[s-1]}: ${best[s]}`;
  tip.innerHTML=rows; tip.style.display="block";
  const lx=e.clientX-r.left, ly=e.clientY-r.top;
  tip.style.left=Math.min(lx+12,r.width-140)+"px"; tip.style.top=(ly+12)+"px";
});
svg.addEventListener("mouseleave",()=>tip.style.display="none");
logEl.addEventListener("change",draw);
document.addEventListener("keydown",e=>{
  if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();
    const pos=filtered.indexOf(cur);
    let np=pos+(e.key==="ArrowDown"?1:-1);
    if(np<0)np=0; if(np>=filtered.length)np=filtered.length-1;
    if(filtered[np]!=null){select(filtered[np]);
      const el=listEl.querySelector(".row.on"); if(el)el.scrollIntoView({block:"nearest"});}}
});
renderList();
if(ITEMS.length) select(0);
</script></body></html>"""


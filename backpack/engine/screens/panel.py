"""panel: the live control panel -- the agent and its buttons, the scrape's drift, the model's
feature drift and the market's buy-order moves on one page that updates itself (127.0.0.1 only).

    python -m engine panel [--port 8770] [--open] [--check 60]

It opens at once from the stats cache (engine/stats.py). A background thread looks at the
db every --check seconds; when new books arrived it summarises them -- the scrape first
(~15 s), then the model's features (~1.5 min: it builds the forecast frame) -- the second
only once no new book has come for FEATURE_SETTLE_S and the newest pass is whole (or after
FEATURE_STALE_S regardless), so it does not build its frame while the agent builds the same
one for its picks on the new pass -- and bumps a version.
The page polls /api/state and redraws when the version moves. The db is only read.

    GET /api/state                 version, what it is doing, the newest books' times
    GET /api/data                  everything the page draws (built once per version)
    GET /api/series?group=&col=    one column's p10..p90 band and missing share per pass
    GET /api/agent                 the live agent (agent/panel.js: state, limits, trades, bots,
                                   the log's tail) + the panel's own action log
    GET /api/items                 [[item, quality]] with a stored book (the Books tab's search)
    GET /api/item?item=&q=[&k=]    one good's books over time (k: its kind code, history.PLAIN_CODE
                                   by default; engine history: bid, tradable bid,
                                   ask per stored book, ~0.2 s), each with its book's (src, id)
    GET /api/book?src=&id=         one stored book, every listing kept / dropped and why, and
                                   whether the agent could trade with it
    POST /api/action               {action, args}: one of ACTIONS, run as the `agent` command it
                                   stands for (agent/*.js) -- nothing the CLI cannot do

The actions need the page's token (a fresh one each start, only in the page) and a Host of
127.0.0.1 / localhost: another site open in the browser cannot reach them. Every action is
appended to backpack/logs/panel_actions.log with its answer.
"""
import json
import os
import re
import secrets
import subprocess
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from engine import stats as S
from engine.events import SEGMENT_GAP_H
from engine.panel import APP_DB, cache_dir_for, load_panel
from engine.screens import history as H
from engine.screens._common import parser

_TEMPLATE = Path(__file__).with_name("panel.html")
_BACKPACK = Path(__file__).resolve().parents[2]
AGENT_DIR = _BACKPACK.parent / "agent"
ACTION_LOG = Path(os.environ.get("PANEL_ACTION_LOG") or _BACKPACK / "logs" / "panel_actions.log")
AGENT_TTL_S = 5            # /api/agent re-runs agent/panel.js at most this often
ITEMS_TTL_S = 3600         # /api/items re-reads the item list at most this often
NOW_H = 24                 # the "now" window: the last 24 h of passes
FEATURE_SETTLE_S = 300     # the features wait until no new book came for this long (the agent's picks)
FEATURE_STALE_S = 1800     # ... or until the books have waited this long, whole pass or not


def _j(x):
    """A JSON-safe number (NaN / inf -> None)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _col(s, nd=6):
    return [None if v is None else round(v, nd) for v in map(_j, s)]


def payload(st, db):
    """Everything the page draws, from one Stats."""
    p = st.passes.sort_values("t").reset_index(drop=True)
    now = pd.Timestamp.now(tz="UTC")
    last = p.t.iloc[-1] if len(p) else now
    win = (last - pd.Timedelta(hours=NOW_H), last + pd.Timedelta(seconds=1))
    ref7 = (win[0] - pd.Timedelta(days=S.REF_DAYS), win[0])
    te = st.meta.get("train_end")
    ref_train = (pd.Timestamp("2000-01-01", tz="UTC"), pd.Timestamp(te) + pd.Timedelta(seconds=1)) if te else None

    gaps = []
    dt = p.t.diff().dt.total_seconds() / 3600
    for i in np.flatnonzero((dt > SEGMENT_GAP_H).to_numpy()):
        gaps.append({"from": p.t.iloc[i - 1].isoformat(), "to": p.t.iloc[i].isoformat(),
                     "hours": round(float(dt.iloc[i]), 1)})

    def p50s(group):
        c = st.cols[st.cols.group == group].pivot(index="pass_id", columns="col", values="p50")
        c = c.reindex(p.pass_id)
        return {k: _col(c[k]) for k in c.columns}

    def table(group, ref):
        if ref is None:
            return []
        return [{k: (_j(v) if isinstance(v, float) else v) for k, v in r.items()}
                for r in S.drift_table(st, group, win, ref)]

    def heat(group):
        h = S.daily_psi(st, group)
        return {"days": h["days"], "cols": h["cols"],
                "z": [[_j(v) for v in row] for row in np.asarray(h["z"]).tolist()] if len(h["cols"]) else []}

    has_feat = bool((st.cols.group == "features").any()) if len(st.cols) else False
    return {
        "meta": {"db": str(db), "built": st.meta.get("built", {}), "train_end": te,
                 "now_h": NOW_H, "ref_days": S.REF_DAYS, "psi_watch": S.PSI_WATCH,
                 "psi_drift": S.PSI_DRIFT, "flat": S.FLAT, "idx_clip": S.IDX_CLIP,
                 "window": [win[0].isoformat(), win[1].isoformat()], "has_features": has_feat},
        "passes": {"pass_id": [int(x) for x in p.pass_id], "t": [x.isoformat() for x in p.t],
                   **{c: _col(p[c]) for c in p.columns if c not in ("pass_id", "t")}},
        "gaps": gaps,
        "scrape": {"table": table("scrape", ref7), "heat": heat("scrape"), "p50": p50s("scrape"),
                   "docs": S.SCRAPE_DOCS},
        "features": ({"train": table("features", ref_train), "week": table("features", ref7),
                      "heat": heat("features"), "p50": p50s("features"),
                      "docs": S.feature_docs(), "groups": S.feature_groups()} if has_feat else None),
    }


class Live:
    """The page's state and the thread that keeps it current."""

    def __init__(self, db, check_s):
        self.db, self.check_s = db, check_s
        self.lock = threading.Lock()
        self.version, self.body, self.st = 0, b"null", None
        self.doing, self.error, self.fresh = "starting", None, {}
        self.mark, self.mark_at = None, time.time()

    def publish(self, st):
        body = json.dumps(payload(st, self.db), separators=(",", ":")).encode("utf-8")
        with self.lock:
            self.st, self.body = st, body
            self.version += 1

    def state(self):
        with self.lock:
            return {"version": self.version, "doing": self.doing, "error": self.error,
                    "fresh": self.fresh, "server_now": pd.Timestamp.now(tz="UTC").isoformat()}

    def step(self):
        """One check: summarise what is new, publish after each stage."""
        self.fresh = S.freshness(self.db)
        mark = S.watermark(self.db)
        if mark != self.mark:
            self.mark, self.mark_at = mark, time.time()
        st, panel = self.st, None
        if st is None or st.meta.get("watermark", {}).get("scrape") != mark:
            self.doing = "summarising the new books"
            st, panel = S.refresh_scrape(self.db)
            self.publish(st)
        if st.meta.get("watermark", {}).get("features") != mark:
            if panel is None:
                panel = load_panel(self.db)
            from engine.forecast import whole_pass
            whole = whole_pass(panel) == int(panel.pass_id.max())
            quiet = time.time() - self.mark_at
            first = "features" not in st.meta.get("watermark", {})
            if first or (whole and quiet >= FEATURE_SETTLE_S) or quiet >= FEATURE_STALE_S:
                self.doing = "summarising the model's features"
                st = S.refresh_features(self.db, panel=panel)
                self.publish(st)
            else:
                self.doing = f"features wait for the books to settle ({int(quiet)} s quiet)"
                return
        self.doing = "watching"

    def run(self):
        st = S.load(self.db)
        if st is not None:
            self.publish(st)
        stop = threading.Event()
        while not stop.is_set():
            try:
                self.step()
                self.error = None
            except Exception as err:          # a locked cache or a db mid-sync: next check
                self.error = f"{type(err).__name__}: {err}"
                self.doing = "waiting to retry"
                traceback.print_exc()
            stop.wait(self.check_s)

    def series(self, group, col):
        st = self.st
        if st is None:
            return None
        r = S.series(st, group, col)
        return {"t": [x.isoformat() for x in r.t], **{c: _col(r[c]) for c in ["mean", "miss"] + S.QCOLS}}


# -- the agent ------------------------------------------------------------------------

def _node(args, timeout=30):
    """Run one agent script: (exit code, stdout + stderr)."""
    env = dict(os.environ, AGENT_ACTION_BY="the panel")
    try:
        r = subprocess.run(["node", "--no-warnings", *args], cwd=AGENT_DIR, capture_output=True,
                           text=True, encoding="utf-8", timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, f"{type(err).__name__}: {err}"
    return r.returncode, (r.stdout + r.stderr).strip()


_REQUESTS = ("restart", "shutdown", "cycle")


def _bad(what):
    raise ValueError(f"bad {what}")


def _num(v, lo, hi):
    x = float(v)
    if not (lo <= x <= hi):
        raise ValueError(f"{x:g} is outside {lo:g}..{hi:g}")
    return f"{x:g}"


def _id(v):
    if not re.fullmatch(r"\d{1,9}", str(v)):
        raise ValueError(f"bad position id {v!r}")
    return str(v)


# action -> (args -> the agent script's argv). Only these exist; `start` is below.
ACTIONS = {
    "kill_on": lambda a: ["stop.js"],
    "kill_off": lambda a: ["stop.js", "off"],
    "resume": lambda a: ["stop.js", "resume"],
    "restart": lambda a: ["control.js", "restart", "from the panel"],
    "shutdown": lambda a: ["control.js", "shutdown", "from the panel"],
    "cycle": lambda a: ["control.js", "cycle", "from the panel"],
    "cancel": lambda a: ["control.js", "cancel", a["name"]] if a.get("name") in _REQUESTS else _bad("name"),
    "set_limit": lambda a: (["set.js", a["key"], _num(a["value"], 0, 1e6)]
                            if re.fullmatch(r"[a-z_]{3,40}", str(a.get("key", ""))) else _bad("key")),
    "sell_now": lambda a: ["position.js", _id(a["id"]), "sell-now"],
    "take_profit": lambda a: ["position.js", _id(a["id"]), "tp", _num(a["pct"], 0.01, 100)],
    "horizon": lambda a: ["position.js", _id(a["id"]), "horizon",
                          "reset" if a.get("hours") == "reset" else _num(a["hours"], 0, 336)],
}


def _audit(action, args, code, out):
    ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (f"{pd.Timestamp.now(tz='UTC').isoformat()} {action} {json.dumps(args, sort_keys=True)} "
            f"-> exit {code}: {' | '.join(out.splitlines())[:300]}\n")
    with open(ACTION_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def _action_log(n=30):
    try:
        with open(ACTION_LOG, encoding="utf-8") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


class Agent:
    """agent/panel.js, cached a few seconds; the actions."""

    def __init__(self, node=None):
        self.node = node or _node
        self.lock = threading.Lock()
        self.at, self.body = 0.0, None

    def snapshot(self, fresh=False):
        with self.lock:
            if fresh or self.body is None or time.time() - self.at > AGENT_TTL_S:
                code, out = self.node(["panel.js"])
                try:
                    data = json.loads(out) if code == 0 else {"error": out[-600:]}
                except ValueError:
                    data = {"error": out[-600:]}
                data["actions"] = _action_log()
                self.body, self.at = json.dumps(data, separators=(",", ":")).encode("utf-8"), time.time()
            return self.body

    def act(self, action, args):
        """Run one action: {ok, code, output}. Logged whatever the answer."""
        if action == "start":
            snap = json.loads(self.snapshot(fresh=True))
            if snap.get("state", {}).get("running"):
                out = {"ok": False, "code": 1, "output": "the agent is already running"}
            else:
                subprocess.Popen('start "TF2 agent (AUTO)" run-auto.cmd', cwd=AGENT_DIR, shell=True)
                out = {"ok": True, "code": 0,
                       "output": "started in its own window (AUTO): it logs in, then runs its first cycle"}
        elif action not in ACTIONS:
            out = {"ok": False, "code": 2, "output": f"unknown action {action}"}
        else:
            try:
                argv = ACTIONS[action](args or {})
            except (KeyError, ValueError, TypeError) as err:
                out = {"ok": False, "code": 2, "output": f"refused: {err}"}
            else:
                code, text = self.node(argv)
                out = {"ok": code == 0, "code": code, "output": text}
        _audit(action, args or {}, out["code"], out["output"])
        self.snapshot(fresh=True)
        return out


def allowed(headers, port, token, post=False):
    """A request from a page this server served: a local Host (no DNS rebinding) and, on a
    POST, a same-origin Origin (when sent) and the page's token."""
    hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
    if headers.get("Host") not in hosts:
        return False
    if not post:
        return True
    origin = headers.get("Origin")
    if origin and origin not in tuple(f"http://{h}" for h in hosts):
        return False
    return secrets.compare_digest(headers.get("X-Panel-Token", ""), token)


class Books:
    """The Books tab: one item's history and one stored book, read from the app db on demand."""

    def __init__(self, db=APP_DB):
        self.db = db
        self.lock = threading.Lock()
        self.at, self.items_body = 0.0, None

    def items(self):
        with self.lock:
            if self.items_body is None or time.time() - self.at > ITEMS_TTL_S:
                self.items_body, self.at = json.dumps(H.items(self.db), separators=(",", ":")).encode("utf-8"), time.time()
            return self.items_body

    def item(self, q):
        name, quality, kind = q.get("item", ""), int(q.get("q", "6")), q.get("k") or H.PLAIN_CODE
        return {"item": name, "quality": quality, "kind": kind,
                "points": H.history(name, quality, app_db=self.db, kind=kind)}

    def book(self, q):
        src, sid = q.get("src", ""), q.get("id", "")
        if src not in H._TABLES or not re.fullmatch(r"\d{1,12}", sid):
            raise ValueError("bad src or id")
        return H.book(src, int(sid), app_db=self.db)


def make_server(live, port, agent=None, token=None, books=None):
    agent = agent or Agent()
    books = books or Books()
    token = token or secrets.token_urlsafe(24)
    page = _TEMPLATE.read_bytes().replace(b"__PANEL_TOKEN__", token.encode())

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/action" or not allowed(self.headers, self.server.server_address[1], token, post=True):
                return self._send(403, b'{"error":"forbidden"}', "application/json")
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
            except ValueError:
                return self._send(400, b'{"error":"bad body"}', "application/json")
            out = agent.act(str(body.get("action", "")), body.get("args") or {})
            return self._send(200, json.dumps(out).encode("utf-8"), "application/json")

        def do_GET(self):
            u = urlparse(self.path)
            if not allowed(self.headers, self.server.server_address[1], token):
                return self._send(403, b"forbidden", "text/plain")
            if u.path == "/api/agent":
                return self._send(200, agent.snapshot(), "application/json")
            if u.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._send(200, json.dumps(live.state()).encode(), "application/json")
            if u.path == "/api/data":
                with live.lock:
                    body = live.body
                return self._send(200, body, "application/json")
            if u.path in ("/api/item", "/api/book"):
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    out = books.item(q) if u.path == "/api/item" else books.book(q)
                except ValueError as err:
                    return self._send(400, json.dumps({"error": str(err)}).encode(), "application/json")
                if out is None:
                    return self._send(404, b'{"error":"no such book"}', "application/json")
                return self._send(200, json.dumps(out, separators=(",", ":")).encode("utf-8"), "application/json")
            if u.path == "/api/items":
                return self._send(200, books.items(), "application/json")
            if u.path == "/api/series":
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                out = live.series(q.get("group", ""), q.get("col", ""))
                if out is None or not out["t"]:
                    return self._send(404, b'{"error":"no such column"}', "application/json")
                return self._send(200, json.dumps(out, separators=(",", ":")).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv):
    ap = parser(__doc__)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="open the page in the browser")
    ap.add_argument("--check", type=float, default=60.0, help="seconds between db checks")
    args = ap.parse_args(argv)

    live = Live(args.db, args.check)
    threading.Thread(target=live.run, daemon=True).start()
    httpd = make_server(live, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"serving {url}  (Ctrl+C to stop; cache in {cache_dir_for(args.db)})", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

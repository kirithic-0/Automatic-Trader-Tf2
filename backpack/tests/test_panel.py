# -*- coding: utf-8 -*-
"""Offline test for the panel server's agent side (engine/screens/panel.py): the actions map to
the agent's own commands and refuse anything else, only a page this server served may act
(token, Host, Origin), every action is audited. A fake `node` stands in: no agent command runs.

    python test_panel.py
"""
import _paths  # noqa: E402
_paths.use_backpack()
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from engine.screens import panel as P

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-4s %-64s %s" % ("PASS" if ok else "FAIL", name, got if ok else "%r (want %r)" % (got, want)))
    if not ok:
        fails.append(name)


class FakeNode:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        if args == ["panel.js"]:
            return 0, json.dumps({"state": {"running": True}, "rows": []})
        return 0, "done: " + " ".join(args)


def test_actions():
    print("actions: the agent's commands, nothing else")
    P.ACTION_LOG = P.Path(tempfile.mkdtemp()) / "actions.log"
    node = FakeNode()
    a = P.Agent(node=node)
    check("kill switch on", a.act("kill_on", {})["ok"], True)
    check("... is stop.js", node.calls[0], ["stop.js"])
    a.act("take_profit", {"id": 12, "pct": "6"})
    check("take-profit -> position.js <id> tp <pct>", node.calls[-2], ["position.js", "12", "tp", "6"])
    a.act("horizon", {"id": "12", "hours": "reset"})
    check("horizon reset", node.calls[-2], ["position.js", "12", "horizon", "reset"])
    a.act("set_limit", {"key": "max_keys_in_play", "value": "3"})
    check("a limit -> set.js", node.calls[-2], ["set.js", "max_keys_in_play", "3"])
    n = len(node.calls)
    for action, args, why in (("take_profit", {"id": "1; del", "pct": 5}, "bad position id"),
                              ("horizon", {"id": 3, "hours": 999}, "outside"),
                              ("set_limit", {"key": "a b", "value": 1}, "bad key"),
                              ("cancel", {"name": "everything"}, "bad name"),
                              ("format_disk", {}, "unknown action")):
        out = a.act(action, args)
        check(f"refused: {action} {args}", (out["ok"], why in out["output"]), (False, True))
    check("... and nothing run for them (only the snapshot refresh)", [c for c in node.calls[n:] if c != ["panel.js"]], [])
    check("start refused while it runs", a.act("start", {})["output"], "the agent is already running")
    log = P.ACTION_LOG.read_text(encoding="utf-8").splitlines()
    check("every action audited, refused ones too", len(log), 10)
    check("... with its answer", "exit 2: refused" in log[5], True)


def test_guard():
    print("only a page this server served may act")
    port, tok = 8770, "secret"
    ok = {"Host": f"127.0.0.1:{port}", "X-Panel-Token": tok}
    check("the page itself", P.allowed(ok, port, tok, post=True), True)
    check("localhost too", P.allowed({**ok, "Host": f"localhost:{port}"}, port, tok, post=True), True)
    check("no token", P.allowed({"Host": ok["Host"]}, port, tok, post=True), False)
    check("a wrong token", P.allowed({**ok, "X-Panel-Token": "guess"}, port, tok, post=True), False)
    check("another site's Origin", P.allowed({**ok, "Origin": "http://evil.example"}, port, tok, post=True), False)
    check("a rebinding Host", P.allowed({**ok, "Host": f"evil.example:{port}"}, port, tok, post=True), False)
    check("a GET needs only the local Host", P.allowed({"Host": ok["Host"]}, port, tok), True)


def test_server():
    print("over HTTP: the token is only in the page")
    P.ACTION_LOG = P.Path(tempfile.mkdtemp()) / "actions.log"
    node = FakeNode()

    class NoLive:
        lock = threading.Lock()
        body = b"null"

        def state(self):
            return {"version": 0}

    class FakeBooks:
        def items(self):
            return b'[["Hat",6]]'

        def item(self, q):
            return {"item": q["item"], "quality": int(q["q"]), "points": []}

        def book(self, q):
            if q.get("id") == "9":
                return None
            return P.Books.book(P.Books(), {"src": q.get("src"), "id": "x"}) if q.get("id") == "x" else {"id": q["id"]}

    httpd = P.make_server(NoLive(), 0, agent=P.Agent(node=node), token="tok123", books=FakeBooks())
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def post(headers):
        req = urllib.request.Request(base + "/api/action", data=b'{"action":"cycle"}', method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    page = urllib.request.urlopen(base + "/").read().decode()
    check("the page carries its token", 'const TOKEN = "tok123"' in page, True)
    check("POST without it: 403", post({})[0], 403)
    code, out = post({"X-Panel-Token": "tok123"})
    check("POST with it: run", (code, out["ok"]), (200, True))
    check("... as control.js cycle", ["control.js", "cycle", "from the panel"] in node.calls, True)
    with urllib.request.urlopen(base + "/api/agent") as r:
        check("GET /api/agent: the snapshot + the action log", (json.loads(r.read())["state"]["running"]), True)

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None
    check("GET /api/items", get("/api/items"), (200, [["Hat", 6]]))
    check("GET /api/item", get("/api/item?item=Hat&q=6")[1]["item"], "Hat")
    check("GET /api/book", get("/api/book?src=pass&id=3"), (200, {"id": "3"}))
    check("GET /api/book, no such book: 404", get("/api/book?src=pass&id=9")[0], 404)
    check("GET /api/book, a bad id: 400", get("/api/book?src=pass&id=x")[0], 400)
    httpd.shutdown()


if __name__ == "__main__":
    test_actions()
    test_guard()
    test_server()
    print("\nFAILED: " + ", ".join(fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)

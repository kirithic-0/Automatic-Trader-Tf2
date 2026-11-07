"""The collector (collector/) runs copies of the engine's read rules: pin them.

  * collector/engine_book.py is engine/book.py byte for byte -- the take-profit alert
    (held_alert.py) reads bids with the engine's own fence and UNTRUSTED list;
  * collector/book_codec.py is backpack/book_codec.py byte for byte;
  * held_alert's TAKE_PROFIT / MAX_JUMP are engine.forecast's.

A change to engine/book.py fails here until it is copied:
    copy backpack\\engine\\book.py collector\\engine_book.py

    python backpack/tests/test_collector_copies.py
"""
import ast
import os
import sys

import _paths  # noqa: E402
from _paths import BACKPACK, COLLECTOR  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))
    if not ok:
        fails.append(name)


def raw(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")      # git may check either out CRLF


def constants(path, names):
    tree = ast.parse(raw(path).decode("utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id in names:
                out[t.id] = ast.literal_eval(node.value)
    return out


_paths.need_collector()

check("engine_book.py == engine/book.py",
      raw(os.path.join(COLLECTOR, "engine_book.py")) == raw(os.path.join(BACKPACK, "engine", "book.py")), True)
check("collector book_codec.py == backpack/book_codec.py",
      raw(os.path.join(COLLECTOR, "book_codec.py")) == raw(os.path.join(BACKPACK, "book_codec.py")), True)
names = {"TAKE_PROFIT", "MAX_JUMP"}
check("held_alert's exit constants are engine.forecast's",
      constants(os.path.join(COLLECTOR, "held_alert.py"), names),
      constants(os.path.join(BACKPACK, "engine", "forecast", "config.py"), names))

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all passed")

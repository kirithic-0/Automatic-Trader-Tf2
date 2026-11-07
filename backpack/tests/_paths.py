"""Import paths for the tests, in one place. Tests run as plain scripts from anywhere, and this
folder is always on sys.path then, so `import _paths` works.

    import _paths
    _paths.use_backpack()      # backpack/ first: `import engine`
    _paths.use_collector()     # last: the collector's writers (snapshot_store, bot_registry)

The collector is in this repository (collector/ and sync/ at the root). A test that needs it and
can't find it exits as skipped, SKIP_EXIT, which run_tests.ps1 reports as SKIP rather than PASS.
"""
import os
import sys

BACKPACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADING = os.path.dirname(BACKPACK)
COLLECTOR_REPO = TRADING                                   # the collector lives in this repo
COLLECTOR = os.path.join(COLLECTOR_REPO, "collector")
SKIP_EXIT = 77


def need_collector():
    """Stop this test file, as skipped, when the collector isn't in this repo."""
    if not os.path.isdir(COLLECTOR):
        print(f"  skip  needs the collector, not found at {COLLECTOR}")
        sys.exit(SKIP_EXIT)


def use_backpack():
    sys.path.insert(0, BACKPACK)


def use_collector():
    need_collector()
    sys.path.append(COLLECTOR)


def use_sync():
    """This repo's sync/: pull.py, fetch_book.py and the gRPC stubs."""
    sys.path.insert(0, os.path.join(TRADING, "sync"))


def use_collector_sync():
    """sync/server.py (the collector's end of the sync; the same sync/ folder)."""
    need_collector()
    sys.path.append(os.path.join(COLLECTOR_REPO, "sync"))

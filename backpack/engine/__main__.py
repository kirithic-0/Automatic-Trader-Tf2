"""python -m engine <screen> [args]   (from backpack/; or `python backpack/engine ...`)"""
import os
import sys

# `python backpack/engine` runs this file with engine/ as sys.path[0]; put
# backpack/ there instead so `import engine` resolves either way.
_BACKPACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKPACK not in sys.path:
    sys.path.insert(0, _BACKPACK)

from engine.screens import SCREENS  # noqa: E402


def main(argv):
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in SCREENS:
        print(__doc__.strip() + "\n\nscreens:")
        for name, mod in SCREENS.items():
            print(f"  {name:14} {mod.__doc__.strip().splitlines()[0]}")
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    return SCREENS[argv[0]].main(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

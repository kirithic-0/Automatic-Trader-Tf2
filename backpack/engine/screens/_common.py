"""Bits every screen shares: the --db/--no-cache/--qualities flags and label fitting."""
import argparse

from engine.panel import DEFAULT_DB, QUALITIES, QUALITY_NAME


def parser(doc):
    ap = argparse.ArgumentParser(description=doc.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--no-cache", action="store_true",
                    help="recompute every book read instead of using engine_cache/")
    ap.add_argument("--qualities", default=",".join(map(str, QUALITIES)),
                    help="comma quality ids, a subset of 6,11 (default both)")
    return ap


def qualities(args):
    qs = {int(x) for x in args.qualities.split(",") if x.strip()}
    bad = qs - set(QUALITIES)
    if bad:
        raise SystemExit(f"--qualities: {sorted(bad)} out of scope; the engine "
                         f"reads Unique+Strange only ({QUALITIES})")
    return qs


def quality_names(qs):
    return ", ".join(QUALITY_NAME.get(q, str(q)) for q in sorted(qs))


def fit(label, width):
    return label if len(label) <= width else label[:width - 3] + "..."

"""Analysis engine: the read side of the collection database (Unique + Strange only).

    from engine import load_panel, label_events, build_features
    panel = load_panel()                       # one row per snapshot, read rules applied
    frame = build_features(label_events(panel, horizons=(24,)))

The read rules live in engine.book and nowhere else. The db is opened
read-only; derived numbers are never written back to it (only to the
disposable engine_cache/). Screens: `python -m engine --help` from backpack/.
See docs/code-base.md ("Analysis engine").
"""
from engine.book import BookRead, read_book
from engine.events import label_col, label_events
from engine.features import FEATURES, build_features
from engine.panel import DEFAULT_DB, load_panel


def build_frame(db=DEFAULT_DB, pct=None, horizons=(24,), use_cache=True,
                cache_dir=None):
    """load_panel -> label_events -> build_features, the pipeline every screen runs."""
    from engine.events import STEP_PCT
    pct = STEP_PCT if pct is None else pct
    frame = label_events(load_panel(db, cache_dir=cache_dir, use_cache=use_cache), pct=pct,
                         horizons=horizons)
    return build_features(frame, pct=pct)


__all__ = ["BookRead", "read_book", "load_panel", "label_events", "label_col",
           "build_features", "build_frame", "FEATURES", "DEFAULT_DB"]

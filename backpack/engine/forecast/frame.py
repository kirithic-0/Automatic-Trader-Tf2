"""One table for everything downstream: the panel + features + labels, and the universe it trades."""
from engine.events import STEP_PCT, label_events
from engine.features import build_features
from engine.forecast.config import HORIZON, LEVEL, MAX_JUMP, MAX_SPREAD, TAKE_PROFIT, TARGET
from engine.forecast.features import (FORECAST_FEATURES, add_bot_features, add_level_features,
                                      add_reach_features)
from engine.forecast.labels import label_forecast
from engine.panel import DEFAULT_DB, SERIES, load_panel

# the BookRead fields read off the bids: blanked together when the bids are a misread
BID_READS = ["bid1", "bid5", "bid_med", "bid_disp", "gap2", "n_near_top", "top_bot", "bid1_fill"]


def drop_misreads(panel, max_jump=MAX_JUMP):
    """The panel with every bid read (BID_READS) blanked where the buy-order median is
    above max_jump x the ask -- the same pass's, else the series' last one. Those are thin
    books whose few bids are mostly a misread (a 9-key bid on a 0.03-key hat): the median,
    and so the fence, keeps it (data-model.md, "The misread guard"). The book rules are
    unchanged; the forecast just never reads these as prices -- not as a level, a
    take-profit, a horizon exit, nor in any feature (a misread in the 7-day high of the
    best bid made a thin book look like a sure rise)."""
    df = panel.copy()
    ask = df.groupby(SERIES, sort=False, dropna=False).ask1.ffill()
    bad = (df.bid_med > max_jump * ask).to_numpy()
    for c in BID_READS:
        if c in df:
            df[c] = df[c].astype(float).mask(bad)
    df.attrs["n_misread"] = int(bad.sum())
    return df


def forecast_frame(db=DEFAULT_DB, horizons=(HORIZON,), target=TARGET, take_profit=TAKE_PROFIT,
                   use_cache=True, cache_dir=None, panel=None, scope="admitted"):
    """The panel with misread bids blanked (drop_misreads) + the engine FEATURES +
    LEVEL_FEATURES + REACH_FEATURES + bot features + labels. `panel`: a load_panel(db) the
    caller already has (engine.stats), else it is loaded. `scope`: panel.SCOPES."""
    if panel is None:
        panel = load_panel(db, cache_dir=cache_dir, use_cache=use_cache, scope=scope)
    panel = drop_misreads(panel)
    frame = build_features(label_events(panel, pct=STEP_PCT, horizons=(24,)), pct=STEP_PCT)
    frame = add_level_features(frame)
    frame = add_reach_features(frame, take_profit=take_profit)
    frame = add_bot_features(frame, db=db, cache_dir=cache_dir, use_cache=use_cache, scope=scope)
    frame = label_forecast(frame, horizons, target, take_profit)
    return frame.sort_values(["item", "quality", "variant", "t"]).reset_index(drop=True)


def feature_list(frame):
    """The model's features (FORECAST_FEATURES)."""
    return list(FORECAST_FEATURES)


def universe(frame, max_spread=MAX_SPREAD):
    """Rows whose ask is only a little above the bids (see module doc)."""
    return (frame.bid1.notna() & frame.ask1.notna() & frame[LEVEL].notna()
            & (frame.spread >= -1e-9) & (frame.spread <= max_spread))

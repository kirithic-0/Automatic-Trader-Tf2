"""The screens: thin views over the engine, one module each, `main(argv)`."""
from engine.screens import (bot_groups, bots, climbers, dashboard, events, exits, forecast, gainers,
                            history, listings, movers, panel, picks, ratchet_watch, recheck, replay, signal_check,
                            timeseries, untrusted)

SCREENS = {
    "dashboard": dashboard,
    "panel": panel,
    "movers": movers,
    "gainers": gainers,
    "climbers": climbers,
    "timeseries": timeseries,
    "history": history,
    "events": events,
    "signal-check": signal_check,
    "ratchet-watch": ratchet_watch,
    "forecast": forecast,
    "picks": picks,
    "listings": listings,
    "recheck": recheck,
    "exits": exits,
    "replay": replay,
    "bots": bots,
    "bot-groups": bot_groups,
    "untrusted": untrusted,
}

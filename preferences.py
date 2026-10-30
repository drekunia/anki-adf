"""Local preferences; note-pair decisions and history belong to one profile."""
from aqt import mw
from .scan_history import unique_history


def settings():
    return mw.addonManager.getConfig(__name__) or {}


def save_settings(data):
    mw.addonManager.writeConfig(__name__, data)


def profile_settings():
    return settings().get("profiles", {}).get(mw.pm.name, {})


def save_profile(data):
    config = settings()
    config.setdefault("profiles", {})[mw.pm.name] = data
    save_settings(config)


def ignored_pairs():
    return profile_settings().get("ignored_pairs", [])


def ignore_pairs(pairs):
    data = profile_settings()
    saved = {tuple(sorted(pair)) for pair in data.get("ignored_pairs", [])}
    saved.update(tuple(sorted(pair)) for pair in pairs)
    data["ignored_pairs"] = [list(pair) for pair in sorted(saved)]
    save_profile(data)


def clear_ignored():
    data = profile_settings()
    data["ignored_pairs"] = []
    save_profile(data)


def recent_scans():
    data = profile_settings()
    old = data.get('history', [])
    history = unique_history(old)
    if history != old:
        data['history'] = history
        save_profile(data)
    return history


def save_recent_scan(entry):
    data = profile_settings()
    data['history'] = unique_history(data.get('history', []), newest=entry)
    save_profile(data)

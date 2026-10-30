"""Keep one recent summary per comparison and selected-note population."""
import json

from .config_model import canonical_config


def history_key(entry):
    config = canonical_config(entry['config'])
    # Hidden deck selections have no effect outside Selected decks mode.
    if config['operation'] != 'decks':
        config['decks'] = []
    selection = (sorted(set(entry.get('selected_ids', [])))
                 if config['operation'] in ('within', 'against') else [])
    return json.dumps([config, selection], sort_keys=True, ensure_ascii=False)


def unique_history(entries, newest=None, limit=20):
    """Retain the latest summary; a repeated scan moves its entry to the top."""
    ordered = sorted(entries, key=lambda entry: entry.get('time', ''), reverse=True)
    if newest is not None:
        ordered.insert(0, newest)
    seen, result = set(), []
    for entry in ordered:
        key = history_key(entry)
        if key not in seen:
            seen.add(key)
            result.append(entry)
            if len(result) >= limit:
                break
    return result

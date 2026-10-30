"""Read-only collection access for scans and configuration previews."""

from .duplicate_core import ScanCancelled, analyze, normalize
import copy
from datetime import datetime, timezone


def quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def source_query(config, source):
    terms = []
    if config.get("deck"):
        terms.append("deck:" + quoted(config["deck"]))
    decks = config.get("deck_filter") or (config.get("decks") if config.get("operation") == "decks" else None)
    if decks:
        terms.append("(" + " or ".join("deck:" + quoted(deck) for deck in decks) + ")")
    if source.get("deck"):
        terms.append("deck:" + quoted(source["deck"]))
    if source.get("model"):
        terms.append("note:" + quoted(source["model"]))
    if config.get("exclude_inactive"):
        terms.extend(["-is:suspended", "-is:buried"])
    return " ".join(terms)


def scan_collection(col, config, selected_ids=(), cancel=None, progress=None, preview=False,
                    snapshot_out=None, records=None):
    def check():
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()

    selected = set(selected_ids)
    operation = config.get("operation", "collection")
    if records is None:
        memberships, counts, samples = {}, [], []
        excluded = set()
        for key, query in (("exclude_suspended", "is:suspended"), ("exclude_buried", "is:buried")):
            if config.get(key):
                excluded.update(col.find_notes(query))
        for index, source in enumerate(config["sources"]):
            check()
            ids = set(col.find_notes(source_query(config, source)))
            ids -= excluded
            if operation == "within":
                ids &= selected
            counts.append(len(ids))
            samples.append(None)
            for offset, nid in enumerate(ids):
                if offset % 1000 == 0:
                    check()
                memberships.setdefault(nid, {}).setdefault(source["field"], set()).add(index)
        notes, metadata = [], {}
        for index, (nid, scan_rows) in enumerate(memberships.items()):
            check()
            note = col.get_note(nid)
            model = note.note_type()
            fields = dict(note.items())
            scan_rows = {field: rows for field, rows in scan_rows.items() if field in fields}
            if not scan_rows:
                continue
            memberships[nid] = scan_rows
            record = {"noteId": int(nid), "modelName": model["name"],
                      "fields": {name: {"value": value} for name, value in fields.items()},
                      "scan_fields": list(scan_rows), "scan_rows": scan_rows}
            notes.append(record)
            for field, rows in scan_rows.items():
                for row in rows:
                    if samples[row] is None and fields[field]:
                        samples[row] = (fields[field], normalize(fields[field], config.get("options")))
            if progress and index % 100 == 0:
                progress(f"Reading notes: {index + 1:,} / {len(memberships):,}")
    else:
        notes = list(records)
        metadata = {}
        memberships = {note['noteId']: note['scan_rows'] for note in notes}
        samples = [None] * len(config['sources'])
    if snapshot_out is not None:
        snapshot_out.extend(notes)
    statistics = []
    for i, source in enumerate(config["sources"]):
        participating = [n for n in notes if i in n["scan_rows"].get(source["field"], set())]
        values = [normalize(n["fields"][source["field"]]["value"], config.get("options")) for n in participating]
        nonempty = [value for value in values if value]
        statistics.append({"notes": len(participating), "nonempty": len(nonempty),
                           "unique": len(set(nonempty)), "samples": list(dict.fromkeys(nonempty))[:5]})
    if preview:
        return {"counts": [stat["notes"] for stat in statistics], "samples": samples,
                "statistics": statistics, "note_count": len(notes)}
    if progress:
        progress(f"Comparing values from {len(notes):,} notes…")
    fields = sorted({field for note in notes for field in note["scan_fields"]})
    matches = analyze(notes, fields, config["mode"], row_scoped=True,
                      options=config.get("options"),
                      seeds=selected if operation == "against" else None,
                      check_cancel=check, ignored=config.get("ignored_pairs")) if fields else []
    affected = {match[f"{side}_note_id"] for match in matches for side in ("left", "right")}
    for nid in affected:
        check()
        note = col.get_note(nid)
        deck_ids = {card.did for card in note.cards()}
        metadata[int(nid)] = {"decks": sorted(col.decks.name(did) for did in deck_ids),
                              "tags": list(getattr(note, "tags", [])), "fields": dict(note.items()),
                              "sources": sorted({row for rows in memberships[nid].values() for row in rows})}
    overlap = []
    for i, stat in enumerate(statistics):
        matched = {nid for nid, data in metadata.items() if i in data["sources"]}
        overlap.append({"notes": stat["notes"], "matched": len(matched),
                        "percent": len(matched) * 100 / stat["notes"] if stat["notes"] else 0})
    return {"note_count": len(notes), "matches": matches, "fields": fields, "metadata": metadata,
            "config": copy.deepcopy(config), "statistics": statistics, "overlap": overlap,
            "scanned_at": datetime.now(timezone.utc).isoformat()}

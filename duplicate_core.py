"""Duplicate detection shared by the add-on UI (no AnkiConnect needed)."""

import csv
import re
import unicodedata
from collections import defaultdict
from html.parser import HTMLParser
from itertools import chain, combinations, product


class PlainJapanese(HTMLParser):
    """Extract visible text, ignoring HTML ruby readings."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"rt", "rp", "script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"rt", "rp", "script", "style"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


DEFAULT_OPTIONS = {"unicode": True, "html": True, "whitespace": True}


class ScanCancelled(Exception):
    pass


def normalize(value, options=None):
    options = DEFAULT_OPTIONS if options is None else options
    result = str(value or "")
    if options.get("html", True):
        parser = PlainJapanese()
        parser.feed(result)
        result = "".join(parser.parts)
    if options.get("unicode", True):
        result = unicodedata.normalize("NFC", result)
        result = result.replace("\u200b", "").replace("\ufeff", "")
    if options.get("whitespace", True):
        result = re.sub(r"\s+", " ", result).strip()
    return result


def get_field(note, name, options=None):
    return normalize(note.get("fields", {}).get(name, {}).get("value", ""), options)


KNOWN_FIELD_INFO = {
    "Expression": ("Expression", "ExpressionReading"),
    "ExpressionReading": ("Expression", "ExpressionReading"),
    "Word": ("Word", "WordReading"),
    "WordReading": ("Word", "WordReading"),
}


def analyze(notes, fields, scope="all", row_scoped=False, options=None, seeds=None, check_cancel=None, ignored=None):
    """Find shared values in distinct notes, across or within selected fields."""
    if scope not in ("cross", "all"):
        raise ValueError("scope must be 'cross' or 'all'")
    if scope == "cross" and not row_scoped and len(set(fields)) != 2:
        raise ValueError("Cross-field comparison requires two different fields.")
    if not fields:
        raise ValueError("Choose at least one field.")

    index = defaultdict(list)
    ignored = {tuple(sorted(pair)) for pair in (ignored or [])}
    for note in notes:
        if check_cancel:
            check_cancel()
        model = note.get("modelName", "")
        for field in note.get("scan_fields", fields):
            spelling_field, reading_field = KNOWN_FIELD_INFO.get(field, (None, None))
            value = get_field(note, field, options)
            if not value:
                continue
            index[value].append(
                {
                    "note_id": note["noteId"],
                    "model": model,
                    "field": field,
                    "raw": str(note.get("fields", {}).get(field, {}).get("value", "")),
                    "rows": note.get("scan_rows", {}).get(field, set()),
                    "spelling": get_field(note, spelling_field, options) if spelling_field else "",
                    "reading": get_field(note, reading_field, options) if reading_field else "",
                }
            )

    results = []
    for value, entries in sorted(index.items()):
        entries.sort(key=lambda entry: (entry["note_id"], entry["field"]))
        unique_pairs = {}
        comparisons = combinations(entries, 2)
        if seeds is not None:
            # Avoid generating all collection pairs when only selected notes matter.
            selected_entries = [e for e in entries if e["note_id"] in seeds]
            other_entries = [e for e in entries if e["note_id"] not in seeds]
            comparisons = chain(combinations(selected_entries, 2), product(selected_entries, other_entries))
        for pair_index, (left, right) in enumerate(comparisons):
            if check_cancel and pair_index % 1024 == 0:
                check_cancel()
            if left["note_id"] > right["note_id"]:
                left, right = right, left
            if left["note_id"] == right["note_id"]:
                continue
            if (left["note_id"], right["note_id"]) in ignored:
                continue
            if scope == "cross":
                if row_scoped:
                    if not any(a != b for a in left["rows"] for b in right["rows"]):
                        continue
                elif left["field"] == right["field"]:
                    continue

            if left["field"] in ("Expression", "Word") and right["field"] in ("Expression", "Word"):
                other, mismatch_status = "reading", "reading-mismatch"
            elif left["field"] in ("ExpressionReading", "WordReading") and right["field"] in ("ExpressionReading", "WordReading"):
                other, mismatch_status = "spelling", "spelling-mismatch"
            else:
                other, mismatch_status = None, None
            if other and left[other] and right[other] and left[other] != right[other]:
                status = mismatch_status
            elif other == "reading" and (not left[other] or not right[other]):
                status = "missing-reading"
            elif left["raw"] != right["raw"]:
                status = "normalized-match"
            else:
                status = "match"

            record = {
                "value": value,
                "left_note_id": left["note_id"],
                "left_model": left["model"],
                "left_field": left["field"],
                "left_fields": {left["field"]},
                "left_spelling": left["spelling"],
                "left_reading": left["reading"],
                "right_note_id": right["note_id"],
                "right_model": right["model"],
                "right_field": right["field"],
                "right_fields": {right["field"]},
                "right_spelling": right["spelling"],
                "right_reading": right["reading"],
                "status": status,
                "left_sources": set(left["rows"]),
                "right_sources": set(right["rows"]),
            }
            ids = (left["note_id"], right["note_id"], status)
            previous = unique_pairs.get(ids)
            if previous is None:
                unique_pairs[ids] = record
            elif previous["status"] == status:
                previous["left_fields"].add(left["field"])
                previous["right_fields"].add(right["field"])
                previous["left_sources"].update(left["rows"])
                previous["right_sources"].update(right["rows"])

        for record in unique_pairs.values():
            record["left_fields"] = ",".join(sorted(record["left_fields"]))
            record["right_fields"] = ",".join(sorted(record["right_fields"]))
            results.append(record)

    results.sort(key=lambda row: (row["left_note_id"], row["right_note_id"], row["value"]))
    return results


REPORT_COLUMNS = (
    "value", "status", "left_note_id", "left_model", "left_field",
    "left_fields", "left_spelling", "left_reading", "right_note_id",
    "right_model", "right_field", "right_fields", "right_spelling", "right_reading",
)


def save_report(path, matches):
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(matches)


def tag_targets(matches, include_reading=False, include_spelling=False, only_field=None,
                include_missing=False, only_source=None):
    """Calculate which notes to tag. No mutation occurs here."""
    statuses = {"match", "normalized-match"}
    if include_reading:
        statuses.add("reading-mismatch")
    if include_spelling:
        statuses.add("spelling-mismatch")
    if include_missing:
        statuses.add("missing-reading")
    targets = set()
    for match in matches:
        if match["status"] not in statuses:
            continue
        for side in ("left", "right"):
            if only_source is not None and only_source not in match.get(f"{side}_sources", set()):
                continue
            if only_field is None or only_field in match[f"{side}_fields"].split(","):
                targets.add(match[f"{side}_note_id"])
    return sorted(targets)


def group_matches(matches):
    """Group pair records into values, retaining every distinct participating note."""
    groups = {}
    for match in matches:
        group = groups.setdefault(match["value"], {"value": match["value"], "notes": {}, "matches": [], "statuses": set()})
        group["matches"].append(match)
        group["statuses"].add(match["status"])
        for side in ("left", "right"):
            nid = match[f"{side}_note_id"]
            note = group["notes"].setdefault(nid, {"id": nid, "model": match[f"{side}_model"], "fields": set(), "spelling": set(), "reading": set()})
            note["fields"].update(match[f"{side}_fields"].split(","))
            for attr in ("spelling", "reading"):
                if match[f"{side}_{attr}"]:
                    note[attr].add(match[f"{side}_{attr}"])
    return list(groups.values())

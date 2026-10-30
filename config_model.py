"""Canonical comparison settings, including migration of older scope options."""


def canonical_config(value):
    operation = value.get("operation", "collection")
    decks = value.get("decks", [value["deck"]] if value.get("deck") else [])
    if value.get("deck") and operation == "collection":
        operation = "decks"
    return {"operation": operation, "decks": sorted(decks), "mode": value.get("mode", "all"),
            "sources": [{"model": s.get("model"), "deck": s.get("deck"), "field": s.get("field", "")}
                        for s in value.get("sources", [])],
            "options": {key: value.get("options", {}).get(key, True) for key in ("unicode", "html", "whitespace")},
            "exclude_suspended": value.get("exclude_suspended", value.get("exclude_inactive", False)),
            "exclude_buried": value.get("exclude_buried", value.get("exclude_inactive", False))}

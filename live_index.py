"""One cached normalized-value index for Add-window warnings.

The UI invalidates this on collection changes other than the newly added note.
After initial indexing, only the added note and its value buckets are inspected.
"""
import copy
import json
from collections import Counter, defaultdict

from .duplicate_core import normalize
from .scan_service import scan_collection, source_query


class Snapshot:
    def __init__(self, config, notes):
        self.config = config
        self.notes = {}
        self.buckets = defaultdict(set)
        self.values = [Counter() for _ in config['sources']]
        self.counts = [0 for _ in config['sources']]
        for note in notes:
            self.put(note)

    def normalized(self, note):
        return {field: normalize(note['fields'][field]['value'], self.config.get('options'))
                for field in note['scan_fields']}

    def remove(self, nid):
        note = self.notes.pop(nid, None)
        if note is None:
            return
        for field, value in self.normalized(note).items():
            if value:
                self.buckets[value].discard(nid)
                if not self.buckets[value]:
                    del self.buckets[value]
            for row in note['scan_rows'][field]:
                self.counts[row] -= 1
                if value:
                    self.values[row][value] -= 1
                    if not self.values[row][value]:
                        del self.values[row][value]

    def put(self, note):
        nid = note['noteId']
        self.remove(nid)
        self.notes[nid] = note
        for field, value in self.normalized(note).items():
            if value:
                self.buckets[value].add(nid)
            for row in note['scan_rows'][field]:
                self.counts[row] += 1
                if value:
                    self.values[row][value] += 1

    def statistics(self):
        return [{'notes': self.counts[i], 'nonempty': sum(counter.values()),
                 'unique': len(counter), 'samples': list(counter)[:5]}
                for i, counter in enumerate(self.values)]


class LiveIndex:
    def __init__(self):
        self.generation = 0
        self.key = None
        self.snapshot = None

    def invalidate(self):
        self.generation += 1
        self.key = self.snapshot = None

    def scan(self, col, config, nid):
        key = (id(col), json.dumps(config, sort_keys=True))
        generation = self.generation
        snapshot = self.snapshot if key == self.key else None
        if snapshot is None:
            notes = []
            initial = copy.deepcopy(config)
            initial['operation'] = 'collection'
            scan_collection(col, initial, preview=True, snapshot_out=notes)
            snapshot = Snapshot(config, notes)
        note = col.get_note(nid)
        fields, rows = dict(note.items()), {}
        excluded = any(config.get(key) and col.find_notes(f'{query} nid:{nid}')
                       for key, query in [('exclude_suspended', 'is:suspended'), ('exclude_buried', 'is:buried')])
        if not excluded:
            for i, source in enumerate(config['sources']):
                query = source_query(config, source)
                if source['field'] in fields and col.find_notes(f'{query} nid:{nid}'):
                    rows.setdefault(source['field'], set()).add(i)
        snapshot.remove(nid)
        if rows:
            snapshot.put({'noteId': int(nid), 'modelName': note.note_type()['name'],
                          'fields': {name: {'value': value} for name, value in fields.items()},
                          'scan_fields': list(rows), 'scan_rows': rows})
        candidates = set()
        if nid in snapshot.notes:
            for value in snapshot.normalized(snapshot.notes[nid]).values():
                candidates.update(snapshot.buckets.get(value, ()))
        result = scan_collection(col, config, [nid], records=[snapshot.notes[i] for i in sorted(candidates)])
        result['note_count'] = len(snapshot.notes)
        result['statistics'] = snapshot.statistics()
        result['overlap'] = [{'notes': stat['notes'],
                              'matched': sum(i in meta['sources'] for meta in result['metadata'].values()),
                              'percent': 100 * sum(i in meta['sources'] for meta in result['metadata'].values()) / stat['notes'] if stat['notes'] else 0}
                             for i, stat in enumerate(result['statistics'])]
        if self.generation == generation:
            self.key, self.snapshot = key, snapshot
        return result

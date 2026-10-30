"""Regression checks without a running Anki installation."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
import tempfile
import csv
from threading import Event
from duplicate_core import analyze, group_matches, normalize, ScanCancelled, tag_targets, save_report

# Load the service under a test-only package without executing the Anki UI entrypoint.
package = types.ModuleType('_checker_tests')
package.__path__ = [str(Path(__file__).parent)]
sys.modules['_checker_tests'] = package
import duplicate_core
sys.modules['_checker_tests.duplicate_core'] = duplicate_core
spec = importlib.util.spec_from_file_location('_checker_tests.scan_service', Path(__file__).with_name('scan_service.py'))
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
sys.modules['_checker_tests.scan_service'] = service
live_spec = importlib.util.spec_from_file_location('_checker_tests.live_index', Path(__file__).with_name('live_index.py'))
live_service = importlib.util.module_from_spec(live_spec)
live_spec.loader.exec_module(live_service)

class Note:
    def __init__(self, nid, fields, model, did):
        self.id, self.fields, self.model, self.did = nid, fields, model, did
        self.tags = []
    def note_type(self):
        return {'name': self.model}
    def items(self):
        return self.fields.items()
    def cards(self):
        return [types.SimpleNamespace(did=self.did)]

class Collection:
    def __init__(self):
        self.notes = {
            1: Note(1, {'Expression': 'same', 'ExpressionReading': 'a'}, 'Kiku', 1),
            2: Note(2, {'Expression': 'same', 'ExpressionReading': 'b'}, 'Kiku', 1),
            3: Note(3, {'Word': 'same', 'WordReading': 'a'}, 'Kaishi', 2),
            4: Note(4, {'Word': 'same', 'WordReading': 'a'}, 'Kaishi', 2),
        }
        self.decks = types.SimpleNamespace(name=lambda did: {1: 'A', 2: 'B'}[did])
    def find_notes(self, query):
        notes = list(self.notes.values())
        if 'deck:"A"' in query and ' or ' not in query: notes = [n for n in notes if n.did == 1]
        if 'deck:"B"' in query and ' or ' not in query: notes = [n for n in notes if n.did == 2]
        for name in ('Kiku', 'Kaishi'):
            if f'note:"{name}"' in query: notes = [n for n in notes if n.model == name]
        if '-is:suspended' in query: notes = [n for n in notes if n.id != 2]
        elif 'is:suspended' in query: notes = [n for n in notes if n.id == 2]
        if 'is:buried' in query: notes = [n for n in notes if n.id == 4]
        if 'nid:' in query:
            wanted = int(query.split('nid:')[-1].strip())
            notes = [n for n in notes if n.id == wanted]
        return [n.id for n in notes]
    def get_note(self, nid): return self.notes[nid]


def config(operation='collection', mode='cross'):
    return {'operation': operation, 'mode': mode, 'sources': [
        {'model': 'Kiku', 'field': 'Expression', 'deck': None},
        {'model': 'Kaishi', 'field': 'Word', 'deck': None}]}

class ScanTests(unittest.TestCase):
    def test_cross_excludes_within_source(self):
        result = service.scan_collection(Collection(), config())
        self.assertEqual(len(result['matches']), 4)
        self.assertTrue(all(m['left_note_id'] <= 2 and m['right_note_id'] >= 3 for m in result['matches']))

    def test_all_combinations_includes_within_source(self):
        self.assertEqual(len(service.scan_collection(Collection(), config(mode='all'))['matches']), 6)

    def test_selected_within_respects_manual_fields(self):
        result = service.scan_collection(Collection(), config('within'), [1, 3])
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['note_count'], 2)

    def test_selected_against_only_pairs_touching_selection(self):
        result = service.scan_collection(Collection(), config('against', 'all'), [1])
        self.assertEqual(len(result['matches']), 3)
        self.assertTrue(all(1 in (m['left_note_id'], m['right_note_id']) for m in result['matches']))

    def test_against_no_participating_selected_notes(self):
        self.assertEqual(service.scan_collection(Collection(), config('against'), [99])['matches'], [])

    def test_selected_against_keeps_selected_selected_pairs(self):
        self.assertEqual(len(service.scan_collection(Collection(), config('against'), [1, 3])['matches']), 3)

    def test_global_deck_intersects_source_filters(self):
        value = config()
        value['deck'] = 'A'
        self.assertEqual(service.scan_collection(Collection(), value)['matches'], [])

    def test_single_source_and_inactive(self):
        value = config(mode='all')
        value['sources'] = value['sources'][:1]
        self.assertEqual(len(service.scan_collection(Collection(), value)['matches']), 1)
        value['exclude_inactive'] = True
        self.assertEqual(service.scan_collection(Collection(), value)['matches'], [])

    def test_group_unique_notes_and_mismatch(self):
        result = service.scan_collection(Collection(), config(mode='all'))
        groups = group_matches(result['matches'])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]['notes']), 4)
        self.assertIn('reading-mismatch', groups[0]['statuses'])
        self.assertEqual(tag_targets(result['matches']), [1, 3, 4])
        self.assertEqual(tag_targets(result['matches'], include_reading=True), [1, 2, 3, 4])

    def test_preview_counts_and_normalization(self):
        result = service.scan_collection(Collection(), config(), preview=True)
        self.assertEqual(result['counts'], [2, 2])
        self.assertEqual(result['samples'][0], ('same', 'same'))

    def test_overlapping_sources_do_not_repeat_pair(self):
        value = config()
        value['sources'][1] = dict(value['sources'][0])
        self.assertEqual(len(service.scan_collection(Collection(), value)['matches']), 1)

    def test_cancel(self):
        cancel = Event()
        cancel.set()
        with self.assertRaises(ScanCancelled):
            service.scan_collection(Collection(), config(), cancel=cancel)

    def test_normalization_options(self):
        value = ' <ruby>食<rt>しょく</rt></ruby>  '
        self.assertEqual(normalize(value), '食')
        self.assertEqual(normalize(value, {'unicode': False, 'html': False, 'whitespace': False}), value)
        self.assertEqual(normalize('e\u0301'), 'é')

    def test_quotes(self):
        self.assertEqual(service.quoted('A"B\\C'), '"A\\"B\\\\C"')

class NewBehaviorTests(unittest.TestCase):
    def test_missing_reading_requires_opt_in(self):
        col = Collection()
        col.notes[3].fields['WordReading'] = ''
        value = config('within')
        result = service.scan_collection(col, value, [1, 3])
        self.assertEqual(result['matches'][0]['status'], 'missing-reading')
        self.assertEqual(tag_targets(result['matches']), [])
        self.assertEqual(tag_targets(result['matches'], include_missing=True), [1, 3])

    def test_normalized_match(self):
        col = Collection()
        col.notes[3].fields['Word'] = ' <b>same</b> '
        result = service.scan_collection(col, config('within'), [1, 3])
        self.assertEqual(result['matches'][0]['status'], 'normalized-match')
        self.assertEqual(tag_targets(result['matches']), [1, 3])

    def test_suspended_and_buried_are_note_exclusions(self):
        value = config(mode='all')
        value.update(exclude_suspended=True, exclude_buried=True)
        result = service.scan_collection(Collection(), value)
        self.assertEqual(result['note_count'], 2)
        self.assertEqual([(m['left_note_id'], m['right_note_id']) for m in result['matches']], [(1, 3)])

    def test_global_multiple_decks_union(self):
        value = config(operation='decks')
        value['decks'] = ['A', 'B']
        self.assertEqual(len(service.scan_collection(Collection(), value)['matches']), 4)

    def test_preview_nonempty_and_unique_counts(self):
        col = Collection()
        col.notes[2].fields['Expression'] = ''
        result = service.scan_collection(col, config(), preview=True)
        self.assertEqual(result['statistics'][0]['notes'], 2)
        self.assertEqual(result['statistics'][0]['nonempty'], 1)
        self.assertEqual(result['statistics'][0]['unique'], 1)

    def test_ignore_pair_is_symmetric_and_value_independent(self):
        value = config()
        value['ignored_pairs'] = [[3, 1]]
        pairs = {(m['left_note_id'], m['right_note_id']) for m in service.scan_collection(Collection(), value)['matches']}
        self.assertNotIn((1, 3), pairs)
        self.assertIn((1, 4), pairs)

    def test_source_target_uses_source_not_just_field_name(self):
        result = service.scan_collection(Collection(), config())
        self.assertEqual(tag_targets(result['matches'], only_source=1), [3, 4])
        self.assertEqual(tag_targets(result['matches'], only_source=0), [1])

    def test_csv_keeps_working_with_source_metadata(self):
        result = service.scan_collection(Collection(), config())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.csv'
            save_report(path, result['matches'])
            with path.open(encoding='utf-8-sig', newline='') as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 4)

    def test_metadata_preserves_note_fields_tags_and_all_decks(self):
        col = Collection()
        col.notes[1].cards = lambda: [types.SimpleNamespace(did=1), types.SimpleNamespace(did=2)]
        col.notes[1].tags = ['known']
        result = service.scan_collection(col, config())
        self.assertEqual(result['metadata'][1]['decks'], ['A', 'B'])
        self.assertEqual(result['metadata'][1]['tags'], ['known'])
        self.assertEqual(result['metadata'][1]['fields']['Expression'], 'same')

    def test_live_index_only_reads_new_note_and_matching_buckets(self):
        col = Collection()
        index = live_service.LiveIndex()
        value = config('against')
        index.scan(col, value, 1)
        original = col.get_note
        reads = []
        col.get_note = lambda nid: (reads.append(nid), original(nid))[1]
        col.notes[5] = Note(5, {'Expression': 'unique', 'ExpressionReading': 'u'}, 'Kiku', 1)
        result = index.scan(col, value, 5)
        self.assertEqual(result['matches'], [])
        self.assertEqual(set(reads), {5})
        self.assertEqual(result['note_count'], 5)

    def test_live_index_invalidation_reflects_edits(self):
        col = Collection()
        value, index = config('against'), live_service.LiveIndex()
        index.scan(col, value, 1)
        col.notes[3].fields['Word'] = 'changed'
        index.invalidate()
        result = index.scan(col, value, 1)
        self.assertFalse(any(m['right_note_id'] == 3 for m in result['matches']))

    def test_live_new_note_finds_existing_notes(self):
        col = Collection()
        value, index = config('against'), live_service.LiveIndex()
        index.scan(col, value, 1)
        col.notes[5] = Note(5, {'Expression': 'same', 'ExpressionReading': 'a'}, 'Kiku', 1)
        result = index.scan(col, value, 5)
        pairs = {(m['left_note_id'], m['right_note_id']) for m in result['matches']}
        self.assertEqual(pairs, {(3, 5), (4, 5)})


if __name__ == '__main__': unittest.main()

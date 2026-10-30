"""History identity and migration checks without Anki."""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest

root = Path(__file__).parent
package = types.ModuleType('_history_tests')
package.__path__ = [str(root)]
sys.modules['_history_tests'] = package
for name in ('config_model', 'scan_history'):
    spec = importlib.util.spec_from_file_location('_history_tests.' + name, root / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
unique_history = sys.modules['_history_tests.scan_history'].unique_history


def entry(time='2026-10-30T13:00:00+00:00', **config):
    value = {'operation': 'collection', 'mode': 'cross', 'sources': [
        {'model': 'Kiku', 'field': 'Expression'}, {'model': 'Kaishi', 'field': 'Word'}]}
    value.update(config)
    return {'time': time, 'groups': 1, 'notes': 1669, 'config': value}


class HistoryTests(unittest.TestCase):
    def test_existing_duplicates_keep_latest(self):
        old, latest = entry(), entry('2026-10-30T13:16:39+00:00')
        self.assertEqual(unique_history([old, latest, old]), [latest])

    def test_repeated_scan_refreshes_counts_and_moves_to_top(self):
        old = entry()
        other = entry('2026-10-30T13:10:00+00:00', mode='all')
        new = entry('2026-10-30T13:20:00+00:00')
        new.update(groups=3, notes=1700)
        self.assertEqual(unique_history([other, old], newest=new), [new, other])

    def test_different_configs_with_same_counts_are_preserved(self):
        a, b = entry(), entry(mode='all')
        self.assertEqual(len(unique_history([a, b])), 2)

    def test_defaults_and_legacy_decks_are_equivalent(self):
        a = entry(deck='Mining')
        b = entry(operation='decks', decks=['Mining'], exclude_suspended=False,
                  exclude_buried=False, options={'html': True, 'unicode': True, 'whitespace': True})
        self.assertEqual(len(unique_history([a, b])), 1)

    def test_hidden_deck_choices_do_not_create_duplicates(self):
        self.assertEqual(len(unique_history([entry(), entry(decks=['Unused'])])), 1)

    def test_deck_order_does_not_create_duplicates(self):
        a, b = entry(operation='decks', decks=['A', 'B']), entry(operation='decks', decks=['B', 'A'])
        self.assertEqual(len(unique_history([a, b])), 1)

    def test_different_selected_notes_remain_distinct(self):
        a, b = entry(operation='within'), entry(operation='within')
        a['selected_ids'], b['selected_ids'] = [1, 2], [1, 3]
        self.assertEqual(len(unique_history([a, b])), 2)
        b['selected_ids'] = [2, 1, 1]
        self.assertEqual(len(unique_history([a, b])), 1)

    def test_twenty_distinct_entries_not_twenty_raw_entries(self):
        entries = [entry(deck=str(i)) for i in range(25)]
        result = unique_history([entries[0]] * 30 + entries)
        self.assertEqual(len(result), 20)

    def test_input_is_not_mutated(self):
        values = [entry(), entry(decks=['Unused'])]
        saved = copy.deepcopy(values)
        unique_history(values)
        self.assertEqual(values, saved)


if __name__ == '__main__':
    unittest.main()

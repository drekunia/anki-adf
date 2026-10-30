import os, sys, types, copy, importlib.util
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
qt_dll = os.add_dll_directory(r'C:\Program Files\Anki\app_packages\PyQt6\Qt6\bin')
from PyQt6 import QtWidgets, QtGui, QtCore
app = QtWidgets.QApplication([])
QtGui.QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\segoeui.ttf')
app.setFont(QtGui.QFont('Segoe UI', 9))
root = Path.cwd()
sys.path.insert(0, str(root))
import test_scanning as fixture
col = fixture.Collection()
for note in col.notes.values(): note.tags = ['duplicate'] if note.id == 1 else []
col.models = types.SimpleNamespace(all=lambda: [
    {'name': 'Kiku', 'flds': [{'name': 'Expression'}, {'name': 'ExpressionReading'}]},
    {'name': 'Kaishi', 'flds': [{'name': 'Word'}, {'name': 'WordReading'}]}])
col.decks.all_names_and_ids = lambda: [types.SimpleNamespace(name='A'), types.SimpleNamespace(name='B')]
col.tags = types.SimpleNamespace(bulk_add=lambda ids, tag: [col.notes[nid].tags.append(tag) for nid in ids if tag not in col.notes[nid].tags])
configuration = {'last': fixture.config(), 'presets': {'Demo': fixture.config()}, 'default_preset': 'Demo'}
mw = QtWidgets.QMainWindow()
mw.col = col
mw.pm = types.SimpleNamespace(name='QA')
mw.addonManager = types.SimpleNamespace(getConfig=lambda _: copy.deepcopy(configuration),
    writeConfig=lambda _, data: (configuration.clear(), configuration.update(copy.deepcopy(data))))
mw.form = types.SimpleNamespace(menuTools=QtWidgets.QMenu(mw))
mw.garbage_collect_on_dialog_finish = lambda _: None
browser = QtWidgets.QMainWindow()
browser.editor = None
browser.selected_notes = lambda: [1, 3]
browser.searches = []
browser.search_for = browser.searches.append
messages = []
aqt = types.ModuleType('aqt')
aqt.__path__ = []
aqt.mw = mw
aqt.dialogs = types.SimpleNamespace(open=lambda name, _: browser)
aqt.gui_hooks = types.SimpleNamespace(profile_will_close=[], browser_menus_did_init=[], add_cards_did_add_note=[], operation_did_execute=[])
qt = types.ModuleType('aqt.qt')
for module in (QtWidgets, QtGui, QtCore):
    for name in dir(module): setattr(qt, name, getattr(module, name))
utils = types.ModuleType('aqt.utils')
utils.qconnect = lambda signal, callback: signal.connect(callback)
utils.askUser = lambda *args, **kwargs: True
utils.showInfo = lambda *args, **kwargs: messages.append(args)
utils.showWarning = lambda *args, **kwargs: messages.append(args)
utils.tooltip = lambda *args, **kwargs: None
class QueryOp:
    def __init__(self, parent=None, op=None, success=None): self.op, self.callback, self.error = op, success, None
    def success(self, callback): self.callback=callback; return self
    def failure(self, callback): self.error=callback; return self
    def run_in_background(self):
        try: result = self.op(col)
        except Exception as exc:
            if self.error: self.error(exc)
            else: raise
        else:
            if self.callback: self.callback(result)
operations = types.ModuleType('aqt.operations')
operations.QueryOp = QueryOp
operations.CollectionOp = QueryOp
sys.modules.update({'aqt': aqt, 'aqt.qt': qt, 'aqt.utils': utils, 'aqt.operations': operations})
spec = importlib.util.spec_from_file_location('adfqa', root / '__init__.py', submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules['adfqa'] = addon
spec.loader.exec_module(addon)
# Untouched all-combinations defaults use one row; saved or added rows survive.
fresh = addon.CheckerDialog(browser, initial_config={})
assert fresh.mode.currentData() == 'all'
assert len(fresh.active_rows()) == 1
fresh.mode.setCurrentIndex(fresh.mode.findData('cross'))
assert len(fresh.active_rows()) == 2
fresh.mode.setCurrentIndex(fresh.mode.findData('all'))
assert len(fresh.active_rows()) == 1
fresh.add_source()
assert len(fresh.active_rows()) == 2
fresh.restore({'mode': 'all'})
assert len(fresh.active_rows()) == 1
fresh.restore(fixture.config(mode='all'))
assert len(fresh.active_rows()) == 2
fresh.mode.setCurrentIndex(fresh.mode.findData('cross'))
fresh.mode.setCurrentIndex(fresh.mode.findData('all'))
assert len(fresh.active_rows()) == 2
fresh.close()
checker = addon.CheckerDialog(browser)
assert checker.find.isEnabled(), checker.ready.text()
assert checker.preset_state.text() == 'Default', checker.preset_state.text()
assert not checker.preset_actions['delete'].isEnabled() is False
checker.preview()
assert 'unique values' in checker.rows[0].detail.text()
assert checker.rows[0].field.findText('Word') < 0
checker.rows[0].field.setEditText('ExpressionReading')
assert checker.preset_state.text() == 'Modified'
checker.save_current_preset()
assert checker.preset_state.text() != 'Modified'
checker.restore(fixture.config())
checker.operation.setCurrentIndex(checker.operation.findData('decks'))
assert not checker.find.isEnabled()
checker.scope_decks.item(0).setCheckState(QtCore.Qt.CheckState.Checked)
assert checker.find.isEnabled()
checker.mode.setCurrentIndex(1)
checker.add_source({'model': 'Kaishi', 'field': 'Word'})
assert len(checker.active_rows()) == 3
checker.remove_source(checker.rows[-1])
checker.restore(fixture.config())
checker.show()
app.processEvents()
checker.preview_timer.stop()
checker.preview()
checker.grab().save(str(root / '.qa' / 'main.png'))
result = addon.scan_collection(col, fixture.config(mode='all'))
from adfqa.results_ui import ResultsDialog
results = ResultsDialog(copy.deepcopy(result), browser)
results.show()
app.processEvents()
assert results.parentWidget() is browser
assert results.tree.columnCount() == 5 and results.notes.columnCount() == 7
assert len(results.note_views) == 2 and 'Expression' in results.note_views[0].toPlainText() or 'Word' in results.note_views[0].toPlainText()
assert results.tag.text() == ''
assert results.tag.placeholderText() == 'duplicate'
assert results.effective_tag() == 'duplicate'
results.tag.setText('custom')
assert results.effective_tag() == 'custom'
results.tag.clear()
results.set_checks(True)
assert results.tag_button.isEnabled()
assert results.targets() == {1, 3, 4}, results.targets()
assert '1 already tagged' in results.checked_label.text()
assert results.note_choices[0].currentText().split(' · ')[0] != results.note_choices[1].currentText().split(' · ')[0]
results.tags.setCurrentIndex(2)
assert results.targets() == {3, 4}
results.tags.setCurrentIndex(1)
assert results.targets() == {1}
results.tags.setCurrentIndex(0)
results.types.setCurrentIndex(2)
assert results.targets() == {3, 4}
results.types.setCurrentIndex(0)
assert not results.review_options['missing-reading'].isEnabled()
results.side.setCurrentIndex(1)
assert results.targets() == {1}
results.side.setCurrentIndex(2)
assert results.targets() == {3, 4}
results.side.setCurrentIndex(0)
first_note = results.notes.topLevelItem(0)
first_note.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
assert results.items[0].checkState(0) == QtCore.Qt.CheckState.PartiallyChecked
results.set_checks(True)
results.minimum.setValue(5)
assert not results.targets()
results.minimum.setValue(2)
results.group_clicked(results.items[0], 0)
assert browser.searches[-1] == 'nid:1,2,3,4'
results.grab().save(str(root / '.qa' / 'results.png'))
import adfqa.results_ui as result_module
notices = []
result_module.tooltip = lambda *args, **kwargs: notices.append(kwargs)
export_path = root / '.qa' / 'export-smoke.csv'
result_module.QFileDialog.getSaveFileName = lambda *args: (str(export_path), 'CSV files (*.csv)')
results.export()
assert export_path.exists()
assert results.export_status.isVisible()
assert not notices, 'CSV export must use inline feedback instead of a tooltip'
export_path.unlink()
results.tag_notes()
assert 'duplicate' in col.notes[3].tags
assert notices[-1]['parent'] is results
# Search again closes old results before opening settings, including when canceled.
real_checker = addon.CheckerDialog
captured = []
addon.CheckerDialog = lambda owner, initial_config=None: types.SimpleNamespace(
    exec=lambda: (captured.append((owner, initial_config)), 0)[1])
old_result = results.result
results.search_again()
assert results.result is old_result and not results.isVisible()
assert captured[-1] == (browser, old_result['config'])
addon.CheckerDialog = lambda owner, initial_config=None: types.SimpleNamespace(
    exec=lambda: 1, config=lambda: fixture.config('within'), selected_ids=lambda: [1, 3])
previous_window = results
previous_window.show()
results.search_again()
assert not previous_window.isVisible()
results = next(dialog for dialog in result_module._open_results if dialog.isVisible())
assert results is not previous_window and results.result is not old_result
assert set(results.groups[0]['notes']) == {1, 3}
assert not results.targets() and results.parentWidget() is browser
assert results.side.count() == 3 and not results.export_status.isVisible()
assert configuration['profiles']['QA']['history'][0]['config']['operation'] == 'within'
addon.CheckerDialog = real_checker
# Empty scans clear old notes, choices and detail panes without replacing the window.
scanned = results.result
empty = dict(scanned, matches=[], metadata={})
results.replace_result(empty)
assert not results.groups and results.notes.topLevelItemCount() == 0
assert all(choice.count() == 0 for choice in results.note_choices)
results.replace_result(scanned)
results.set_checks(True)
results.ignore_selected()
assert not results.groups
assert configuration['profiles']['QA']['ignored_pairs']
assert not messages, messages
results.close()
checker.close()
opened = []
addon.CheckerDialog = lambda owner, initial_config=None: types.SimpleNamespace(exec=lambda: (opened.append(owner), 0)[1])
addon.open_duplicate_checker()
assert opened == [browser]
changes = types.SimpleNamespace(note=True, card=True, deck=False, notetype=False)
addon._expected_add_change = True
previous_generation = addon._live_index.generation
addon._invalidate_live(changes, None)
assert addon._live_index.generation == previous_generation
addon._invalidate_live(changes, None)
assert addon._live_index.generation == previous_generation + 1
print('PASS: real Qt configuration, previews, preset updates, scope validation, source filtering, split details, tri-state checks, tagging counts/targets, Browser navigation, and ignore list', flush=True)

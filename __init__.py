"""Advanced Duplicate Finder: collection and Browser companion."""
import copy
from threading import Event
from aqt import gui_hooks, mw
from aqt.operations import QueryOp
from aqt.qt import (QAction, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
                    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QProgressDialog,
                    QListWidget, QListWidgetItem, QMenu, QToolButton,
                    QPushButton, QScrollArea, QTimer, QVBoxLayout, QWidget, Qt)
from aqt.utils import askUser, qconnect, showInfo, showWarning, tooltip
from .browser_window import browser_window
from .duplicate_core import ScanCancelled
from .scan_service import scan_collection
from .results_ui import show_results
from .preferences import settings, save_settings, recent_scans, save_recent_scan, ignored_pairs, clear_ignored
from .config_model import canonical_config
from .live_index import LiveIndex

_live_index = LiveIndex()
_expected_add_change = False


def searchable(combo):
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
    combo.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)


def set_data(combo, value):
    index = combo.findData(value)
    if index < 0 and combo.isEditable() and value is not None:
        combo.setCurrentIndex(-1)
        combo.setEditText(str(value))
    else:
        combo.setCurrentIndex(max(index, 0))


def combo_data(combo):
    """Resolve typed exact choices as well as clicked dropdown items."""
    index = combo.findText(combo.currentText())
    return combo.itemData(index) if index >= 0 else combo.currentText()


class SourceRow(QGroupBox):
    def __init__(self, owner, source=None):
        super().__init__(owner)
        self.owner = owner
        source = source or {}
        layout = QVBoxLayout(self)
        line = QHBoxLayout()
        self.model = QComboBox(self)
        self.model.addItem('Any note type', None)
        for name in owner.models:
            self.model.addItem(name, name)
        searchable(self.model)
        set_data(self.model, source.get('model'))
        self.deck = QComboBox(self)
        self.deck.addItem('Use search scope', None)
        for name in owner.decks:
            self.deck.addItem(name, name)
        searchable(self.deck)
        set_data(self.deck, source.get('deck'))
        for label, control in [('Note type:', self.model), ('Deck:', self.deck)]:
            line.addWidget(QLabel(label))
            line.addWidget(control, 1)
        layout.addLayout(line)
        line = QHBoxLayout()
        self.field = QComboBox(self)
        self.remove = QPushButton('Remove', self)
        line.addWidget(QLabel('Field:'))
        line.addWidget(self.field, 1)
        line.addWidget(self.remove)
        layout.addLayout(line)
        self.detail = QLabel(self)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.refresh_fields(source.get('field', ''))
        qconnect(self.model.currentTextChanged, self.model_changed)
        qconnect(self.deck.currentTextChanged, owner.changed)
        qconnect(self.field.currentTextChanged, owner.changed)
        qconnect(self.remove.clicked, lambda: owner.remove_source(self))

    def refresh_fields(self, text=''):
        self.field.blockSignals(True)
        self.field.clear()
        self.field.addItems(self.owner.models.get(combo_data(self.model), self.owner.all_fields))
        searchable(self.field)
        self.field.setCurrentIndex(-1)
        self.field.setEditText(text)
        self.field.lineEdit().setPlaceholderText('Type or choose a field')
        self.field.blockSignals(False)

    def model_changed(self, *_):
        text = self.field.currentText()
        names = self.owner.models.get(combo_data(self.model), self.owner.all_fields)
        self.refresh_fields(text if text in names else '')
        self.owner.changed()

    def value(self):
        return {'model': combo_data(self.model), 'deck': combo_data(self.deck),
                'field': self.field.currentText().strip()}

    def valid(self):
        names = self.owner.models.get(combo_data(self.model), self.owner.all_fields)
        return (self.model.findText(self.model.currentText()) >= 0
                and self.deck.findText(self.deck.currentText()) >= 0
                and self.field.currentText().strip() in names)


class CheckerDialog(QDialog):
    def __init__(self, browser, initial_config=None):
        super().__init__(browser)
        self.browser, self.rows, self.loading = browser, [], True
        self.preview_cancel, self.generation = None, 0
        self.models = {m['name']: [f['name'] for f in m['flds']]
                       for m in sorted(mw.col.models.all(), key=lambda m: m['name'].casefold())}
        self.all_fields = sorted({f for fields in self.models.values() for f in fields}, key=str.casefold)
        self.decks = sorted((d.name for d in mw.col.decks.all_names_and_ids()), key=str.casefold)
        self.setWindowTitle('Advanced Duplicate Finder')
        self.resize(*settings().get('main_size', [820, 720]))
        layout = QVBoxLayout(self)
        line = QHBoxLayout()
        self.preset = QComboBox(self)
        self.preset.addItem('Custom comparison', None)
        for name in settings().get('presets', {}):
            self.preset.addItem(name, name)
        save = QPushButton('Save', self)
        more = QToolButton(self)
        more.setText('⋯')
        menu = QMenu(more)
        more.setMenu(menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.preset_actions = {}
        for key, title, handler in [
            ('new', 'Save as new preset…', self.save_preset),
            ('update', 'Update current preset', self.save_current_preset),
            ('rename', 'Rename preset…', self.rename_preset),
            ('delete', 'Delete preset', self.delete_preset),
            ('default', 'Set as default', self.set_default_preset)]:
            action = menu.addAction(title)
            qconnect(action.triggered, handler)
            self.preset_actions[key] = action
        self.preset_state = QLabel(self)
        line.addWidget(QLabel('Preset:'))
        line.addWidget(self.preset, 1)
        line.addWidget(self.preset_state)
        line.addWidget(save)
        line.addWidget(more)
        layout.addLayout(line)
        history_line = QHBoxLayout()
        self.history = QComboBox(self)
        self.history.addItem('Recent scans (summaries only)', None)
        for item in recent_scans():
            self.history.addItem(f"{item['time'][:19]} UTC · {item['groups']} groups / {item['notes']} notes", item['config'])
        history_line.addWidget(self.history, 1)
        restore_history = QPushButton('Load settings to rerun', self)
        qconnect(restore_history.clicked, self.load_history)
        history_line.addWidget(restore_history)
        layout.addLayout(history_line)
        layout.addWidget(QLabel('1. Search scope'))
        self.operation = QComboBox(self)
        self.operation.addItem('Entire collection', 'collection')
        self.operation.addItem('Selected decks', 'decks')
        self.operation.addItem('Selected notes — compare with each other', 'within')
        self.operation.addItem('Selected notes — compare against collection', 'against')
        layout.addWidget(self.operation)
        self.scope_decks = QListWidget(self)
        self.scope_decks.setMaximumHeight(95)
        for name in self.decks:
            item = QListWidgetItem(name, self.scope_decks)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(self.scope_decks)
        self.scope_hint = QLabel(self)
        self.scope_hint.setWordWrap(True)
        layout.addWidget(self.scope_hint)
        layout.addWidget(QLabel('2. Comparison mode'))
        self.mode = QComboBox(self)
        self.mode.addItem('Two fields — Source A against Source B', 'cross')
        self.mode.addItem('All combinations', 'all')
        layout.addWidget(self.mode)
        layout.addWidget(QLabel('3. Fields to compare'))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(210)
        container = QWidget(scroll)
        self.sources_layout = QVBoxLayout(container)
        self.sources_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self.add = QPushButton('Add field', self)
        layout.addWidget(self.add)
        options = QGroupBox('Text normalization', self)
        option_layout = QVBoxLayout(options)
        line = QHBoxLayout()
        self.options = {}
        for key, label in [('unicode', 'Normalize Unicode'), ('html', 'Ignore HTML and ruby'),
                           ('whitespace', 'Normalize whitespace')]:
            box = QCheckBox(label, self)
            box.setChecked(True)
            box.setToolTip({'unicode': 'NFC normalization: combines equivalent Unicode sequences; does not fold full-width characters. Removes zero-width spaces and BOM.', 'html': 'Compare visible text after removing HTML, ruby readings (rt/rp), scripts and styles.', 'whitespace': 'Trim leading/trailing whitespace and collapse internal whitespace to a single space.'}[key])
            self.options[key] = box
            line.addWidget(box)
            qconnect(box.toggled, self.changed)
        option_layout.addLayout(line)
        layout.addWidget(options)
        filtering = QGroupBox('Note filtering', self)
        filter_layout = QVBoxLayout(filtering)
        self.exclude_suspended = QCheckBox('Exclude notes with suspended cards', self)
        self.exclude_buried = QCheckBox('Exclude notes with buried cards', self)
        for box in (self.exclude_suspended, self.exclude_buried):
            box.setToolTip('Exclude the entire note if ANY of its cards meet this condition, even when another sibling is active.')
            filter_layout.addWidget(box)
            qconnect(box.toggled, self.changed)
        ignore_line = QHBoxLayout()
        self.ignore_label = QLabel(self)
        reset_ignore = QPushButton('Reset ignored overlaps…', self)
        qconnect(reset_ignore.clicked, self.reset_ignored)
        ignore_line.addWidget(self.ignore_label, 1)
        ignore_line.addWidget(reset_ignore)
        filter_layout.addLayout(ignore_line)
        layout.addWidget(filtering)
        mining_line = QHBoxLayout()
        self.mining = QCheckBox('Warn after adding a note', self)
        self.mining.setToolTip('Checks the saved comparison below after saving in Anki’s Add window. Does not block adding.')
        self.mining.setChecked(settings().get('mining_warning', False))
        self.mining_preset = QComboBox(self)
        self.mining_preset.addItem('Use last comparison', None)
        for name in settings().get('presets', {}):
            self.mining_preset.addItem(name, name)
        set_data(self.mining_preset, settings().get('mining_preset'))
        mining_line.addWidget(self.mining)
        mining_line.addWidget(self.mining_preset, 1)
        layout.addLayout(mining_line)
        self.ready = QLabel(self)
        self.ready.setTextFormat(Qt.TextFormat.PlainText)
        self.ready.setWordWrap(True)
        layout.addWidget(self.ready)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.find = buttons.addButton('Find duplicates', QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(buttons)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(450)
        qconnect(self.preview_timer.timeout, self.preview)
        qconnect(buttons.accepted, self.accept)
        qconnect(buttons.rejected, self.reject)
        qconnect(self.add.clicked, lambda: self.add_source())
        qconnect(self.operation.currentIndexChanged, self.changed)
        qconnect(self.scope_decks.itemChanged, self.changed)
        qconnect(self.mode.currentIndexChanged, self.mode_changed)
        qconnect(self.preset.activated, self.load_preset)
        qconnect(save.clicked, self.save_current_preset)
        qconnect(self.finished, self.finished_dialog)
        initial = settings().get('default_preset') if initial_config is None else None
        set_data(self.preset, initial)
        self.restore(initial_config if initial_config is not None else
                     settings().get('presets', {}).get(initial, settings().get('last', {})))

    def active_rows(self):
        return self.rows[:2] if self.mode.currentData() == 'cross' else self.rows

    def config(self):
        return {'operation': self.operation.currentData(), 'decks': [self.scope_decks.item(i).text()
                for i in range(self.scope_decks.count()) if self.scope_decks.item(i).checkState() == Qt.CheckState.Checked],
                'mode': self.mode.currentData(), 'sources': [row.value() for row in self.active_rows()],
                'options': {key: box.isChecked() for key, box in self.options.items()},
                'exclude_suspended': self.exclude_suspended.isChecked(), 'exclude_buried': self.exclude_buried.isChecked()}

    def selected_ids(self):
        return list(set(self.browser.selected_notes())) if self.browser else []

    def add_source(self, source=None):
        row = SourceRow(self, source)
        self.rows.append(row)
        self.sources_layout.insertWidget(self.sources_layout.count() - 1, row)
        self.update_mode()

    def remove_source(self, row):
        if self.mode.currentData() == 'all' and len(self.rows) > 1:
            self.rows.remove(row)
            self.sources_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
            self.update_mode()

    def mode_changed(self, *_):
        if (not self.loading and self.mode.currentData() == 'all'
                and all(not any(row.value().values()) for row in self.rows)):
            # Untouched two-field defaults become one all-combinations field.
            # Explicit Add field clicks and restored searches keep their rows.
            while len(self.rows) > 1:
                row = self.rows.pop()
                self.sources_layout.removeWidget(row)
                row.hide()
                row.deleteLater()
        self.update_mode()

    def update_mode(self, *_):
        if not self.loading and self.mode.currentData() == 'cross' and len(self.rows) < 2:
            self.add_source()
        cross = self.mode.currentData() == 'cross'
        for i, row in enumerate(self.rows):
            row.setVisible(not cross or i < 2)
            row.setTitle(f'Source {chr(65 + i)}' if cross else f'Field {i + 1}')
            row.remove.setVisible(not cross)
            row.remove.setEnabled(len(self.rows) > 1)
        self.add.setVisible(not cross)
        self.changed()

    def changed(self, *_):
        if self.loading:
            return
        self.generation += 1
        if self.preview_cancel:
            self.preview_cancel.set()
        self.preview_timer.stop()
        operation, count = self.operation.currentData(), len(self.selected_ids())
        self.scope_decks.setVisible(operation == 'decks')
        self.ignore_label.setText(f'{len(ignored_pairs()):,} intentional note-pair overlaps ignored in this profile')
        self.scope_hint.setText('Every source filters within this scope. Decks include subdecks.' if operation in ('collection', 'decks') else
            f'{count:,} selected notes. ' + ('Compare within this selection.' if operation == 'within' else
            'Only pairs involving a selected note are returned. Scope and source filters apply to both sides.'))
        valid = (bool(self.active_rows()) and all(row.valid() for row in self.active_rows())
                 and (operation != 'decks' or bool(self.config()['decks'])))
        message = 'Choose a valid note type, deck and field for each source.'
        if operation in ('within', 'against') and count < (2 if operation == 'within' else 1):
            valid = False
            message = 'No notes selected in Browser.' if not count else 'Select at least two different notes.'
        name = self.preset.currentData()
        saved = settings().get('presets', {}).get(name)
        self.preset_state.setText('Modified' if saved and canonical_config(saved) != canonical_config(self.config()) else ('Default' if name and settings().get('default_preset') == name else ''))
        for key, action in self.preset_actions.items():
            action.setEnabled(bool(name) if key != 'new' else True)
        self.find.setEnabled(valid)
        for row in self.active_rows():
            row.detail.setText('Counting notes…' if valid else 'Choose a field to preview its normalized value.')
        if valid:
            descriptions = [f"{r.value()['model'] or 'Any note type'} → {r.value()['field']}" for r in self.active_rows()]
            self.ready.setText('Ready to compare: ' + ' ↔ '.join(descriptions))
            self.preview_timer.start()
        else:
            self.ready.setText(message)

    def preview(self):
        generation = self.generation
        self.preview_cancel = cancel = Event()
        config, ids = self.config(), self.selected_ids()
        def success(result):
            if cancel.is_set() or generation != self.generation:
                return
            for row, stat in zip(self.active_rows(), result['statistics']):
                row.detail.setText(f"{stat['notes']:,} matching notes · {stat['nonempty']:,} non-empty values · {stat['unique']:,} unique values")
                row.detail.setToolTip('Sample normalized values:\n' + '\n'.join(stat['samples']))
        def failure(exc):
            if not cancel.is_set() and generation == self.generation:
                self.ready.setText(f'Preview unavailable: {exc}')
        QueryOp(parent=self, op=lambda col: scan_collection(col, config, ids, cancel, preview=True),
                success=success).failure(failure).run_in_background()

    def restore(self, config):
        self.loading = True
        operation = config.get('operation', 'collection')
        if config.get('deck') and operation == 'collection': operation = 'decks'
        set_data(self.operation, operation)
        decks = config.get('decks', [config['deck']] if config.get('deck') else [])
        for i in range(self.scope_decks.count()):
            item = self.scope_decks.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in decks else Qt.CheckState.Unchecked)
        set_data(self.mode, config.get('mode', 'all'))
        for row in self.rows:
            self.sources_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        sources = config.get('sources') or ([{}, {}] if self.mode.currentData() == 'cross' else [{}])
        if self.mode.currentData() == 'cross' and len(sources) < 2:
            sources = sources + [{}]
        for source in sources:
            self.add_source(source)
        for key, box in self.options.items():
            box.setChecked(config.get('options', {}).get(key, True))
        self.exclude_suspended.setChecked(config.get('exclude_suspended', config.get('exclude_inactive', False)))
        self.exclude_buried.setChecked(config.get('exclude_buried', config.get('exclude_inactive', False)))
        self.loading = False
        self.update_mode()

    def save_preset(self):
        if not self.find.isEnabled():
            showInfo('Choose valid fields and scope before saving a preset.')
            return
        name, ok = QInputDialog.getText(self, 'Save comparison preset', 'Preset name:')
        name = name.strip()
        if not ok or not name:
            return
        data = settings()
        presets = data.setdefault('presets', {})
        if name in presets and not askUser(f"Replace preset '{name}'?", parent=self):
            return
        presets[name] = self.config()
        save_settings(data)
        if self.preset.findData(name) < 0:
            self.preset.addItem(name, name)
        set_data(self.preset, name)
        self.sync_mining_presets()
        self.changed()

    def load_preset(self, *_):
        value = settings().get('presets', {}).get(self.preset.currentData())
        if value:
            self.restore(value)

    def delete_preset(self):
        name = self.preset.currentData()
        if name and askUser(f"Delete preset '{name}'?", parent=self):
            data = settings()
            data.get('presets', {}).pop(name, None)
            save_settings(data)
            if data.get('default_preset') == name:
                data.pop('default_preset', None)
                save_settings(data)
            self.preset.removeItem(self.preset.currentIndex())
            self.sync_mining_presets()
            self.changed()

    def save_current_preset(self):
        name = self.preset.currentData()
        if not name:
            self.save_preset()
        elif self.find.isEnabled():
            data = settings()
            data['presets'][name] = self.config()
            save_settings(data)
            self.changed()

    def rename_preset(self):
        old = self.preset.currentData()
        if not old: return
        name, ok = QInputDialog.getText(self, 'Rename preset', 'New name:', text=old)
        name = name.strip()
        if not ok or not name or name == old: return
        data = settings()
        if name in data['presets']:
            showInfo('A preset with that name already exists.')
            return
        data['presets'][name] = data['presets'].pop(old)
        for key in ('default_preset', 'mining_preset'):
            if data.get(key) == old: data[key] = name
        save_settings(data)
        index = self.preset.currentIndex()
        self.preset.setItemText(index, name)
        self.preset.setItemData(index, name)
        self.sync_mining_presets()
        self.changed()

    def set_default_preset(self):
        if self.preset.currentData():
            data = settings()
            data['default_preset'] = self.preset.currentData()
            save_settings(data)
            self.changed()

    def sync_mining_presets(self):
        active = settings().get('mining_preset', self.mining_preset.currentData())
        self.mining_preset.clear()
        self.mining_preset.addItem('Use last comparison', None)
        for name in settings().get('presets', {}):
            self.mining_preset.addItem(name, name)
        set_data(self.mining_preset, active)

    def load_history(self):
        config = self.history.currentData()
        if config:
            set_data(self.preset, None)
            self.restore(config)

    def reset_ignored(self):
        if ignored_pairs() and askUser('Clear all ignored note-pair overlaps for this profile?', parent=self):
            clear_ignored()
            self.changed()

    def finished_dialog(self, *_):
        self.preview_timer.stop()
        if self.preview_cancel:
            self.preview_cancel.set()
        data = settings()
        data['last'] = self.config()
        data['mining_warning'] = self.mining.isChecked()
        data['mining_preset'] = self.mining_preset.currentData()
        data['main_size'] = [self.width(), self.height()]
        save_settings(data)


def run_scan(config, selected_ids, parent, on_result=None):
    config = copy.deepcopy(config)
    config['ignored_pairs'] = ignored_pairs()
    cancel, progress_text = Event(), ['Finding participating notes…']
    progress = QProgressDialog(progress_text[0], 'Cancel', 0, 0, parent)
    progress.setWindowTitle('Finding duplicates')
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    qconnect(progress.canceled, cancel.set)
    timer = QTimer(progress)
    qconnect(timer.timeout, lambda: progress.setLabelText(progress_text[0]))
    timer.start(150)
    def cleanup():
        timer.stop()
        progress.close()
        progress.deleteLater()
    def success(result):
        stopped = cancel.is_set()
        cleanup()
        if not stopped:
            from .duplicate_core import group_matches
            stored = {k: v for k, v in config.items() if k != 'ignored_pairs'}
            entry = {'time': result['scanned_at'], 'groups': len(group_matches(result['matches'])),
                     'notes': result['note_count'], 'config': stored}
            if config.get('operation') in ('within', 'against'):
                entry['selected_ids'] = sorted(set(selected_ids))
            save_recent_scan(entry)
            if on_result is not None:
                on_result(result)
            else:
                show_results(result, parent=parent)
    def failure(exc):
        cleanup()
        if not isinstance(exc, ScanCancelled):
            showWarning(f'Duplicate scan failed:\n{exc}')
    QueryOp(parent=parent, op=lambda col: scan_collection(col, config, selected_ids, cancel,
            lambda text: progress_text.__setitem__(0, text)), success=success).failure(failure).run_in_background()


def open_duplicate_checker(browser=None, initial_config=None, on_result=None):
    if mw.col is None:
        showWarning('Open an Anki profile before running Advanced Duplicate Finder.')
        return
    if browser is None:
        _open_from_browser(browser_window(), initial_config, on_result)
        return
    browser_window(browser)
    dialog = CheckerDialog(browser, initial_config=initial_config)
    if dialog.exec():
        run_scan(dialog.config(), dialog.selected_ids(), browser, on_result=on_result)


def _open_from_browser(browser, initial_config=None, on_result=None):
    if browser.editor is not None:
        browser.editor.call_after_note_saved(lambda: open_duplicate_checker(browser, initial_config, on_result))
    else:
        open_duplicate_checker(browser, initial_config, on_result)


def _setup_browser_menu(browser):
    action = QAction('Advanced Duplicate Finder', browser)
    qconnect(action.triggered, lambda: _open_from_browser(browser))
    browser.form.menu_Notes.addAction(action)


def _mining_note_added(note):
    global _expected_add_change
    data = settings()
    config = copy.deepcopy(data.get('presets', {}).get(data.get('mining_preset'), data.get('last', {})))
    if not data.get('mining_warning') or not config.get('sources'):
        return
    if not all(source.get('field') for source in config['sources']):
        return
    if config.get('operation') == 'decks':
        config['deck_filter'] = config.get('decks', [])
    config['operation'] = 'against'
    config['ignored_pairs'] = ignored_pairs()
    config['warning_preset'] = data.get('mining_preset') or 'Last comparison'
    _expected_add_change = True
    nid = int(note.id)
    def success(result):
        if result['matches']:
            tooltip(f"Possible duplicates found — {config['warning_preset']}.", period=5000)
            show_results(result)
    QueryOp(parent=mw, op=lambda col: _live_index.scan(col, config, nid), success=success).failure(
        lambda exc: showWarning(f'Mining duplicate check failed:\n{exc}')).run_in_background()


def _invalidate_live(changes, _handler):
    global _expected_add_change
    added = _expected_add_change
    _expected_add_change = False
    # Add-window success hooks run just before their operation-change hook.
    # The incremental check already incorporates that one added note.
    if added and changes.note and changes.card and not changes.deck and not changes.notetype:
        return
    if any(getattr(changes, key, False) for key in ('note', 'note_text', 'card', 'deck', 'notetype', 'tag', 'config')):
        _live_index.invalidate()


action = QAction('Advanced Duplicate Finder', mw)
qconnect(action.triggered, lambda: open_duplicate_checker())
mw.form.menuTools.addAction(action)
gui_hooks.browser_menus_did_init.append(_setup_browser_menu)
gui_hooks.add_cards_did_add_note.append(_mining_note_added)
gui_hooks.operation_did_execute.append(_invalidate_live)
gui_hooks.profile_will_close.append(_live_index.invalidate)
if hasattr(gui_hooks, 'sync_did_finish'):
    gui_hooks.sync_did_finish.append(lambda *_: _live_index.invalidate())

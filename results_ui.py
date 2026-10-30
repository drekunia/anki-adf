"""Grouped results, side-by-side note inspection, and explicit bulk actions."""
import html
import re
from datetime import datetime
import aqt
from aqt import gui_hooks, mw
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
                    QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QTextBrowser,
                    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, Qt)
from aqt.utils import askUser, qconnect, showInfo, showWarning, tooltip
from .duplicate_core import group_matches, normalize, save_report, tag_targets
from .browser_window import browser_window
from .preferences import settings, save_settings, ignore_pairs

STATUS = {'match': 'Exact text match', 'normalized-match': 'Normalized match',
          'reading-mismatch': 'Reading mismatch', 'spelling-mismatch': 'Spelling mismatch',
          'missing-reading': 'Missing reading'}
REVIEW = {'reading-mismatch', 'spelling-mismatch', 'missing-reading'}
ROLE = Qt.ItemDataRole.UserRole
_open_results = set()


def added(nid):
    try:
        return datetime.fromtimestamp(nid / 1000).astimezone().strftime('%Y-%m-%d %H:%M')
    except (ValueError, OSError, OverflowError):
        return str(nid)


class ResultItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        if column in (1, 4):
            return (self.data(column, ROLE) or 0) < (other.data(column, ROLE) or 0)
        return self.text(column).casefold() < other.text(column).casefold()


class ResultsTree(QTreeWidget):
    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        self.pressed_state = (item, item.checkState(0)) if item else None
        super().mousePressEvent(event)


class ResultsDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(browser_window(parent))
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.result, self.groups = result, group_matches(result['matches'])
        self.checked, self.items, self.current_group = {}, [], None
        self.updating = False
        self.setWindowTitle('Advanced Duplicate Finder — Results')
        self.resize(*settings().get('results_size', [1040, 720]))
        self.setMinimumSize(820, 540)
        layout = QVBoxLayout(self)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.stats = QLabel(self)
        self.stats.setWordWrap(True)
        layout.addWidget(self.stats)
        hint = QLabel('Click a group or note to show it in Browse. Checking a group selects its notes. '
                      'Details and tags are a scan snapshot; rerun after edits.', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        line = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('Filter value, note type, deck or field…')
        self.status = QComboBox(self)
        self.status.addItem('All statuses', None)
        self.status.addItem('Needs review', 'review')
        for value, label in STATUS.items(): self.status.addItem(label, value)
        self.types = QComboBox(self)
        self.types.addItem('All note types', None)
        self.types.addItem('Cross-note-type pairs only', 'cross')
        self.types.addItem('Same-note-type pairs only', 'same')
        line.addWidget(self.search, 2)
        line.addWidget(self.status, 1)
        line.addWidget(self.types, 1)
        layout.addLayout(line)
        line = QHBoxLayout()
        self.tags = QComboBox(self)
        for label, value in [('Any tag state', None), ('Only already-tagged notes', 'tagged'),
                             ('Only notes missing tag', 'untagged')]: self.tags.addItem(label, value)
        self.tags.setToolTip('Uses the tag entered under Bulk actions. Groups remain visible for context; tagging is restricted to notes matching this filter.')
        self.minimum = QSpinBox(self)
        self.minimum.setRange(2, 1000000)
        self.minimum.setValue(2)
        line.addWidget(self.tags)
        line.addWidget(QLabel('Minimum notes:'))
        line.addWidget(self.minimum)
        line.addStretch()
        self.sorting = QComboBox(self)
        for label, value in [('Newest first', (4, True)), ('Oldest first', (4, False)),
                             ('Most notes', (1, True)), ('Expression', (0, False)),
                             ('Note type', (3, False)), ('Match status', (2, False))]:
            self.sorting.addItem(label, value)
        line.addWidget(QLabel('Sort:'))
        line.addWidget(self.sorting)
        layout.addLayout(line)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.tree = ResultsTree(self)
        self.tree.setHeaderLabels(['Value', 'Notes', 'Status', 'Sources', 'Newest note'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 110)
        self.tree.setColumnWidth(1, 45)
        self.tree.setColumnWidth(2, 125)
        self.tree.setColumnWidth(3, 120)
        self.tree.setColumnHidden(4, True)
        self.splitter.addWidget(self.tree)
        detail = QWidget(self)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_title = QLabel('Select a duplicate group to inspect its notes.', self)
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_title.setWordWrap(True)
        detail_layout.addWidget(self.detail_title)
        self.notes = ResultsTree(self)
        self.notes.setHeaderLabels(['Note ID', 'Note type', 'Decks', 'Field', 'Spelling', 'Reading', 'Added'])
        self.notes.setAlternatingRowColors(True)
        self.notes.setMaximumHeight(110)
        detail_layout.addWidget(self.notes)
        self.compare = QSplitter(Qt.Orientation.Horizontal, self)
        self.note_choices, self.note_views = [], []
        for side in ('Left note', 'Right note'):
            panel = QWidget(self)
            side_layout = QVBoxLayout(panel)
            side_layout.setContentsMargins(0, 0, 0, 0)
            selector = QComboBox(self)
            selector.setToolTip(side + ': choose any note from the group')
            viewer = QTextBrowser(self)
            viewer.setOpenExternalLinks(False)
            viewer.setOpenLinks(False)
            side_layout.addWidget(selector)
            side_layout.addWidget(viewer, 1)
            self.note_choices.append(selector)
            self.note_views.append(viewer)
            self.compare.addWidget(panel)
            qconnect(selector.currentIndexChanged, self.render_details)
        detail_layout.addWidget(self.compare, 2)
        self.splitter.addWidget(detail)
        self.splitter.setSizes(settings().get('results_split', [400, 640]))
        layout.addWidget(self.splitter, 1)
        line = QHBoxLayout()
        check, clear = QPushButton('Select visible groups', self), QPushButton('Clear selection', self)
        ignore = QPushButton('Ignore selected overlaps…', self)
        line.addWidget(check)
        line.addWidget(clear)
        line.addWidget(ignore)
        line.addStretch()
        layout.addLayout(line)
        bulk = QGroupBox('Bulk actions — apply to checked notes in visible groups', self)
        bulk_layout = QVBoxLayout(bulk)
        line = QHBoxLayout()
        self.tag = QLineEdit(self)
        self.tag.setPlaceholderText('duplicate')
        self.tag.setToolTip('Leave empty to use the default tag: duplicate.')
        self.side = QComboBox(self)
        self.side.addItem('Both / all sources', None)
        for i, source in enumerate(result.get('config', {}).get('sources', [])):
            label = f"Source {chr(65 + i)}" if result.get('config', {}).get('mode') == 'cross' else f"Field {i + 1}"
            self.side.addItem(f"{label}: {source.get('model') or 'Any type'} → {source['field']}", i)
        self.tag_button = QPushButton('Tag 0 notes', self)
        line.addWidget(QLabel('Tag:'))
        line.addWidget(self.tag, 1)
        line.addWidget(QLabel('Target:'))
        line.addWidget(self.side, 2)
        line.addWidget(self.tag_button)
        bulk_layout.addLayout(line)
        line = QHBoxLayout()
        self.review_options = {}
        statuses = {match['status'] for match in result['matches']}
        for status, label in [('reading-mismatch', 'Include reading mismatches'),
                              ('spelling-mismatch', 'Include spelling mismatches'),
                              ('missing-reading', 'Include missing readings')]:
            box = QCheckBox(label, self)
            box.setEnabled(status in statuses)
            box.setToolTip('Disabled when this scan has no results in this category.')
            self.review_options[status] = box
            line.addWidget(box)
            qconnect(box.toggled, self.update_checked)
        bulk_layout.addLayout(line)
        self.checked_label = QLabel(self)
        self.checked_label.setWordWrap(True)
        bulk_layout.addWidget(self.checked_label)
        layout.addWidget(bulk)
        line = QHBoxLayout()
        search_again = QPushButton('Search again…', self)
        search_again.setToolTip('Close these results and reopen the search settings for another scan.')
        export = QPushButton('Export CSV…', self)
        close = QPushButton('Close', self)
        for button in (search_again, export, close): line.addWidget(button)
        layout.addLayout(line)
        self.export_status = QLabel(self)
        self.export_status.setWordWrap(True)
        self.export_status.hide()
        layout.addWidget(self.export_status)
        qconnect(self.tree.currentItemChanged, self.group_changed)
        qconnect(self.tree.itemClicked, self.group_clicked)
        qconnect(self.tree.itemChanged, self.group_checked)
        qconnect(self.notes.itemClicked, self.note_clicked)
        qconnect(self.notes.itemChanged, self.note_checked)
        for control, signal in [(self.search, 'textChanged'), (self.status, 'currentIndexChanged'),
                                (self.types, 'currentIndexChanged'), (self.tags, 'currentIndexChanged'),
                                (self.minimum, 'valueChanged')]:
            qconnect(getattr(control, signal), self.filter)
        qconnect(self.tag.textChanged, self.filter)
        qconnect(self.side.currentIndexChanged, self.update_checked)
        qconnect(self.sorting.currentIndexChanged, self.apply_sort)
        qconnect(check.clicked, lambda: self.set_checks(True))
        qconnect(clear.clicked, lambda: self.set_checks(False))
        qconnect(ignore.clicked, self.ignore_selected)
        qconnect(export.clicked, self.export)
        qconnect(search_again.clicked, self.search_again)
        qconnect(self.tag_button.clicked, self.tag_notes)
        qconnect(close.clicked, self.close)
        qconnect(self.finished, self.save_geometry)
        self.populate()

    def populate(self):
        self.updating = True
        self.tree.clear()
        self.items, self.checked, self.current_group = [], {}, None
        metadata = self.result.get('metadata', {})
        for index, group in enumerate(self.groups):
            item = ResultItem(self.tree, [group['value'], str(len(group['notes'])),
                ', '.join(STATUS[s] for s in sorted(group['statuses'])),
                ' ↔ '.join(sorted({note['model'] for note in group['notes'].values()})), added(max(group['notes']))])
            item.setData(0, ROLE, index)
            item.setData(1, ROLE, len(group['notes']))
            item.setData(4, ROLE, max(group['notes']))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setToolTip(0, group['value'])
            for column in range(1, 5):
                item.setToolTip(column, item.text(column))
            self.items.append(item)
        self.tree.setSortingEnabled(True)
        self.apply_sort()
        affected = {nid for group in self.groups for nid in group['notes']}
        review = sum(bool(group['statuses'] & REVIEW) for group in self.groups)
        self.summary.setText(f"{len(self.groups):,} groups · {len(affected):,} affected notes · {review:,} need review · {self.result['note_count']:,} notes scanned")
        overlap = self.result.get('overlap', [])
        self.stats.setText('Source overlap: ' + ' · '.join(f"{i+1}: {stat['matched']:,}/{stat['notes']:,} notes ({stat['percent']:.1f}%)" for i, stat in enumerate(overlap)) if overlap else '')
        if self.result.get('config', {}).get('warning_preset'):
            self.stats.setText('Mining preset: ' + self.result['config']['warning_preset'] + ' · ' + self.stats.text())
        if not self.groups:
            self.notes.clear()
            self.detail_title.setText('No duplicates found in this scan. Try a broader scope or different fields.')
            for viewer in self.note_views: viewer.clear()
            for selector in self.note_choices: selector.clear()
        self.updating = False
        self.filter()
        if self.items: self.tree.setCurrentItem(self.items[0])

    def search_again(self):
        from . import _open_from_browser
        browser, config = self.parentWidget(), self.result.get('config')
        self.close()
        _open_from_browser(browser, initial_config=config)

    def replace_result(self, result):
        if not self.isVisible():
            return
        self.updating = True
        self.result, self.groups = result, group_matches(result['matches'])
        self.search.clear()
        for combo in (self.status, self.types, self.tags):
            combo.setCurrentIndex(0)
        self.minimum.setValue(2)
        self.side.clear()
        self.side.addItem('Both / all sources', None)
        for i, source in enumerate(result.get('config', {}).get('sources', [])):
            label = f"Source {chr(65 + i)}" if result.get('config', {}).get('mode') == 'cross' else f"Field {i + 1}"
            self.side.addItem(f"{label}: {source.get('model') or 'Any type'} → {source['field']}", i)
        statuses = {match['status'] for match in result['matches']}
        for status, box in self.review_options.items():
            box.setChecked(False)
            box.setEnabled(status in statuses)
        self.export_status.hide()
        self.populate()
        self.raise_()
        self.activateWindow()

    def apply_sort(self, *_):
        column, descending = self.sorting.currentData()
        self.tree.sortItems(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)

    def group_changed(self, item, _previous):
        if self.updating or item is None: return
        self.current_group = item.data(0, ROLE)
        group = self.groups[self.current_group]
        self.detail_title.setText(f"{group['value']} · {len(group['notes'])} notes")
        self.updating = True
        self.notes.clear()
        for nid, note in sorted(group['notes'].items(), reverse=True):
            meta = self.result.get('metadata', {}).get(nid, {})
            row = QTreeWidgetItem(self.notes, [str(nid), note['model'], ', '.join(meta.get('decks', [])),
                ', '.join(sorted(note['fields'])), ', '.join(sorted(note['spelling'])),
                ', '.join(sorted(note['reading'])), added(nid)])
            row.setData(0, ROLE, nid)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked if nid in self.checked.get(self.current_group, set()) else Qt.CheckState.Unchecked)
            for col in range(7): row.setToolTip(col, row.text(col))
        for i, selector in enumerate(self.note_choices):
            selector.blockSignals(True)
            selector.clear()
            for nid, note in sorted(group['notes'].items(), reverse=True):
                selector.addItem(f"{note['model']} · #{nid}", nid)
            selected = min(i, selector.count()-1)
            if i == 1 and selector.count() > 1:
                first_model = group['notes'][selector.itemData(0)]['model']
                selected = next((j for j in range(1, selector.count())
                                 if group['notes'][selector.itemData(j)]['model'] != first_model), 1)
            selector.setCurrentIndex(selected)
            selector.blockSignals(False)
        self.updating = False
        self.render_details()

    def render_details(self, *_):
        for selector, viewer in zip(self.note_choices, self.note_views):
            nid = selector.currentData()
            if nid is None:
                viewer.clear()
                continue
            meta = self.result.get('metadata', {}).get(nid, {})
            fields = meta.get('fields', {})
            output = [f'<p><b>#{nid}</b> · Added {html.escape(added(nid))}</p>',
                      '<p>Decks: ' + html.escape(', '.join(meta.get('decks', []))) + '</p>',
                      '<p>Tags: ' + html.escape(' '.join(meta.get('tags', []))) + '</p>']
            for name, value in fields.items():
                plain = normalize(value, {'html': True, 'unicode': False, 'whitespace': False})
                output.append('<h4>' + html.escape(name) + '</h4><p>' + html.escape(plain).replace('\n', '<br>') + '</p>')
            viewer.setHtml(''.join(output))

    def effective_tag(self):
        return self.tag.text().strip() or 'duplicate'

    def filter(self, *_):
        if self.updating:
            return
        text, status, kind, tag_state = self.search.text().casefold(), self.status.currentData(), self.types.currentData(), self.tags.currentData()
        tag = self.effective_tag().casefold()
        metadata = self.result.get('metadata', {})
        for item, group in zip(self.items, self.groups):
            content = group['value'] + ' ' + ' '.join(note['model'] + ' ' + ' '.join(note['fields']) + ' ' +
                ' '.join(metadata.get(nid, {}).get('decks', [])) for nid, note in group['notes'].items())
            models = {note['model'] for note in group['notes'].values()}
            pair_kinds = {'same' if m['left_model'] == m['right_model'] else 'cross' for m in group['matches']}
            tags = [tag in {t.casefold() for t in metadata.get(nid, {}).get('tags', [])} for nid in group['notes']]
            match = (text in content.casefold() and len(group['notes']) >= self.minimum.value()
                     and (not status or status in group['statuses'] or status == 'review' and bool(group['statuses'] & REVIEW))
                     and (not kind or kind in pair_kinds)
                     and (not tag_state or tag_state == 'tagged' and any(tags) or tag_state == 'untagged' and not all(tags)))
            item.setHidden(not match)
        self.update_checked()

    def group_checked(self, item, column):
        if self.updating or column != 0: return
        index = item.data(0, ROLE)
        self.checked[index] = set(self.groups[index]['notes']) if item.checkState(0) == Qt.CheckState.Checked else set()
        self.sync_checks()

    def note_checked(self, item, column):
        if self.updating or column != 0 or self.current_group is None: return
        selected = self.checked.setdefault(self.current_group, set())
        nid = item.data(0, ROLE)
        if item.checkState(0) == Qt.CheckState.Checked: selected.add(nid)
        else: selected.discard(nid)
        self.sync_checks()

    def sync_checks(self):
        self.updating = True
        for index, item in enumerate(self.items):
            selected = self.checked.get(index, set())
            state = Qt.CheckState.Unchecked if not selected else (Qt.CheckState.Checked if len(selected) == len(self.groups[index]['notes']) else Qt.CheckState.PartiallyChecked)
            item.setCheckState(0, state)
        if self.current_group is not None:
            for i in range(self.notes.topLevelItemCount()):
                row = self.notes.topLevelItem(i)
                row.setCheckState(0, Qt.CheckState.Checked if row.data(0, ROLE) in self.checked.get(self.current_group, set()) else Qt.CheckState.Unchecked)
        self.updating = False
        self.update_checked()

    def set_checks(self, checked):
        for i, item in enumerate(self.items):
            if not checked or not item.isHidden(): self.checked[i] = set(self.groups[i]['notes']) if checked else set()
        self.sync_checks()

    def checked_groups(self):
        return [(self.groups[i], self.checked.get(i, set())) for i, item in enumerate(self.items)
                if not item.isHidden() and self.checked.get(i)]

    def targets(self):
        ids = set()
        kind, status = self.types.currentData(), self.status.currentData()
        for group, checked in self.checked_groups():
            matches = [m for m in group['matches']
                       if (not kind or ('same' if m['left_model'] == m['right_model'] else 'cross') == kind)
                       and (not status or m['status'] == status or status == 'review' and m['status'] in REVIEW)]
            ids.update(checked.intersection(tag_targets(matches,
                include_reading=self.review_options['reading-mismatch'].isChecked(),
                include_spelling=self.review_options['spelling-mismatch'].isChecked(),
                include_missing=self.review_options['missing-reading'].isChecked(), only_source=self.side.currentData())))
        tag_state = self.tags.currentData()
        if tag_state:
            tag = self.effective_tag().casefold()
            ids = {nid for nid in ids if (tag in {t.casefold() for t in self.result['metadata'][nid]['tags']}) == (tag_state == 'tagged')}
        return ids

    def update_checked(self, *_):
        if self.updating:
            return
        ids = self.targets()
        tag = self.effective_tag().casefold()
        old = sum(tag in {t.casefold() for t in self.result.get('metadata', {}).get(nid, {}).get('tags', [])} for nid in ids)
        groups = len(self.checked_groups())
        checked = len({nid for _, selected in self.checked_groups() for nid in selected})
        self.checked_label.setText(f'{groups:,} groups selected · {checked:,} notes checked · {len(ids):,} notes will be tagged ({len(ids)-old:,} new, {old:,} already tagged)')
        self.tag_button.setText(f'Tag {len(ids):,} notes')
        self.tag_button.setEnabled(bool(ids) and bool(tag) and not re.search(r'\s', tag))

    def group_clicked(self, item, _column):
        state = getattr(self.tree, 'pressed_state', None)
        if state and state[0] is item and state[1] != item.checkState(0): return
        self.open_group(item.data(0, ROLE))

    def note_clicked(self, item, _column):
        state = getattr(self.notes, 'pressed_state', None)
        if state and state[0] is item and state[1] != item.checkState(0): return
        self.open_ids([item.data(0, ROLE)])

    def open_group(self, index):
        if index is not None: self.open_ids(sorted(self.groups[index]['notes']))

    def open_ids(self, ids):
        browser = self.parentWidget()
        def show_notes():
            browser.search_for('nid:' + ','.join(map(str, ids)))
            browser_window(browser)
        if browser.editor is not None: browser.editor.call_after_note_saved(show_notes)
        else: show_notes()

    def ignore_selected(self):
        pairs = {tuple(sorted((m['left_note_id'], m['right_note_id']))) for group, checked in self.checked_groups()
                 for m in group['matches'] if m['left_note_id'] in checked and m['right_note_id'] in checked}
        if not pairs:
            showInfo('Check both notes of an overlap, or check an entire group, to mark it intentional.')
            return
        if not askUser(f'Ignore {len(pairs):,} note-pair overlaps in future scans?\nThis applies to all field values shared by each pair. Notes and tags are unchanged.', parent=self): return
        ignore_pairs(pairs)
        self.result['matches'] = [m for m in self.result['matches'] if tuple(sorted((m['left_note_id'], m['right_note_id']))) not in pairs]
        self.groups = group_matches(self.result['matches'])
        affected = {nid for group in self.groups for nid in group['notes']}
        for i, stat in enumerate(self.result.get('overlap', [])):
            stat['matched'] = sum(i in self.result['metadata'][nid]['sources'] for nid in affected)
            stat['percent'] = 100 * stat['matched'] / stat['notes'] if stat['notes'] else 0
        self.populate()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export comparisons', 'anki_duplicates.csv', 'CSV files (*.csv)')
        if not path: return
        if not path.lower().endswith('.csv'): path += '.csv'
        try:
            save_report(path, self.result['matches'])
            self.export_status.setText('CSV exported. Includes all non-ignored comparisons, regardless of filters.')
            self.export_status.show()
        except OSError as exc: showWarning(f'Could not export CSV:\n{exc}', parent=self)

    def tag_notes(self):
        ids, tag = sorted(self.targets()), self.effective_tag()
        if not ids or not tag or re.search(r'\s', tag): return
        # Re-read tags before confirmation so counts do not rely on the snapshot.
        def confirm(existing):
            old = sum(tag.casefold() in {t.casefold() for t in tags} for tags in existing.values())
            if not askUser(f"Tag {len(existing):,} notes with '{tag}'?\n{len(existing)-old:,} new · {old:,} already tagged\nExisting tags are retained. Undo is available in Anki.", parent=self): return
            def success(_):
                for nid in existing:
                    self.result['metadata'][nid]['tags'] = list(set(existing[nid] + [tag]))
                self.filter()
                self.render_details()
                tooltip(f'Tagged {len(existing):,} notes.', parent=self)
            CollectionOp(parent=self, op=lambda col: col.tags.bulk_add(list(existing), tag)).success(success).failure(
                lambda exc: showWarning(f'Could not apply tags:\n{exc}')).run_in_background()
        QueryOp(parent=self, op=lambda col: {nid: list(col.get_note(nid).tags) for nid in ids}, success=confirm).failure(
            lambda exc: showWarning(f'Could not read current note tags:\n{exc}')).run_in_background()

    def save_geometry(self, *_):
        data = settings()
        data['results_size'] = [self.width(), self.height()]
        data['results_split'] = self.splitter.sizes()
        save_settings(data)


def show_results(result, parent=None):
    dialog = ResultsDialog(result, parent=browser_window(parent))
    mw.garbage_collect_on_dialog_finish(dialog)
    _open_results.add(dialog)
    qconnect(dialog.finished, lambda _: _open_results.discard(dialog))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def _close_results():
    for dialog in list(_open_results): dialog.close()


gui_hooks.profile_will_close.append(_close_results)

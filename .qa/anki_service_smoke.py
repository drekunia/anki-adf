import sys, types, importlib.util
from pathlib import Path
root = Path.cwd()
package = types.ModuleType('adfserviceqa')
package.__path__ = [str(root)]
sys.modules['adfserviceqa'] = package
from adfserviceqa.scan_service import scan_collection
from adfserviceqa.live_index import LiveIndex
from anki.collection import Collection
import tempfile
folder = tempfile.mkdtemp(prefix='collection-', dir=str(root / '.qa'))
col = Collection(str(Path(folder) / 'collection.anki2'))
try:
    decks = [col.decks.id('QA Mining'), col.decks.id('QA Kaishi')]
    def make_model(name, fields, siblings=1):
        model = col.models.new(name)
        for name in fields: col.models.add_field(model, col.models.new_field(name))
        for i in range(siblings):
            template = col.models.new_template('Card ' + str(i+1))
            template['qfmt'] = str(i+1) + ' {{' + fields[0] + '}}'
            template['afmt'] = '{{FrontSide}}'
            col.models.add_template(model, template)
        col.models.add(model)
        return model
    kiku = make_model('QA Kiku', ['Expression', 'ExpressionReading', 'Meaning'], 2)
    kaishi = make_model('QA Kaishi', ['Word', 'WordReading', 'Definition'])
    def add(model, did, value, reading):
        note = col.new_note(model)
        note[model['flds'][0]['name']] = value
        note[model['flds'][1]['name']] = reading
        col.add_note(note, did)
        return note
    a = add(kiku, decks[0], 'same', 'a')
    b = add(kaishi, decks[1], '<b>same</b>', 'a')
    value = {'operation':'collection', 'mode':'cross', 'sources':[
        {'model':'QA Kiku', 'deck':None, 'field':'Expression'},
        {'model':'QA Kaishi', 'deck':None, 'field':'Word'}]}
    result = scan_collection(col, value)
    assert len(result['matches']) == 1 and result['matches'][0]['status'] == 'normalized-match'
    assert result['metadata'][int(a.id)]['fields']['Expression'] == 'same'
    value.update(operation='decks', decks=['QA Mining', 'QA Kaishi'])
    assert len(scan_collection(col, value)['matches']) == 1
    # Any suspended sibling excludes the entire note, even if another is active.
    sibling = a.cards()[0]
    sibling.queue = -1
    sibling.did = decks[1]
    col.update_card(sibling)
    value['exclude_suspended'] = True
    assert scan_collection(col, value)['matches'] == []
    value['exclude_suspended'] = False
    result = scan_collection(col, value)
    assert result['metadata'][int(a.id)]['decks'] == ['QA Kaishi', 'QA Mining']
    col.tags.bulk_add([a.id, b.id], 'qa_duplicate')
    assert 'qa_duplicate' in col.get_note(a.id).tags
    assert col.undo_status().undo
    col.undo()
    assert 'qa_duplicate' not in col.get_note(a.id).tags
    value['operation'] = 'against'
    index = LiveIndex()
    index.scan(col, value, int(a.id))
    new = add(kiku, decks[0], 'same', 'a')
    result = index.scan(col, value, int(new.id))
    assert len(result['matches']) == 1
    value['ignored_pairs'] = [[int(new.id), int(b.id)]]
    assert index.scan(col, value, int(new.id))['matches'] == []
    print('PASS: real Anki temporary collection — deck/source search, normalized matching, multiple decks, any-sibling suspension, undoable tags, incremental mining index and ignore pairs', flush=True)
finally:
    col.close()

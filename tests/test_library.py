"""Reversible directory metadata over synthetic immutable research fixtures."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Projects, dispatch
from library_store import Library, record_title
from material_store import Materials
from preparation_tools import execute
from schema import StoreError, canonical, decode, digest
from workbench_store import Workbench
from test_chat_research import PROBLEM, note


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dsh-library-synthetic-')
        self.root = Path(self.temp.name).resolve() / 'data'
        self.projects = Projects(self.root)
        self.project = self.projects.create('session-a', deepcopy(PROBLEM))['id']
        self.library = Library(self.root)
        self.entry = {'kind': 'object', 'id': 'N'}
        self.projects.mutate(self.project, 'commit', {'operation_id': 'note', 'changes': [
            {'kind': 'Note', 'id': 'N', 'expected_rev': 0, 'data': note()}]})

    def tearDown(self):
        self.temp.cleanup()

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def change(self, action='update', rev=0, op='metadata', entry=None, project=None, **metadata):
        return self.library.change(action, 'session-a', self.project if project is None else project,
                                   entry or self.entry, rev, op, metadata)

    def model(self, name, args, session='session-a', call='transport-call'):
        return execute(self.root, {'session_id': session, 'tool_call_id': call, 'name': name, 'arguments': args})

    def test_pure_bounded_directory_and_exact_windows(self):
        before = files(self.root)
        page = self.library.listing('session-a', self.project, limit=1)
        self.assertEqual(page['total'], 3)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['next_offset'], 1)
        result = self.library.read('session-a', self.project, self.entry, limit=37)
        self.assertEqual(len(result['text']), 37)
        self.assertEqual(result['next_offset'], 37)
        self.assertTrue(result['truncated'])
        self.assertEqual(files(self.root), before)
        self.assertFalse((self.root / 'library').exists())
        for options in ({'limit': 0}, {'offset': True}, {'query': 'x' * 201}, {'sort': 'random'}, {'removed_only': 1}):
            self.error('INVALID_SCHEMA', self.library.listing, 'session-a', self.project, **options)
        self.error('TOO_LARGE', self.library.listing, 'session-a', self.project, limit=101)
        self.error('TOO_LARGE', self.library.read, 'session-a', self.project, self.entry, limit=12001)
        self.error('INVALID_SCHEMA', self.library.read, 'session-a', self.project, self.entry, offset=10**9)

    def test_titleless_obligation_has_readable_directory_and_backlink_titles(self):
        obligation_id = 'gap-54b254b6-3c1f-4471-ac9d-227b429f75c5'
        statement = '合成待证事项，无数学结论'
        obligation = {'statement': statement, 'scope': 'Synthetic scope', 'completion_criteria': ['Read'],
                      'next_action': 'Read details', 'reason': 'Synthetic fixture', 'check_method': 'human',
                      'checker_id': None, 'problem_ref': {'id': 'P', 'rev': 1}, 'uses': [{'id': 'N', 'rev': 1}],
                      'candidates': [], 'evidence': [], 'state': 'open'}
        self.projects.mutate(self.project, 'commit', {'operation_id': 'titleless-obligation', 'changes': [
            {'kind': 'Obligation', 'id': obligation_id, 'expected_rev': 0, 'data': obligation}]})
        before = files(self.projects.path(self.project) / 'research')
        # The workbench recents and library use this same human directory response.
        page = dispatch(self.root, {'action': 'library-list', 'args': {'session_id': 'session-a', 'project_id': self.project}})
        row = next(item for item in page['items'] if item['entry']['id'] == obligation_id)
        self.assertEqual(row['title'], statement)
        self.assertNotIn(obligation_id, row['title'])
        target = next(item for item in page['items'] if item['entry'] == self.entry)
        self.assertEqual(target['referenced_by'][0]['title'], statement)
        self.assertEqual(files(self.projects.path(self.project) / 'research'), before)
        for value in ('', '   ', obligation_id):
            self.assertEqual(record_title({'kind': 'Obligation', 'id': obligation_id, 'data': {'statement': value}}), '待证事项')
        long_title = record_title({'kind': 'Obligation', 'id': obligation_id, 'data': {'statement': 'x' * 200}})
        self.assertEqual(len(long_title), 160)
        self.assertTrue(long_title.endswith('…'))

    def test_metadata_cas_restart_receipts_and_immutable_history(self):
        research = self.projects.path(self.project) / 'research'
        original = files(research)
        receipt = self.change(title='Display title', summary='Directory summary', tags=['algebra'], pinned=True)
        self.assertEqual(receipt['metadata']['metadata_rev'], 1)
        self.library = Library(self.root)
        self.assertEqual(self.change(title='Display title', summary='Directory summary', tags=['algebra'], pinned=True), receipt)
        self.error('OPERATION_CONFLICT', self.change, title='different')
        self.error('VERSION_CONFLICT', self.change, op='stale', title='stale')
        removed = self.change('remove', 1, 'remove')
        restored = self.change('restore', 2, 'restore')
        self.assertEqual(self.change('remove', 1, 'remove'), removed)
        self.assertEqual(self.change('restore', 2, 'restore'), restored)
        self.assertEqual(len(list((self.root / 'library').glob('*.json'))), 3)
        self.assertEqual(files(research), original)
        read = json.loads(self.library.read('session-a', self.project, self.entry, rev=1)['text'])
        self.assertEqual(read['content']['data'], note())
        self.assertEqual(read['library']['title'], 'Display title')
        self.assertEqual(read['library']['metadata_rev'], 3)
        self.assertFalse(read['library']['removed'])

    def test_removed_reference_readable_and_ai_directory_hidden(self):
        referencing = {**note(), 'title': 'Uses removed note', 'uses': [{'id': 'N', 'rev': 1}]}
        self.projects.mutate(self.project, 'commit', {'operation_id': 'reference', 'changes': [
            {'kind': 'Note', 'id': 'N2', 'expected_rev': 0, 'data': referencing}]})
        self.change('remove', 0, 'remove')
        active = self.library.listing('session-a', self.project)
        self.assertNotIn(self.entry, [item['entry'] for item in active['items']])
        trash = self.library.listing('session-a', self.project, removed_only=True, limit=1, kind='Note')
        self.assertEqual(trash['total'], 1)
        self.assertEqual(trash['items'][0]['referenced_by'], [{'title': 'Uses removed note', 'kind': 'Note', 'ref': {'id': 'N2', 'rev': 1}}])
        self.assertEqual(trash['items'][0]['referenced_by_total'], 1)
        args = {'project_id': self.project, 'ref': None, 'offset': 0, 'limit': 12000}
        directory = json.loads(self.model('math_research_read', args)['text'])
        self.assertNotIn('N', [item['ref']['id'] for item in directory])
        exact = json.loads(self.model('math_research_read', {**args, 'ref': {'id': 'N', 'rev': 1}})['text'])
        self.assertTrue(exact['library']['removed'])
        self.assertEqual(exact['data'], note())
        self.projects.mutate(self.project, 'commit', {'operation_id': 'still-valid-reference', 'changes': [
            {'kind': 'Note', 'id': 'N3', 'expected_rev': 0, 'data': referencing}]})

    def test_removed_backlinks_do_not_leak_title_ref_or_count(self):
        secret = {**note(), 'title': 'REMOVED_SOURCE_SECRET', 'uses': [{'id': 'N', 'rev': 1}]}
        self.projects.mutate(self.project, 'commit', {'operation_id': 'secret-source', 'changes': [
            {'kind': 'Note', 'id': 'Secret', 'expected_rev': 0, 'data': secret}]})
        self.change('remove', entry={'kind': 'object', 'id': 'Secret'}, op='remove-secret')
        active = self.library.listing('session-a', self.project)
        self.assertNotIn('REMOVED_SOURCE_SECRET', json.dumps(active))
        self.assertNotIn('Secret', json.dumps(active))
        for row in active['items']:
            if row['entry']['id'] in ('P', 'N'):
                self.assertNotIn('Secret', [source['ref']['id'] for source in row['referenced_by']])
        note_row = next(row for row in active['items'] if row['entry'] == self.entry)
        self.assertEqual(note_row['referenced_by'], [])
        self.assertEqual(note_row['referenced_by_total'], 0)
        all_rows = self.library.listing('session-a', self.project, include_removed=True)['items']
        full_note = next(row for row in all_rows if row['entry'] == self.entry)
        self.assertEqual(full_note['referenced_by_total'], 1)
        self.assertEqual(full_note['referenced_by'][0]['ref'], {'id': 'Secret', 'rev': 1})
        exact = json.loads(self.library.read('session-a', self.project, {'kind': 'object', 'id': 'Secret'}, rev=1)['text'])
        self.assertEqual(exact['content']['data'], secret)
        self.assertTrue(exact['library']['removed'])
        # Removal confirmation for N still counts only active source backlinks.
        self.change('remove', op='remove-target')
        trash = self.library.listing('session-a', self.project, removed_only=True)['items']
        target = next(row for row in trash if row['entry'] == self.entry)
        self.assertEqual(target['referenced_by_total'], 0)
        self.change('restore', rev=1, entry={'kind': 'object', 'id': 'Secret'}, op='restore-secret')
        restored = self.library.listing('session-a', self.project, removed_only=True)['items']
        target = next(row for row in restored if row['entry'] == self.entry)
        self.assertEqual(target['referenced_by_total'], 1)
        self.assertEqual(target['referenced_by'][0]['ref'], {'id': 'Secret', 'rev': 1})

    def test_filters_sort_and_metadata_bounds(self):
        self.change(title='Zebra', tags=['SearchTag'])
        self.change(entry={'kind': 'object', 'id': 'P'}, op='pin', title='Alpha', pinned=True)
        result = self.library.listing('session-a', self.project, query='searchtag', sort='title_asc')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['title'], 'Zebra')
        items = self.library.listing('session-a', self.project, sort='title_asc')['items']
        self.assertEqual(items[0]['entry']['id'], 'P')
        for metadata in ({'body': 'cannot edit original'}, {'tags': ['x', 'x']}, {'tags': ['x'] * 51}, {'pinned': 1}):
            self.error('INVALID_SCHEMA', self.change, op='invalid', rev=1, **metadata)
        self.error('TOO_LARGE', self.change, rev=1, op='long', title='x' * 2001)
        self.error('TOO_LARGE', self.change, rev=1, op='long-summary', summary='x' * 12001)
        self.error('NOT_FOUND', self.change, entry={'kind': 'object', 'id': 'missing'})

    def test_common_human_model_dispatch_scope_and_provenance(self):
        args = {'project_id': self.project, 'entry': self.entry, 'action': 'update', 'expected_rev': 0,
                'operation_id': 'model-metadata', 'patch': {'title': 'Model display'}}
        result = self.model('math_library_manage', args)
        self.assertEqual(self.model('math_library_manage', args, call='retry-transport'), result)
        page = self.model('math_library_list', {'project_id': self.project})
        self.assertIn('Model display', [item['title'] for item in page['items']])
        self.error('PROJECT_SCOPE', self.model, 'math_library_list', {'project_id': self.project}, session='session-b')
        self.error('PROJECT_SCOPE', self.model, 'math_library_manage', {**args, 'operation_id': 'other'}, session='session-b')
        self.error('INVALID_SCHEMA', self.model, 'math_library_list', {'project_id': self.project, 'session_id': 'session-a'})
        event = decode(next((self.root / 'library').glob('*.json')).read_bytes())
        self.assertEqual(event['request']['actor'], {'kind': 'model', 'id': 'dsh-math-chat'})
        self.assertEqual(event['request']['origin']['tool_call_id'], 'transport-call')
        human = dispatch(self.root, {'action': 'library-list', 'args': {'session_id': 'session-a', 'project_id': self.project}})
        self.assertEqual(human, page)

    def test_project_remove_restore_and_context_filtering(self):
        entry = {'kind': 'project', 'id': self.project}
        self.change('remove', entry=entry)
        self.assertEqual(self.projects.listing(), [])
        self.assertTrue(self.projects.listing(include_removed=True)[0]['library']['removed'])
        context = self.model('math_project_context', {'query': '', 'offset': 0, 'limit': 50})
        self.assertEqual(context['projects'], [])
        self.assertIsNone(context['current_project_id'])
        exact = self.library.read('session-a', self.project, entry)
        self.assertTrue(exact['item']['removed'])
        self.change('restore', 1, 'restore-project', entry=entry)
        self.assertEqual(len(self.projects.listing()), 1)
        self.assertEqual(len(self.model('math_project_context', {'query': '', 'offset': 0, 'limit': 50})['projects']), 1)

    def test_draft_scope_tombstone_and_confirmation_block(self):
        workbench = Workbench(self.root)
        draft = workbench.draft_save('session-a', 0, 'prepare', {key: deepcopy(value) for key, value in PROBLEM.items() if key not in ('parent', 'relation')})['draft']
        entry = {'kind': 'draft', 'id': draft['id']}
        self.library.change('remove', 'session-a', None, entry, 0, 'remove-draft')
        self.assertIsNone(workbench.draft_read('session-a')['draft'])
        self.assertTrue(workbench.draft_read('session-a', draft['id'])['library']['removed'])
        self.assertEqual(self.library.listing('session-b', None)['items'], [])
        self.error('NOT_FOUND', self.library.read, 'session-b', None, entry)
        self.error('REMOVED', workbench.finalize_draft, 'session-a', draft['id'], 1, 'finalize')
        self.library.change('restore', 'session-a', None, entry, 1, 'restore-draft')
        with patch.object(workbench.projects, '_create_project', side_effect=RuntimeError('interrupted before publication')):
            with self.assertRaises(RuntimeError):
                workbench.finalize_draft('session-a', draft['id'], 1, 'finalize')
        row = self.library.listing('session-a', None)['items'][0]
        self.assertFalse(row['can_manage'])
        retry = self.library.change('restore', 'session-a', None, entry, 1, 'restore-draft')
        self.assertEqual(retry['metadata']['metadata_rev'], 2)
        self.error('FINALIZE_PENDING', self.library.change, 'remove', 'session-a', None, entry, 2, 'pending-remove')
        self.assertIsNotNone(workbench.draft_read('session-a')['pending_finalization'])

    def test_preparation_exact_history_survives_content_edits_and_removal(self):
        workbench = Workbench(self.root)
        first = workbench.draft_save('session-a', 0, 'draft-first', {'title': 'First'})['draft']
        workbench.draft_save('session-a', 1, 'draft-second', {'title': 'Second'}, first['id'])
        entry = {'kind': 'draft', 'id': first['id']}
        self.library.change('remove', 'session-a', None, entry, 0, 'remove-draft')
        read = self.library.read('session-a', None, entry, rev=1)
        self.assertEqual(read['item']['content_rev'], 1)
        self.assertTrue(read['item']['removed'])
        self.assertEqual(json.loads(read['text'])['content']['fields']['title'], 'First')
        self.error('NOT_FOUND', self.library.read, 'session-a', None, entry, rev=3)

    def test_scratchpad_historical_content_and_reversible_metadata(self):
        workbench = Workbench(self.root)
        workbench.scratchpad_save('session-a', self.project, 0, 'pad-1', 'First', 'old α')
        workbench.scratchpad_save('session-a', self.project, 1, 'pad-2', 'Second', 'new β')
        entry = {'kind': 'scratchpad', 'id': 'main'}
        before = files(self.root / 'workbench')
        self.change('remove', entry=entry)
        old = json.loads(self.library.read('session-a', self.project, entry, rev=1)['text'])
        self.assertEqual(old['content']['body'], 'old α')
        self.assertTrue(old['library']['removed'])
        self.assertTrue(workbench.scratchpad_read('session-a', self.project, rev=1)['library']['removed'])
        self.assertEqual(files(self.root / 'workbench'), before)
        self.change('restore', 1, 'restore-pad', entry=entry)
        self.assertIn(entry, [row['entry'] for row in self.library.listing('session-a', self.project)['items']])

    def test_material_derived_from_committed_evidence_and_original_preserved(self):
        materials = Materials(self.root)
        raw = b'# Synthetic material\n$x^2$'
        upload = materials.begin('session-a', self.project, 'upload', [
            {'name': 'fixture.md', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}])
        materials.chunk('session-a', upload['upload_id'], upload['files'][0]['file_id'], 0, base64.b64encode(raw).decode())
        self.assertEqual(self.library.listing('session-a', self.project, kind='material')['total'], 0)
        preview = materials.preview('session-a', upload['upload_id'])
        result = materials.confirm('session-a', upload['upload_id'], 'confirm', preview['preview_hash'], self.project)
        ref = result['materials'][0]['ref']
        row = self.library.listing('session-a', self.project, kind='material')['items'][0]
        self.assertEqual(row['title'], 'fixture.md')
        self.assertEqual(row['bytes'], len(raw))
        self.assertEqual(row['material']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(row['material']['status'], 'extracted')
        before = files(self.projects.path(self.project) / 'research')
        entry = {'kind': 'object', 'id': ref['id']}
        self.change('remove', entry=entry)
        read = materials.read('session-a', self.project, ref, 0, 12000)
        self.assertTrue(read['library']['removed'])
        self.assertIn('Synthetic material', read['text'])
        self.assertEqual(files(self.projects.path(self.project) / 'research'), before)

    def test_readonly_mode_and_read_paths_do_not_write(self):
        metadata = self.projects.path(self.project) / 'project.json'
        project = decode(metadata.read_bytes())
        metadata.write_bytes(canonical({**project, 'mode': 'legacy_readonly_copy'}))
        before = files(self.root)
        self.assertFalse(self.library.listing('session-a', self.project)['items'][0]['can_manage'])
        self.error('READ_ONLY', self.change, title='blocked')
        self.assertEqual(files(self.root), before)

    def test_rehashed_semantically_invalid_transition_rejected(self):
        self.change(title='valid')
        path = next((self.root / 'library').glob('*.json'))
        event = decode(path.read_bytes())
        event['result']['metadata']['removed'] = True
        event['event_hash'] = digest({key: value for key, value in event.items() if key != 'event_hash'})
        path.write_bytes(canonical(event))
        self.error('CORRUPT_SIDECAR', Library(self.root).listing, 'session-a', self.project)

    def test_interruption_after_publication_has_durable_retry_receipt(self):
        import research_store
        original = research_store.sync_directory
        def interrupt(path):
            if Path(path) == self.root / 'library' and any(Path(path).glob('*.json')):
                raise RuntimeError('after publication before acknowledgement')
            return original(path)
        with patch('research_store.sync_directory', side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.change(title='durable')
        receipt = Library(self.root).change('update', 'session-a', self.project, self.entry, 0, 'metadata', {'title': 'durable'})
        self.assertEqual(receipt['metadata']['metadata_rev'], 1)
        self.assertEqual(len(list((self.root / 'library').glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()

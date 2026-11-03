"""Fresh synthetic selected exports; no models, research runs, or program reviews."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from backend import Projects
from library_store import Library
from material_store import Materials
from selection_export import export_selection, read_export
from schema import StoreError

PROBLEM = {'title': 'Synthetic export α', 'statement': 'Temporary strings only', 'quantifiers': 'None',
           'domain': 'Fixtures', 'completion_criteria': ['Read strings'], 'sources': ['Synthetic'],
           'knowledge_status': 'synthetic_fixture', 'target_type': 'exploratory', 'conventions': [], 'parent': None, 'relation': ''}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dsh-selection-synthetic-')
        self.root = Path(self.temp.name).resolve()
        self.projects = Projects(self.root / 'data')
        self.project = self.projects.create('session-a', deepcopy(PROBLEM))['id']
        self.library = Library(self.projects.root)
        problem = self.projects.overview(self.project)['objects'][0]
        self.problem_ref = {'id': problem['id'], 'rev': problem['rev']}
        data = {'note_type': 'observation', 'title': 'Selected note / α', 'summary': 'Summary', 'body': 'SELECTED_BODY',
                'statement': 'Synthetic observation', 'assumptions': [], 'problem_ref': self.problem_ref,
                'route_id': None, 'uses': [], 'candidate_for': [], 'evidence': [], 'gaps': [], 'failure': None}
        self.projects.mutate(self.project, 'commit', {'operation_id': 'notes', 'changes': [
            {'kind': 'Note', 'id': 'N', 'expected_rev': 0, 'data': data},
            {'kind': 'Note', 'id': 'Hidden', 'expected_rev': 0, 'data': {**data, 'title': 'UNSELECTED_SECRET', 'body': 'UNSELECTED_BODY'}}]})

    def tearDown(self):
        self.temp.cleanup()

    def export(self, refs=None, **options):
        return export_selection(self.projects, self.project, self.projects.overview(self.project)['snapshot']['state']['seq'],
                                self.library.snapshot(self.project)['version'], refs if refs is not None else [{'id': 'N', 'rev': 1}],
                                session_id='session-a', **options)

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def extract(self, result, name='extracted'):
        target = self.root / name
        with zipfile.ZipFile(result['zip_path']) as archive:
            self.assertTrue(all(not n.startswith('/') and '..' not in Path(n).parts for n in archive.namelist()))
            archive.extractall(target)
        return target

    def verify(self, directory, success=True):
        value = subprocess.run(['python3', '-B', 'verify_bundle.py', '.'], cwd=directory, capture_output=True, text=True)
        self.assertEqual(value.returncode == 0, success, value.stderr)

    def test_selected_readable_and_relocated_zip(self):
        result = self.export(include_process=True)
        target = self.extract(result)
        self.verify(target)
        relocated = self.root / 'relocated'
        shutil.move(target, relocated)
        self.verify(relocated)
        joined = b''.join(p.read_bytes() for p in relocated.rglob('*') if p.is_file())
        self.assertIn(b'SELECTED_BODY', joined)
        self.assertNotIn(b'UNSELECTED_SECRET', joined)
        self.assertNotIn(b'UNSELECTED_BODY', joined)
        value = json.loads((relocated / 'selection.json').read_text())
        self.assertEqual(len(value['records']), 1)
        self.assertEqual(value['omissions'][0]['reason'], 'not_selected')
        self.assertNotIn('statement', value['omissions'][0])
        self.assertTrue((relocated / 'process/process.svg').exists())
        part = read_export(self.projects, self.project, result['export_id'], 0, 100, 'session-a')
        self.assertEqual(base64.b64decode(part['base64']), Path(result['zip_path']).read_bytes()[:100])
        self.error('PROJECT_SCOPE', read_export, self.projects, self.project, result['export_id'], 0, 100, 'other')
        self.error('UNSAFE_PATH', read_export, self.projects, self.project, '../bad')

    def test_associated_session_export_download_and_revocation(self):
        from project_access import ProjectAccess
        access = ProjectAccess(self.projects.root)
        seq = self.projects.overview(self.project)['snapshot']['state']['seq']
        version = self.library.snapshot(self.project)['version']
        refs = [{'id': 'N', 'rev': 1}]
        self.error('PROJECT_SCOPE', export_selection, self.projects, self.project, seq, version, refs, session_id='session-b')
        access.change('associate', 'session-b', 0, 'associate-export', self.project)
        result = export_selection(self.projects, self.project, seq, version, refs, session_id='session-b')
        target = self.extract(result, 'associated')
        self.verify(target)
        part = read_export(self.projects, self.project, result['export_id'], 0, 100, 'session-b')
        self.assertEqual(base64.b64decode(part['base64']), Path(result['zip_path']).read_bytes()[:100])
        self.error('PROJECT_SCOPE', read_export, self.projects, self.project, result['export_id'], 0, 100, 'session-c')
        self.assertEqual(read_export(self.projects, self.project, result['export_id'])['bytes'], result['bytes'])
        # JSON dispatch still requires session identity; no-session use is internal only.
        from backend import dispatch
        args = {'project_id': self.project, 'export_id': result['export_id'], 'offset': 0, 'limit': 100, 'session_id': None}
        self.error('INVALID_SCHEMA', dispatch, self.projects.root, {'action': 'selection-bytes', 'args': args})
        export_args = {'project_id': self.project, 'expected_seq': seq, 'expected_library_version': version,
                       'selected_refs': refs, 'session_id': None}
        self.error('INVALID_SCHEMA', dispatch, self.projects.root, {'action': 'selection-export', 'args': export_args})
        access.change('disassociate', 'session-b', 1, 'revoke-export', self.project)
        self.error('PROJECT_SCOPE', export_selection, self.projects, self.project, seq, version, refs, session_id='session-b')
        self.error('PROJECT_SCOPE', read_export, self.projects, self.project, result['export_id'], 0, 100, 'session-b')
        # Ownership remains sufficient after another session's association is revoked.
        self.assertEqual(read_export(self.projects, self.project, result['export_id'], 0, 100, 'session-a')['bytes'], result['bytes'])

    def test_dependency_opt_in_and_deleted_omission(self):
        target = self.extract(self.export(include_dependencies=True))
        value = json.loads((target / 'selection.json').read_text())
        self.assertEqual(len(value['records']), 2)
        self.library.change('remove', 'session-a', self.project, {'kind': 'object', 'id': self.problem_ref['id']}, 0, 'remove-problem')
        target = self.extract(self.export(include_dependencies=True), 'removed')
        value = json.loads((target / 'selection.json').read_text())
        self.assertEqual(len(value['records']), 1)
        self.assertEqual(value['omissions'][0]['reason'], 'unavailable')
        self.assertNotIn('title', value['omissions'][0])
        self.assertNotIn('ref', value['omissions'][0])
        self.error('EXPORT_SELECTION', self.export, [self.problem_ref])

    def test_snapshot_versions_and_input_bounds(self):
        seq = self.projects.overview(self.project)['snapshot']['state']['seq']
        version = self.library.snapshot(self.project)['version']
        self.error('VERSION_CONFLICT', export_selection, self.projects, self.project, seq - 1, version, [{'id': 'N', 'rev': 1}], session_id='session-a')
        self.library.change('update', 'session-a', self.project, {'kind': 'object', 'id': 'N'}, 0, 'rename', {'title': 'New readable name'})
        self.error('VERSION_CONFLICT', export_selection, self.projects, self.project, seq, version, [{'id': 'N', 'rev': 1}], session_id='session-a')
        self.error('INVALID_SCHEMA', self.export, [])
        self.error('INVALID_SCHEMA', self.export, [{'id': 'N', 'rev': 1}] * 501)
        self.error('INVALID_SCHEMA', self.export, include_dependencies='yes')
        self.error('INVALID_SCHEMA', self.export, [{'id': 'N', 'rev': 1}] * 2)

    def test_tampered_missing_extra_and_symlink_files_fail(self):
        for mode in ('tamper', 'missing', 'extra', 'symlink', 'appledouble'):
            target = self.extract(self.export(), mode)
            record = next((target / 'records').iterdir())
            if mode == 'tamper':
                record.write_text('tampered')
            elif mode == 'missing':
                record.unlink()
            elif mode in ('extra', 'appledouble'):
                (target / ('unexpected' if mode == 'extra' else '._private-payload')).write_text('extra')
            else:
                record.unlink()
                record.symlink_to(self.root / 'outside')
            self.verify(target, False)

    def test_rehashed_semantically_invalid_references_and_options_fail(self):
        for mode in ('reference', 'options', 'marker'):
            target = self.extract(self.export(include_dependencies=True), 'semantic-' + mode)
            manifest = json.loads((target / 'manifest.json').read_text())
            selection = json.loads((target / 'selection.json').read_text())
            if mode == 'reference':
                selection['records'][0]['data']['problem_ref'] = {'ref': {'id': 'DOES_NOT_EXIST', 'rev': 999}, 'title': 'Fake', 'file': '../../outside'}
            elif mode == 'marker':
                selection['records'][0]['data']['problem_ref'] = {'status': 'omitted', 'reason': 'unavailable', 'title': 'PRIVATE'}
            else:
                manifest['options']['include_dependencies'] = 'yes'
            raw = json.dumps(selection).encode()
            (target / 'selection.json').write_bytes(raw)
            manifest['files']['selection.json'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            (target / 'manifest.json').write_text(json.dumps(manifest))
            self.verify(target, False)

    def test_titleless_omission_does_not_disclose_statement(self):
        obligation = {'statement': 'OMITTED_PRIVATE_STATEMENT', 'scope': 'OMITTED_PRIVATE_SCOPE', 'completion_criteria': ['Synthetic'],
                      'check_method': 'human', 'checker_id': None, 'problem_ref': self.problem_ref, 'uses': [], 'candidates': [],
                      'evidence': [], 'next_action': 'Read', 'state': 'open', 'reason': 'Synthetic fixture'}
        data = next(o for o in self.projects.overview(self.project)['objects'] if o['id'] == 'N')['data']
        self.projects.mutate(self.project, 'commit', {'operation_id': 'obligation', 'changes': [
            {'kind': 'Obligation', 'id': 'O', 'expected_rev': 0, 'data': obligation},
            {'kind': 'Note', 'id': 'N', 'expected_rev': 1, 'data': {**data, 'uses': [{'id': 'O', 'rev': 1}]}}]})
        target = self.extract(self.export([{'id': 'N', 'rev': 2}]), 'omission-title')
        value = json.loads((target / 'selection.json').read_text())
        marker = next(m for m in value['omissions'] if m.get('ref', {}).get('id') == 'O')
        self.assertEqual(marker['title'], 'Obligation')
        joined = b''.join(p.read_bytes() for p in target.rglob('*') if p.is_file())
        self.assertNotIn(b'OMITTED_PRIVATE_STATEMENT', joined)
        self.assertNotIn(b'OMITTED_PRIVATE_SCOPE', joined)

    def test_material_originals_explicit_only(self):
        materials = Materials(self.projects.root)
        raw = b'SYNTHETIC ORIGINAL ONLY'
        upload = materials.begin('session-a', self.project, 'upload', [{'name': 'readable.txt', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}])
        materials.chunk('session-a', upload['upload_id'], upload['files'][0]['file_id'], 0, base64.b64encode(raw).decode())
        preview = materials.preview('session-a', upload['upload_id'])
        result = materials.confirm('session-a', upload['upload_id'], 'confirm', preview['preview_hash'], self.project)
        ref = result['materials'][0]['ref']
        target = self.extract(self.export([], material_refs=[ref]), 'no-original')
        self.assertFalse((target / 'materials').exists())
        selection = json.loads((target / 'selection.json').read_text())
        self.assertEqual(selection['records'][0]['data']['material_text'], raw.decode())
        target = self.extract(self.export([], material_refs=[ref], include_material_originals=True), 'with-original')
        self.assertEqual([p.read_bytes() for p in (target / 'materials').iterdir()], [raw])
        self.verify(target)


if __name__ == '__main__':
    unittest.main()

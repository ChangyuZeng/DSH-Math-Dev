"""Materials use fresh synthetic fixtures only; no models or research execution."""
from copy import deepcopy
import base64
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from backend import Projects, dispatch
from material_store import Materials, CHUNK, MAX_FILE, MAX_BATCH, extract
from schema import StoreError, canonical, decode

PROBLEM = {'title': 'Material fixture', 'statement': 'Synthetic upload only', 'quantifiers': 'None',
           'domain': 'Fixtures', 'completion_criteria': ['Archive bytes'], 'sources': ['Synthetic temporary fixtures'],
           'knowledge_status': 'synthetic_fixture', 'target_type': 'exploratory',
           'conventions': [], 'parent': None, 'relation': ''}


def pdf(text='Synthetic PDF text'):
    stream = ('BT /F1 12 Tf 20 100 Td (' + text + ') Tj ET').encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data, offsets = b'%PDF-1.4\n', [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return data + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n'


def docx():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic DOCX α</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dsh-material-synthetic-')
        self.root = Path(self.tmp.name).resolve() / 'data'
        self.projects = Projects(self.root)
        self.materials = Materials(self.root)
        self.project = self.projects.create('session-a', deepcopy(PROBLEM))['id']
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def upload(self, files, project=True, op=None):
        self.counter += 1
        begin = self.materials.begin('session-a', self.project if project else None, op or f'upload-{self.counter}',
                [{'name': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in files])
        for row, (_, raw) in zip(begin['files'], files):
            for index in range((len(raw) + CHUNK - 1) // CHUNK):
                self.materials.chunk('session-a', begin['upload_id'], row['file_id'], index,
                                     base64.b64encode(raw[index * CHUNK:(index + 1) * CHUNK]).decode())
        return begin['upload_id']

    def confirm(self, upload, **kwargs):
        preview = self.materials.preview('session-a', upload)
        return self.materials.confirm('session-a', upload, 'confirm', preview['preview_hash'], self.project, **kwargs)

    def test_mixed_formats_real_extractors_and_archive_only(self):
        files = [('paper.pdf', pdf()), ('paper.docx', docx()), ('notes.md', '# α\n$x^2$'.encode()),
                 ('code.py', b'raise RuntimeError("MUST NEVER EXECUTE")\n'),
                 ('unknown.bin', b'\x00\xff\x13'), ('bad.docx', b'not a zip')]
        upload = self.upload(files)
        preview = self.materials.preview('session-a', upload)
        self.assertEqual([row['status'] for row in preview['files']],
                         ['extracted', 'extracted', 'extracted', 'extracted', 'archive_only', 'failed'])
        self.assertIn('Synthetic PDF text', preview['files'][0]['text'])
        self.assertIn('Synthetic DOCX α', preview['files'][1]['text'])
        result = self.confirm(upload)
        self.assertTrue(all(row['status'] == 'imported' for row in result['materials']))
        self.assertEqual(self.confirm(upload), result)
        view = self.projects.overview(self.project)
        self.assertEqual(len([o for o in view['objects'] if o['kind'] == 'Evidence']), len(files))
        self.assertEqual(view['operation_model_requests'], 0)
        for (_, raw), item in zip(files, result['materials']):
            obj = next(o for o in view['objects'] if o['id'] == item['ref']['id'])
            original = obj['data']['attachments'][0]
            archived = self.projects.path(self.project) / 'research' / original['relative_path']
            self.assertEqual(archived.read_bytes(), raw)
            self.assertEqual(obj['data']['source_status'], 'unverified_source')
            self.assertIsNone(obj['data']['citation'])

    def test_prepare_without_project_and_session_gates(self):
        upload = self.upload([('a.txt', b'synthetic')], project=False)
        preview = self.materials.preview('session-a', upload)
        self.assertIsNone(preview['project_id'])
        for fn, args in [(self.materials.preview, (upload,)),
                         (self.materials.chunk, (upload, '0', 0, base64.b64encode(b'synthetic').decode())),
                         (self.materials.cleanup, (upload,))]:
            self.error('PROJECT_SCOPE', fn, 'other-session', *args)
        other = self.projects.create('other-session', deepcopy(PROBLEM))['id']
        self.error('PROJECT_SCOPE', self.materials.confirm, 'session-a', upload, 'confirm', preview['preview_hash'], other)
        result = self.confirm(upload)
        ref = result['materials'][0]['ref']
        self.error('PROJECT_SCOPE', self.materials.read, 'other-session', self.project, ref)
        self.assertEqual(self.materials.read('session-a', self.project, ref)['text'], 'synthetic')
        self.materials.cleanup('session-a', upload)
        self.assertEqual(self.materials.cleanup('session-a', upload), {'upload_id': upload, 'cleaned': True})
        self.assertEqual(self.materials.read('session-a', self.project, ref)['text'], 'synthetic')

    def test_duplicate_choice_and_exact_bounded_read(self):
        first = self.confirm(self.upload([('a.txt', b'abcdef')]))['materials'][0]
        upload = self.upload([('renamed.txt', b'abcdef')])
        self.assertEqual(self.materials.preview('session-a', upload)['files'][0]['existing_ref'], first['ref'])
        self.assertEqual(self.confirm(upload)['materials'][0]['status'], 'duplicate')
        again = self.confirm(self.upload([('again.txt', b'abcdef')]), skip_duplicates=False)['materials'][0]
        self.assertNotEqual(first['ref'], again['ref'])
        value = self.materials.read('session-a', self.project, first['ref'], 1, 3)
        self.assertEqual((value['text'], value['next_offset'], value['total_chars']), ('bcd', 4, 6))
        self.error('TOO_LARGE', self.materials.read, 'session-a', self.project, first['ref'], 0, 12001)
        self.error('INVALID_SCHEMA', self.materials.read, 'session-a', self.project, first['ref'], 7)
        self.error('NOT_MATERIAL', self.materials.read, 'session-a', self.project, {'id': 'P', 'rev': 1})
        self.error('NOT_FOUND', self.materials.read, 'session-a', self.project, {'id': first['ref']['id'], 'rev': 2})

    def test_chunk_retry_hash_incomplete_and_partial_failure(self):
        raw = b'x' * (CHUNK + 1)
        upload = self.upload([('large.txt', raw), ('good.txt', b'good')])
        encoded = base64.b64encode(raw[:CHUNK]).decode()
        self.materials.chunk('session-a', upload, '0', 0, encoded)
        self.error('OPERATION_CONFLICT', self.materials.chunk, 'session-a', upload, '0', 0,
                   base64.b64encode(b'y' * CHUNK).decode())
        path = self.materials._path(upload) / 'chunk-0-1'
        path.unlink()  # Simulate an interrupted upload, not a committed object.
        preview = self.materials.preview('session-a', upload)
        self.assertEqual([row['status'] for row in preview['files']], ['blocked', 'extracted'])
        result = self.confirm(upload)
        self.assertEqual([row['status'] for row in result['materials']], ['failed', 'imported'])
        self.assertEqual(self.confirm(upload), result)

    def test_limits_names_manifest_hash_and_confirmation_binding(self):
        row = {'name': 'a', 'bytes': 1, 'sha256': '0' * 64}
        self.error('TOO_LARGE', self.materials.begin, 'session-a', None, 'x', [{**row, 'bytes': MAX_FILE + 1}])
        self.error('TOO_LARGE', self.materials.begin, 'session-a', None, 'x', [row] * 51)
        self.error('TOO_LARGE', self.materials.begin, 'session-a', None, 'x', [{**row, 'bytes': MAX_FILE}] * 6)
        for name in ('../x', '.env', 'private_key.pem', 'x\x00y', 'a/b', 'a\\b'):
            with self.assertRaises(StoreError):
                self.materials.begin('session-a', None, 'x', [{**row, 'name': name}])
        upload = self.upload([('a.txt', b'good')], op='same')
        self.error('OPERATION_CONFLICT', self.materials.begin, 'session-a', self.project, 'same', [row])
        other = self.projects.create('session-a', deepcopy(PROBLEM))['id']
        preview = self.materials.preview('session-a', upload)
        self.error('PROJECT_SCOPE', self.materials.confirm, 'session-a', upload, 'confirm', preview['preview_hash'], other)
        self.error('VERSION_CONFLICT', self.materials.confirm, 'session-a', upload, 'confirm', '0' * 64, self.project)
        self.confirm(upload)
        self.error('OPERATION_CONFLICT', self.materials.confirm, 'session-a', upload, 'changed', preview['preview_hash'], self.project)
        bad = self.materials.begin('session-a', None, 'hash', [row])['upload_id']
        self.materials.chunk('session-a', bad, '0', 0, base64.b64encode(b'a').decode())
        self.assertEqual(self.materials.preview('session-a', bad)['files'][0]['status'], 'blocked')

    def test_notebook_stored_text_outputs_and_text_truncation(self):
        notebook = canonical({'cells': [{'cell_type': 'code', 'source': ['raise RuntimeError("never execute")\n'],
                                         'outputs': [
                                             {'output_type': 'stream', 'name': 'stdout', 'text': ['saved ', 'stdout']},
                                             {'output_type': 'stream', 'name': 'stderr', 'text': 'saved stderr'},
                                             {'output_type': 'execute_result', 'data': {'text/plain': ['α + ', 'β'], 'text/html': '<script>FORBIDDEN_HTML</script>'}},
                                             {'output_type': 'display_data', 'data': {'text/html': 'FORBIDDEN_HTML', 'image/png': 'FORBIDDEN_IMAGE', 'application/vnd.jupyter.widget-view+json': {'model_id': 'FORBIDDEN_WIDGET'}}},
                                             {'output_type': 'error', 'traceback': ['FORBIDDEN_TRACEBACK']}]},
                                        {'cell_type': 'markdown', 'source': 'markdown α'}]})
        value = extract(notebook, 'example.ipynb')
        self.assertEqual(value['status'], 'extracted')
        for expected in ('raise RuntimeError', 'saved stdout', 'saved stderr', 'α + β', 'markdown α', '[cell 1 output 3 execute_result]'):
            self.assertIn(expected, value['text'])
        self.assertNotIn('FORBIDDEN', value['text'])
        self.assertFalse(value['truncated'])
        self.assertIn('Code was not executed', value['warnings'][0])
        self.assertTrue(extract(b'a' * 200001, 'long.txt')['truncated'])
        self.assertEqual(extract(b'\xff', 'bad.txt')['status'], 'failed')
        self.assertEqual(extract(b'broken', 'broken.pdf')['status'], 'failed')

    def test_notebook_exact_text_boundary_and_output_limits(self):
        # A cell exactly fills the budget: only omitted later content truncates it.
        cell = {'cell_type': 'code', 'source': 'a' * (200000 - len('[cell 1 code]\n\n'))}
        exact = extract(canonical({'cells': [cell]}), 'exact.ipynb')
        self.assertEqual(len(exact['text']), 200000)
        self.assertFalse(exact['truncated'])
        next_cell = extract(canonical({'cells': [cell, {'cell_type': 'markdown', 'source': 'lost'}]}), 'cells.ipynb')
        self.assertEqual(len(next_cell['text']), 200000)
        self.assertTrue(next_cell['truncated'])
        with_output = {**cell, 'outputs': [{'output_type': 'stream', 'text': 'lost output'}]}
        self.assertTrue(extract(canonical({'cells': [with_output]}), 'outputs.ipynb')['truncated'])
        output = {'output_type': 'stream', 'text': 'stored'}
        crowded = {'cell_type': 'code', 'source': '', 'outputs': [output] * 101}
        bounded = extract(canonical({'cells': [crowded]}), 'many.ipynb')
        self.assertTrue(bounded['truncated'])
        self.assertEqual(bounded['text'].count('stored'), 100)
        many_cells = [{**crowded, 'outputs': [output] * 100}] * 11
        total = extract(canonical({'cells': many_cells}), 'total.ipynb')
        self.assertTrue(total['truncated'])
        self.assertEqual(total['text'].count('stored'), 1000)
        self.assertNotIn('[cell 11 output', total['text'])
        self.assertTrue(extract(canonical({'cells': [{'cell_type': 'markdown', 'source': ''}] * 1001}), 'cells.ipynb')['truncated'])
        huge = {'cell_type': 'code', 'source': '', 'outputs': [{'output_type': 'stream', 'text': 'x' * 200001}]}
        bounded = extract(canonical({'cells': [huge]}), 'large.ipynb')
        self.assertEqual(len(bounded['text']), 200000)
        self.assertTrue(bounded['truncated'])

    def test_sensitive_content_and_filename_collision(self):
        upload = self.upload([('secret.txt', b'api_key=abc123'), ('good.txt', b'good')])
        self.assertEqual(self.materials.preview('session-a', upload)['files'][0]['status'], 'blocked')
        self.assertEqual([r['status'] for r in self.confirm(upload)['materials']], ['failed', 'imported'])
        collision = self.confirm(self.upload([('material-extraction.json', b'{}')]))['materials'][0]
        self.assertEqual(self.materials.read('session-a', self.project, collision['ref'])['text'], '{}')

    def test_begin_interruption_recovers_empty_staging(self):
        files = [{'name': 'a.txt', 'bytes': 1, 'sha256': hashlib.sha256(b'a').hexdigest()}]
        with patch.object(self.materials, '_save', side_effect=OSError('synthetic initialization stop')):
            with self.assertRaises(OSError):
                self.materials.begin('session-a', None, 'interrupted', files)
        recovered = self.materials.begin('session-a', None, 'interrupted', files)
        self.assertEqual(recovered, self.materials.begin('session-a', None, 'interrupted', files))

    def test_commit_interruption_recovers_without_duplicate(self):
        upload = self.upload([('one.txt', b'one'), ('two.txt', b'two')])
        self.materials.preview('session-a', upload)
        original = self.materials._save
        calls = 0
        def crash(path, state):
            nonlocal calls
            calls += 1
            if state['result'] is not None:
                raise OSError('synthetic receipt publication failure')
            return original(path, state)
        with patch.object(self.materials, '_save', side_effect=crash):
            with self.assertRaises(OSError):
                self.confirm(upload)
        result = self.confirm(upload)
        self.assertTrue(all(r['status'] == 'imported' for r in result['materials']))
        self.assertEqual(len([o for o in self.projects.overview(self.project)['objects'] if o['kind'] == 'Evidence']), 2)

    def test_expiry_cleanup_and_read_purity(self):
        upload = self.upload([('a.txt', b'text')])
        with patch('material_store.time.time', return_value=10**12):
            self.error('UPLOAD_EXPIRED', self.materials.preview, 'session-a', upload)
            self.materials.cleanup('session-a', upload)
        upload = self.upload([('a.txt', b'text')])
        ref = self.confirm(upload)['materials'][0]['ref']
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.materials.read('session-a', self.project, ref)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_binary_download_chunks_and_integrity(self):
        raw = b'\x00\xff' * (200 * 1024)
        ref = self.confirm(self.upload([('image.bin', raw)]))['materials'][0]['ref']
        first = dispatch(self.root, {'action': 'material-bytes', 'args':
                         {'session_id': 'session-a', 'project_id': self.project, 'ref': ref}})
        self.assertEqual(first['bytes'], len(raw))
        self.assertEqual(first['next_offset'], 384 * 1024)
        last = self.materials.bytes('session-a', self.project, ref, first['next_offset'])
        self.assertIsNone(last['next_offset'])
        self.assertEqual(base64.b64decode(first['base64']) + base64.b64decode(last['base64']), raw)
        self.error('PROJECT_SCOPE', self.materials.bytes, 'other', self.project, ref)
        self.error('TOO_LARGE', self.materials.bytes, 'session-a', self.project, ref, 0, 384 * 1024 + 1)
        view = self.projects.overview(self.project)
        obj = next(o for o in view['objects'] if o['id'] == ref['id'])
        target = self.projects.path(self.project) / 'research' / obj['data']['attachments'][0]['relative_path']
        target.write_bytes(b'corrupted synthetic original')
        with self.assertRaises(StoreError):
            self.materials.read('session-a', self.project, ref)
        with self.assertRaises(StoreError):
            self.materials.bytes('session-a', self.project, ref)

    def test_staging_symlinks_refused_without_touching_target(self):
        upload = self.upload([('a.txt', b'safe')])
        target = self.materials._path(upload) / 'chunk-0-0'
        target.unlink()
        outside = Path(self.tmp.name) / 'outside.txt'
        outside.write_bytes(b'safe')
        target.symlink_to(outside)
        self.error('UNSAFE_PATH', self.materials.cleanup, 'session-a', upload)
        preview = self.materials.preview('session-a', upload)
        self.assertEqual(preview['files'][0]['status'], 'blocked')
        self.assertEqual(outside.read_bytes(), b'safe')

    def test_configurable_limits_are_bounded_and_returned(self):
        with patch.dict('os.environ', {'DSH_MATH_MATERIAL_MAX_FILE_BYTES': '2',
                                      'DSH_MATH_MATERIAL_MAX_BATCH_BYTES': '3',
                                      'DSH_MATH_MATERIAL_MAX_FILES': '2'}):
            materials = Materials(self.root)
            files = [{'name': 'a.txt', 'bytes': 2, 'sha256': hashlib.sha256(b'aa').hexdigest()}]
            result = materials.begin('session-a', None, 'limited', files)
            self.assertEqual(result['limits'], {'file_bytes': 2, 'batch_bytes': 3, 'files': 2, 'chunk_bytes': CHUNK})
            self.error('TOO_LARGE', materials.begin, 'session-a', None, 'large-file', [{**files[0], 'bytes': 3}])
            self.error('TOO_LARGE', materials.begin, 'session-a', None, 'large-batch', files * 2)
            self.error('TOO_LARGE', materials.begin, 'session-a', None, 'many-files', files * 3)
        for name, values in [('DSH_MATH_MATERIAL_MAX_FILE_BYTES', ['0', '-1', '104857601', '1.5', 'abc']),
                             ('DSH_MATH_MATERIAL_MAX_BATCH_BYTES', ['524288001']),
                             ('DSH_MATH_MATERIAL_MAX_FILES', ['201'])]:
            for value in values:
                with patch.dict('os.environ', {name: value}):
                    self.error('INVALID_CONFIG', Materials, self.root)

    def test_dispatch_strict_arguments(self):
        self.error('INVALID_SCHEMA', dispatch, self.root, {'action': 'material-read', 'args': {'session_id': 'session-a'}})
        begin = dispatch(self.root, {'action': 'material-begin', 'args': {'session_id': 'session-a', 'project_id': None,
                         'operation_id': 'empty', 'files': [{'name': 'empty.txt', 'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}]}})
        preview = dispatch(self.root, {'action': 'material-preview', 'args': {'session_id': 'session-a', 'upload_id': begin['upload_id']}})
        self.assertEqual(preview['files'][0]['text'], '')
        repeat = dispatch(self.root, {'action': 'material-begin', 'args': {'session_id': 'session-a', 'project_id': None,
                         'operation_id': 'empty', 'files': [{'name': 'empty.txt', 'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}]}})
        self.assertEqual(repeat, begin)
        result = self.materials.confirm('session-a', begin['upload_id'], 'confirm-empty', preview['preview_hash'], self.project)
        ref = result['materials'][0]['ref']
        self.assertIsNotNone(ref)
        self.assertEqual(self.materials.read('session-a', self.project, ref)['text'], '')
        downloaded = self.materials.bytes('session-a', self.project, ref)
        self.assertEqual((downloaded['base64'], downloaded['bytes'], downloaded['next_offset']), ('', 0, None))
        self.error('INVALID_SCHEMA', self.materials.bytes, 'session-a', self.project, ref, 1)


if __name__ == '__main__':
    unittest.main()

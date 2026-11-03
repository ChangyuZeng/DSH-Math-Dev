"""Session-bound material uploads. Data only; confirmation is a human operation.

Original bytes and bounded extraction metadata are ordinary schema4 Evidence
attachments. Staging is never a research object and grants no execution authority.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid
import zipfile

from backend import HERE, Projects, checked_root, make_directory, write_bytes_new, write_new
from exporting import guard_content
from research_store import read_regular, safe_path, sync_directory
from schema import StoreError, canonical, decode, digest, fields, identifier, integer, ref as check_ref, require, sha

MAX_FILE = 20 * 1024 * 1024
MAX_BATCH = 100 * 1024 * 1024
CHUNK = 512 * 1024
MAX_FILES = 50
MAX_TEXT = 200000
TTL = 24 * 60 * 60
LIMITS = {"file_bytes": MAX_FILE, "batch_bytes": MAX_BATCH, "files": MAX_FILES, "chunk_bytes": CHUNK}
TEXT_EXTENSIONS = {"txt", "md", "markdown", "tex", "bib", "json", "csv", "tsv", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "r", "jl", "sh", "yaml", "yml", "toml", "xml", "html", "css", "sql", "log", "rst", "c", "h", "cpp", "rs", "go", "java", "lean", "v", "m", "sage"}


def filename(name):
    require(type(name) is str and 0 < len(name.encode('utf-8')) <= 255 and name not in ('.', '..')
            and '/' not in name and '\\' not in name and not any(ord(c) < 32 for c in name),
            'UNSAFE_PATH', 'Expected a plain bounded filename')
    require(not name.lower().startswith('.env') and not any(word in name.lower() for word in
            ('credential', 'oauth', 'auth.json', 'private_key')), 'SENSITIVE_CONTENT', 'Unsafe attachment name')
    return name


def extract(raw, name):
    """Bounded literal text extraction. Code/notebook outputs are never executed."""
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    value = {"format": ext, "status": "archive_only", "text": "", "truncated": False, "warnings": []}
    try:
        if ext in TEXT_EXTENSIONS or ext == 'ipynb':
            text = raw.decode('utf-8-sig')
            require('\x00' not in text, 'UNSUPPORTED', 'Binary content in text file')
            if ext == 'ipynb':
                notebook = decode(text)
                require(type(notebook) is dict and type(notebook.get('cells')) is list,
                        'INVALID_SCHEMA', 'Expected notebook cells')
                parts, count, output_count = [], 0, 0
                value['truncated'] = len(notebook['cells']) > 1000

                def literal(item):
                    require(type(item) is str or (type(item) is list and all(type(s) is str for s in item)),
                            'INVALID_SCHEMA', 'Invalid notebook text')
                    return item if type(item) is str else ''.join(item)

                def append(part):
                    nonlocal count
                    remaining = MAX_TEXT - count
                    value['truncated'] = value['truncated'] or len(part) > remaining
                    parts.append(part[:remaining])
                    count += min(len(part), remaining)

                for cell_index, cell in enumerate(notebook['cells'][:1000], 1):
                    require(type(cell) is dict and cell.get('cell_type') in ('markdown', 'code', 'raw'),
                            'INVALID_SCHEMA', 'Invalid notebook cell')
                    append('[cell ' + str(cell_index) + ' ' + cell['cell_type'] + ']\n'
                           + literal(cell.get('source', '')) + '\n')
                    outputs = cell.get('outputs', []) if cell['cell_type'] == 'code' else []
                    require(type(outputs) is list, 'INVALID_SCHEMA', 'Invalid notebook outputs')
                    allowance = min(100, 1000 - output_count)
                    value['truncated'] = value['truncated'] or len(outputs) > allowance
                    for output_index, output in enumerate(outputs[:allowance], 1):
                        output_count += 1
                        require(type(output) is dict, 'INVALID_SCHEMA', 'Invalid notebook output')
                        kind = output.get('output_type')
                        if kind == 'stream':
                            output_text = literal(output.get('text', ''))
                        elif kind in ('display_data', 'execute_result'):
                            data = output.get('data', {})
                            require(type(data) is dict, 'INVALID_SCHEMA', 'Invalid notebook output data')
                            output_text = literal(data.get('text/plain', ''))
                        else:
                            continue
                        if output_text:
                            append('[cell ' + str(cell_index) + ' output ' + str(output_index) + ' '
                                   + kind + ']\n' + output_text + '\n')
                text = ''.join(parts)
                value['warnings'].append('Notebook stored sources, stream text, and text/plain only; HTML, widgets, images, and execution metadata omitted. Code was not executed.')
            value.update(status='extracted', text=text[:MAX_TEXT], truncated=value['truncated'] or len(text) > MAX_TEXT)
        elif ext in ('pdf', 'docx'):
            if ext == 'docx':
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    infos = archive.infolist()
                    require(len(infos) <= 5000 and sum(item.file_size for item in infos) <= 50 * 1024 * 1024,
                            'TOO_LARGE', 'DOCX expanded content exceeds extraction budget')
                    require(all(not item.flag_bits & 1 for item in infos), 'UNSUPPORTED', 'Encrypted DOCX')
            completed = subprocess.run([os.environ.get('DSH_MATH_NODE', 'node'), '--max-old-space-size=256',
                                        str(HERE / 'material_extract.mjs'), name], input=raw,
                                       capture_output=True, timeout=40, cwd=HERE)
            require(completed.returncode == 0 and len(completed.stdout) <= 2 * 1024 * 1024,
                    'EXTRACTION_FAILED', 'Extractor failed or exceeded output limit')
            value = decode(completed.stdout)
            fields(value, 'format status text truncated warnings')
            require(value['status'] in ('extracted', 'archive_only', 'failed') and type(value['text']) is str
                    and len(value['text']) <= MAX_TEXT and type(value['truncated']) is bool
                    and type(value['warnings']) is list and all(type(w) is str for w in value['warnings']),
                    'EXTRACTION_FAILED', 'Invalid extractor response')
        else:
            value['warnings'].append('Unsupported format; original archived without extraction.')
        guard_content(value['text'].encode('utf-8'))
    except (OSError, ValueError, StoreError, subprocess.SubprocessError, zipfile.BadZipFile, UnicodeError) as error:
        if isinstance(error, StoreError) and error.code == 'SENSITIVE_CONTENT':
            raise
        value = {"format": ext, "status": "failed", "text": "", "truncated": False,
                 "warnings": ['Extraction failed; original can still be archived.']}
    return value


class Materials:
    def __init__(self, root):
        self.root = checked_root(root)
        self.projects = Projects(root)
        self.directory = safe_path(self.root, 'materials')
        self.limits = dict(LIMITS)
        for key, env, ceiling in (
                ('file_bytes', 'DSH_MATH_MATERIAL_MAX_FILE_BYTES', 100 * 1024 * 1024),
                ('batch_bytes', 'DSH_MATH_MATERIAL_MAX_BATCH_BYTES', 500 * 1024 * 1024),
                ('files', 'DSH_MATH_MATERIAL_MAX_FILES', 200)):
            raw = os.environ.get(env)
            if raw is not None:
                require(raw.isascii() and raw.isdigit() and 0 < len(raw) <= 10,
                        'INVALID_CONFIG', env + ' must be a bounded positive integer')
                value = int(raw)
                require(0 < value <= ceiling, 'INVALID_CONFIG', env + ' exceeds its supported range')
                self.limits[key] = value

    @contextmanager
    def _locked(self):
        make_directory(self.directory)
        path = safe_path(self.directory, '.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'UNSAFE_PATH', 'Unsafe material lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StoreError('LOCKED', 'Material store is in use; retry the same request') from error
            yield
        finally:
            os.close(fd)

    def _path(self, upload_id):
        sha(upload_id)
        return safe_path(self.directory, 'staging/' + upload_id)

    def _save(self, path, value):
        temporary = safe_path(path, '.state-' + str(uuid.uuid4()))
        write_new(temporary, value)
        destination = safe_path(path, 'state.json')
        if destination.exists():
            read_regular(destination)
        os.replace(temporary, destination)
        sync_directory(path)

    def _load(self, session_id, upload_id, *, expired=False):
        identifier(session_id)
        path = self._path(upload_id)
        state = decode(read_regular(safe_path(path, 'state.json')))
        require(state['session_id'] == session_id, 'PROJECT_SCOPE', 'Upload belongs to another session')
        require(expired or time.time() - state['created_at'] <= TTL, 'UPLOAD_EXPIRED', 'Upload expired; clean up and start a new upload')
        return path, state

    def _project(self, session_id, project_id, *, writable=False):
        identifier(session_id)
        metadata = self.projects.metadata(project_id)
        from project_access import ProjectAccess
        with ProjectAccess(self.root).authorized(metadata, session_id):
            pass
        if writable:
            require(metadata['schema_version'] == 4 and metadata.get('mode') == 'daily_research',
                    'READ_ONLY', 'Material import requires a schema4 daily project')
        return metadata

    def begin(self, session_id, project_id, operation_id, files):
        identifier(session_id)
        identifier(operation_id)
        if project_id is not None:
            self._project(session_id, project_id, writable=True)
        require(type(files) is list and 1 <= len(files) <= self.limits['files'], 'TOO_LARGE', 'Batch exceeds configured file count')
        total = 0
        rows = []
        for index, item in enumerate(files):
            fields(item, 'name bytes sha256')
            filename(item['name'])
            integer(item['bytes'])
            sha(item['sha256'])
            require(item['bytes'] <= self.limits['file_bytes'], 'TOO_LARGE', 'File exceeds configured byte limit')
            total += item['bytes']
            rows.append({'file_id': str(index), **item})
        require(total <= self.limits['batch_bytes'], 'TOO_LARGE', 'Batch exceeds configured byte limit')
        upload_id = digest({'session_id': session_id, 'operation_id': operation_id})
        request = {'project_id': project_id, 'files': rows}
        with self._locked():
            path = self._path(upload_id)
            if path.exists() and safe_path(path, 'state.json').exists():
                _, state = self._load(session_id, upload_id)
                require(state['request'] == request, 'OPERATION_CONFLICT', 'Upload operation reused with different files')
                require(not state.get('cleaned'), 'UPLOAD_CLEANED', 'Upload was cleaned')
            else:
                make_directory(path)
                # A killed initializer may leave an empty directory or unpublished
                # state temporary files; no chunk can exist before state publication.
                for child in path.iterdir():
                    checked_root(child)
                    require(child.name.startswith('.state-'), 'CORRUPT_SIDECAR', 'Upload has bytes but no manifest')
                    read_regular(child)
                state = {'session_id': session_id, 'created_at': time.time(), 'request': request,
                         'preview': None, 'confirmation': None, 'result': None, 'cleaned': False}
                self._save(path, state)
        return {'upload_id': upload_id, 'files': rows, 'limits': dict(self.limits)}

    def chunk(self, session_id, upload_id, file_id, index, base64):
        integer(index)
        require(type(base64) is str and len(base64) <= 4 * math.ceil(CHUNK / 3), 'TOO_LARGE', 'Chunk exceeds 512 KiB')
        try:
            raw = __import__('base64').b64decode(base64, validate=True)
        except ValueError as error:
            raise StoreError('INVALID_SCHEMA', 'Invalid base64 chunk') from error
        with self._locked():
            path, state = self._load(session_id, upload_id)
            require(not state['cleaned'] and state['confirmation'] is None, 'UPLOAD_CLOSED', 'Upload is closed')
            row = next((item for item in state['request']['files'] if item['file_id'] == file_id), None)
            require(row is not None, 'NOT_FOUND', 'Unknown upload file')
            expected = min(CHUNK, row['bytes'] - index * CHUNK)
            require(expected > 0 and len(raw) == expected, 'INVALID_SCHEMA', 'Chunk index or size differs from declared file')
            target = safe_path(path, 'chunk-' + file_id + '-' + str(index))
            if target.exists():
                require(read_regular(target) == raw, 'OPERATION_CONFLICT', 'Chunk retry changed bytes')
            else:
                require(state['preview'] is None, 'UPLOAD_CLOSED', 'Preview freezes upload bytes')
                write_bytes_new(target, raw)
        return {'file_id': file_id, 'index': index, 'received_bytes': len(raw)}

    def _bytes(self, path, row):
        parts = []
        for index in range(math.ceil(row['bytes'] / CHUNK)):
            target = safe_path(path, 'chunk-' + row['file_id'] + '-' + str(index))
            require(target.exists(), 'UPLOAD_INCOMPLETE', 'Missing upload chunk ' + str(index))
            parts.append(read_regular(target))
        raw = b''.join(parts)
        require(len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256'],
                'ATTACHMENT_INTEGRITY', 'Uploaded size/hash differs from declaration')
        return raw

    @staticmethod
    def _existing(objects, sha256):
        for versions in objects.values():
            obj = versions[-1]
            if (obj['kind'] == 'Evidence' and obj['data']['source']['locator'] == 'material:' + sha256
                    and obj['data']['production']['method'] == 'dsh-math-material-import'):
                return {'id': obj['id'], 'rev': obj['rev']}
        return None

    def preview(self, session_id, upload_id):
        with self._locked():
            path, state = self._load(session_id, upload_id)
            require(not state['cleaned'], 'UPLOAD_CLOSED', 'Upload is closed')
            project_id = state['request']['project_id']
            if project_id is not None:
                self._project(session_id, project_id, writable=True)
            if state['preview'] is not None:
                return state['preview']
            objects = {}
            if project_id is not None:
                with self.projects.open_project(project_id, session_id=session_id, chat=True, require_writable=True) as (_, store):
                    objects = store.snapshot()['state']['objects']
            rows = []
            for row in state['request']['files']:
                item = {**row, 'existing_ref': self._existing(objects, row['sha256'])}
                try:
                    raw = self._bytes(path, row)
                    guard_content(raw)
                    value = extract(raw, row['name'])
                    # A sensitive extracted string blocks import; never archive it silently.
                    guard_content(value['text'].encode('utf-8'))
                    item.update(value)
                except (StoreError, OSError, ValueError) as error:
                    item.update(status='blocked', format=row['name'].rsplit('.', 1)[-1].lower(), text='', truncated=False,
                                warnings=[str(error)], error={'code': getattr(error, 'code', 'UPLOAD_FAILED'), 'message': str(error)})
                rows.append(item)
            preview = {'upload_id': upload_id, 'project_id': project_id, 'files': rows}
            preview['preview_hash'] = digest(preview)
            state['preview'] = preview
            self._save(path, state)
            return preview

    def confirm(self, session_id, upload_id, operation_id, preview_hash, project_id, skip_duplicates=True):
        identifier(operation_id)
        sha(preview_hash)
        require(type(skip_duplicates) is bool, 'INVALID_SCHEMA', 'Expected skip_duplicates boolean')
        self._project(session_id, project_id, writable=True)
        with self._locked():
            path, state = self._load(session_id, upload_id)
            require(not state['cleaned'], 'UPLOAD_CLOSED', 'Upload is closed')
            require(state['request']['project_id'] in (None, project_id), 'PROJECT_SCOPE', 'Upload was bound to another project')
            preview = state['preview']
            require(preview is not None and preview['preview_hash'] == preview_hash, 'VERSION_CONFLICT', 'Inspect this exact material preview before confirming')
            request = {'operation_id': operation_id, 'preview_hash': preview_hash, 'project_id': project_id, 'skip_duplicates': skip_duplicates}
            require(state['confirmation'] in (None, request), 'OPERATION_CONFLICT', 'Confirmation operation changed')
            if state['result'] is not None:
                return state['result']
            state['confirmation'] = request
            self._save(path, state)  # Freeze before the first formal journal mutation.
            results = []
            with self.projects.open_project(project_id, writable=True, session_id=session_id, chat=True,
                    actor={'kind': 'human', 'id': 'local-user'},
                    origin={'source': 'dsh-math-human', 'session_id': session_id, 'run_id': None, 'tool_call_id': None}) as (_, store):
                for item in preview['files']:
                    result = {key: item[key] for key in ('file_id', 'name', 'sha256')}
                    result['ref'] = None
                    try:
                        require(item['status'] != 'blocked', 'UPLOAD_BLOCKED', '; '.join(item['warnings']))
                        raw = self._bytes(path, item)
                        guard_content(raw)
                        objects = store.snapshot()['state']['objects']
                        object_id = 'MAT-' + digest({'upload_id': upload_id, 'file_id': item['file_id'], 'operation_id': operation_id})[:48]
                        # Recover a commit that became durable before its receipt.
                        if object_id in objects:
                            result.update(ref={'id': object_id, 'rev': 1}, status='imported')
                        else:
                            existing = self._existing(objects, item['sha256']) if skip_duplicates else None
                            if existing:
                                result.update(ref=existing, status='duplicate')
                            else:
                                extraction = {key: item[key] for key in ('format', 'status', 'text', 'truncated', 'warnings')}
                                extraction.update(original_sha256=item['sha256'], name=item['name'], format_version=1)
                                attachments = [store._archive_bytes(raw, item['name']),
                                               store._archive_bytes(canonical(extraction), 'material-extraction.json')]
                                problem = objects['P'][-1]
                                data = {'evidence_type': 'source_excerpt', 'problem_ref': {'id': problem['id'], 'rev': problem['rev']},
                                        'attachments': attachments,
                                        'source': {'locator': 'material:' + item['sha256'], 'version': None, 'accessed_at': None, 'conditions': []},
                                        'source_status': 'unverified_source', 'support_scope': 'Imported material for reading and preparation only.',
                                        'limits': ['Unverified source; extraction may omit layout, formulas, images or content.', 'No mathematical claim or execution authority is granted.'],
                                        'production': {'method': 'dsh-math-material-import', 'version': '1'}, 'citation': None}
                                store.commit('material-' + digest({'upload': upload_id, 'file': item['file_id'], 'op': operation_id}),
                                             [{'kind': 'Evidence', 'id': object_id, 'expected_rev': 0, 'data': data}])
                                result.update(ref={'id': object_id, 'rev': 1}, status='imported')
                    except (StoreError, OSError, ValueError) as error:
                        result.update(status='failed', error={'code': getattr(error, 'code', 'IMPORT_FAILED'), 'message': str(error)})
                    results.append(result)
            value = {'upload_id': upload_id, 'project_id': project_id, 'operation_id': operation_id, 'materials': results}
            state['result'] = value
            self._save(path, state)
            return value

    def cleanup(self, session_id, upload_id):
        with self._locked():
            path, state = self._load(session_id, upload_id, expired=True)
            # Verify every resolved deletion target before removing staging bytes.
            for child in list(path.iterdir()):
                checked_root(child)
                require(child.parent == path, 'UNSAFE_PATH', 'Invalid staging cleanup target')
                read_regular(child)
                if child.name != 'state.json':
                    child.unlink()
            state['cleaned'] = True
            state['preview'] = None
            self._save(path, state)
        return {'upload_id': upload_id, 'cleaned': True}

    def read(self, session_id, project_id, ref, offset=0, limit=12000):
        check_ref(ref)
        integer(offset)
        integer(limit, 1)
        require(limit <= 12000, 'TOO_LARGE', 'Read at most 12000 characters')
        metadata = self._project(session_id, project_id)
        require(metadata['schema_version'] == 4, 'READ_ONLY', 'Materials require schema4')
        with self.projects.open_project(project_id, session_id=session_id, model=True, chat=True) as (_, store):
            obj = store.get(ref['id'], ref['rev'])
            data = obj['data']
            require(obj['kind'] == 'Evidence' and data['production'] == {'method': 'dsh-math-material-import', 'version': '1'},
                    'NOT_MATERIAL', 'Exact Evidence revision is not an imported material')
            descriptor = next((a for a in reversed(data['attachments']) if a['logical_name'] == 'material-extraction.json'), None)
            require(descriptor is not None, 'ATTACHMENT_INTEGRITY', 'Missing extraction metadata')
            raw = read_regular(safe_path(store.root, descriptor['relative_path']))
            require(len(raw) == descriptor['bytes'] and hashlib.sha256(raw).hexdigest() == descriptor['sha256'],
                    'ATTACHMENT_INTEGRITY', 'Extraction metadata changed')
            value = decode(raw)
            require(value.get('format_version') == 1 and type(value.get('text')) is str and len(value['text']) <= MAX_TEXT,
                    'INVALID_SCHEMA', 'Invalid extraction metadata')
            require(data['source']['locator'] == 'material:' + value['original_sha256']
                    and any(a['sha256'] == value['original_sha256'] for a in data['attachments']),
                    'ATTACHMENT_INTEGRITY', 'Original does not match material metadata')
            text = value['text']
            require(offset <= len(text), 'INVALID_SCHEMA', 'Read offset exceeds extracted text')
            end = min(offset + limit, len(text))
            from library_store import Library
            library = Library(self.root).metadata(project_id, {'kind': 'object', 'id': ref['id']})
            return {'ref': ref, 'name': value['name'], 'sha256': value['original_sha256'], 'status': value['status'],
                    'library': library,
                    'text': text[offset:end], 'offset': offset, 'next_offset': end if end < len(text) else None,
                    'total_chars': len(text), 'truncated': value['truncated'], 'warnings': value['warnings']}

    def bytes(self, session_id, project_id, ref, offset=0, limit=384 * 1024):
        integer(offset)
        integer(limit, 1)
        require(limit <= 384 * 1024, 'TOO_LARGE', 'Download at most 384 KiB per request')
        material = self.read(session_id, project_id, ref, 0, 1)
        with self.projects.open_project(project_id, session_id=session_id, model=True, chat=True) as (_, store):
            obj = store.get(ref['id'], ref['rev'])
            descriptor = next(a for a in obj['data']['attachments'] if a['sha256'] == material['sha256'])
            raw = read_regular(safe_path(store.root, descriptor['relative_path']))
            require(len(raw) == descriptor['bytes'] and hashlib.sha256(raw).hexdigest() == material['sha256'],
                    'ATTACHMENT_INTEGRITY', 'Original material bytes changed')
            require(offset <= len(raw), 'INVALID_SCHEMA', 'Download offset exceeds original bytes')
            end = min(offset + limit, len(raw))
            return {'name': material['name'], 'bytes': len(raw), 'sha256': material['sha256'],
                    'base64': base64.b64encode(raw[offset:end]).decode('ascii'), 'offset': offset,
                    'next_offset': end if end < len(raw) else None}


def dispatch_material(root, action, args):
    store = Materials(root)
    if action == 'material-begin':
        fields(args, 'session_id project_id operation_id files')
        return store.begin(**args)
    if action == 'material-chunk':
        fields(args, 'session_id upload_id file_id index base64')
        return store.chunk(**args)
    if action == 'material-preview':
        fields(args, 'session_id upload_id')
        return store.preview(**args)
    if action == 'material-confirm':
        args = {'skip_duplicates': True, **args}
        fields(args, 'session_id upload_id operation_id preview_hash project_id skip_duplicates')
        return store.confirm(**args)
    if action == 'material-cleanup':
        fields(args, 'session_id upload_id')
        return store.cleanup(**args)
    if action == 'material-bytes':
        args = {'offset': 0, 'limit': 384 * 1024, **args}
        fields(args, 'session_id project_id ref offset limit')
        return store.bytes(**args)
    if action == 'material-read':
        args = {'offset': 0, 'limit': 12000, **args}
        fields(args, 'session_id project_id ref offset limit')
        return store.read(**args)
    raise StoreError('FORBIDDEN', 'Unknown material action')

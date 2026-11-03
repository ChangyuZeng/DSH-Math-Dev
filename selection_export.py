"""Bounded, selected readable exports. No journal, scratchpad, or model execution."""
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile

from backend import HERE, Projects, checked_root, make_directory, write_bytes_new
from exporting import guard_content
from research_store import read_regular, safe_path
from schema import canonical, fields, ref as check_ref, require

FORMAT = 'dsh-math-selection-v1'
MAX_OBJECTS = 500
MAX_BYTES = 100 * 1024 * 1024
CHUNK = 384 * 1024


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _key(ref):
    check_ref(ref)
    return ref['id'], ref['rev']


def _name(text):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '-', str(text)).strip(' .')
    text = re.sub(r'\s+', ' ', text)[:70].strip(' .')
    return text if text and not text.startswith('.') else 'Untitled'


def _refs(value):
    if type(value) is dict:
        if set(value) == {'id', 'rev'}:
            yield value
        else:
            for child in value.values():
                yield from _refs(child)
    elif type(value) is list:
        for child in value:
            yield from _refs(child)


def _title(obj, metadata):
    return metadata.get('title') or obj['data'].get('title') or obj['data'].get('statement') or obj['data'].get('scope') or obj['kind']


def export_selection(projects, project_id, expected_seq, expected_library_version, selected_refs,
                     material_refs=None, include_material_originals=False, include_dependencies=False,
                     include_process=False, include_summary=True, session_id=None):
    from library_store import Library
    require(type(expected_seq) is int and expected_seq >= 0, 'INVALID_SCHEMA', 'Expected research sequence')
    require(type(expected_library_version) is dict, 'INVALID_SCHEMA', 'Expected library version')
    fields(expected_library_version, 'seq head_hash')
    require(all(type(v) is bool for v in (include_material_originals, include_dependencies, include_process, include_summary)),
            'INVALID_SCHEMA', 'Export options must be booleans')
    material_refs = [] if material_refs is None else material_refs
    require(type(selected_refs) is list and type(material_refs) is list and 0 < len(selected_refs) + len(material_refs) <= MAX_OBJECTS,
            'INVALID_SCHEMA', 'Select 1–500 exact revisions')
    requested = [_key(r) for r in selected_refs + material_refs]
    require(len(requested) == len(set(requested)), 'INVALID_SCHEMA', 'Duplicate selected revision')
    material_keys = {_key(r) for r in material_refs}
    library = Library(projects.root)
    with projects.open_project(project_id, session_id=session_id, chat=session_id is not None) as (project, store):
        snapshot = store.snapshot()
        require(snapshot['usable'] and snapshot['state']['seq'] == expected_seq, 'VERSION_CONFLICT', 'Refresh research before exporting')
        sidecar = library.snapshot(project_id)
        require(sidecar['version'] == expected_library_version, 'VERSION_CONFLICT', 'Refresh library before exporting')
        require(not sidecar.get('project', {}).get('removed', False), 'EXPORT_SELECTION', 'Restore project before exporting')
        objects = {(o['id'], o['rev']): o for versions in snapshot['state']['objects'].values() for o in versions}
        metadata = {key.removeprefix('object:'): value for key, value in sidecar['entries'].items() if key.startswith('object:')}
        def visible(key):
            meta = metadata.get(key[0], {})
            return key in objects and not meta.get('removed', False) and not meta.get('private', False)
        for key in requested:
            require(visible(key), 'EXPORT_SELECTION', 'Selected revision is missing, private, or removed')
        included = dict.fromkeys(requested)
        if include_dependencies:
            pending = list(requested)
            while pending:
                key = pending.pop()
                for value in _refs(objects[key]['data']):
                    target = _key(value)
                    if visible(target) and target not in included:
                        require(len(included) < MAX_OBJECTS, 'TOO_LARGE', 'Dependency selection exceeds 500 revisions')
                        included[target] = None
                        pending.append(target)
        labels = {key: _title(objects[key], metadata.get(key[0], {}))[:300] for key in included}
        names = {key: f'records/{index:03d} - {_name(labels[key])}.md' for index, key in enumerate(included, 1)}
        omissions, links = [], []
        def render(value, source):
            if type(value) is dict:
                if set(value) == {'id', 'rev'}:
                    key = _key(value)
                    if key in included:
                        links.append((source, key))
                        return {'ref': value, 'title': labels[key], 'file': names[key]}
                    public = visible(key)
                    marker = {'status': 'omitted', 'reason': 'not_selected' if public else 'unavailable'}
                    if public:
                        marker.update(ref=value, title=(metadata.get(key[0], {}).get('title') or objects[key]['data'].get('title') or objects[key]['kind'])[:300])
                    omissions.append({'from': {'id': source[0], 'rev': source[1]}, **marker})
                    return marker
                # Attachment storage paths never leave the store in a readable export.
                return {k: render(v, source) for k, v in value.items() if k != 'relative_path'}
            if type(value) is list:
                return [render(v, source) for v in value]
            return value
        payloads, records = {}, []
        for key in included:
            obj = objects[key]
            data = render(obj['data'], key)
            if key in material_keys:
                require(obj['kind'] == 'Evidence' and obj['data'].get('production', {}).get('method') == 'dsh-math-material-import',
                        'EXPORT_SELECTION', 'Material selection must identify an imported material revision')
                descriptor = next((a for a in obj['data']['attachments'] if a['logical_name'] == 'material-extraction.json'), None)
                require(descriptor is not None and descriptor['bytes'] <= 2 * 1024 * 1024, 'ATTACHMENT_INTEGRITY', 'Material extraction metadata missing or too large')
                raw = read_regular(safe_path(projects.path(project_id) / 'research', descriptor['relative_path']))
                require(len(raw) == descriptor['bytes'] and _hash(raw) == descriptor['sha256'], 'ATTACHMENT_INTEGRITY', 'Material extraction changed')
                extraction = json.loads(raw)
                require(type(extraction.get('text')) is str and len(extraction['text']) <= 200000, 'ATTACHMENT_INTEGRITY', 'Invalid bounded material extraction')
                data['material_text'] = extraction['text']
                data['material_extraction_status'] = extraction.get('status')
                data['material_text_truncated'] = extraction.get('truncated', False)
            exact = {'id': key[0], 'rev': key[1]}
            records.append({'ref': exact, 'kind': obj['kind'], 'title': labels[key], 'file': names[key],
                            'selected': key in requested, 'data': data})
            lines = ['# ' + labels[key], '', f"{obj['kind']} · revision {key[1]}", '', 'Exact reference: ' + key[0] + '@' + str(key[1]), '']
            for field, value in data.items():
                lines.extend(['## ' + field.replace('_', ' '), '', value if type(value) is str else json.dumps(value, ensure_ascii=False, indent=2), ''])
            payloads[names[key]] = '\n'.join(lines).encode()
            require(sum(map(len, payloads.values())) <= MAX_BYTES, 'TOO_LARGE', 'Selection exceeds 100 MiB')
        for key in material_keys:
            require(objects[key]['kind'] == 'Evidence' and objects[key]['data'].get('production', {}).get('method') == 'dsh-math-material-import',
                    'EXPORT_SELECTION', 'Material selection must identify an imported material revision')
        if include_material_originals:
            for index, key in enumerate(included, 1):
                if key not in material_keys:
                    continue
                for number, attachment in enumerate(objects[key]['data'].get('attachments', []), 1):
                    if attachment['logical_name'] == 'material-extraction.json':
                        continue
                    require(type(attachment['bytes']) is int and 0 <= attachment['bytes'] <= MAX_BYTES - sum(map(len, payloads.values())),
                            'TOO_LARGE', 'Material exceeds export budget')
                    raw = read_regular(safe_path(projects.path(project_id) / 'research', attachment['relative_path']))
                    require(len(raw) == attachment['bytes'] and _hash(raw) == attachment['sha256'], 'ATTACHMENT_INTEGRITY', 'Material changed')
                    payloads[f'materials/{index:03d}-{number:02d} - {_name(attachment["logical_name"])}'] = raw
        if include_summary:
            summary = sidecar.get('project', {})
            title = summary.get('title') or project.get('title') or 'Research project'
            payloads['Project summary.md'] = ('# ' + title + '\n\n' + (summary.get('summary') or '') +
                '\n\nSelected content only. This summary does not include unselected research or private scratchpads.\n').encode()
        payloads['selection.json'] = canonical({'records': records, 'omissions': omissions}) + b'\n'
        if include_process:
            outline = ['# Selected process', '', 'Selection diagram only; not a proof or full project history.', '']
            rows = []
            for index, key in enumerate(included):
                label = labels[key][:90]
                outline.append('- ' + label)
                rows.append(f'<text x="20" y="{35 + index * 32}" font-size="14">{html.escape(label)}</text>')
            for source, target in sorted(set(links)):
                outline.append('- ' + labels[source] + ' → ' + labels[target])
                a, b = list(included).index(source), list(included).index(target)
                rows.append(f'<path d="M 760 {30 + a * 32} Q 800 {30 + a * 32} 760 {30 + b * 32}" fill="none" stroke="#64748b"/>')
            payloads['process/outline.md'] = '\n'.join(outline).encode()
            payloads['process/process.svg'] = (f'<svg xmlns="http://www.w3.org/2000/svg" width="820" height="{max(90, len(included)*32+30)}" viewBox="0 0 820 {max(90, len(included)*32+30)}"><rect width="100%" height="100%" fill="white"/><g fill="#172033">' + ''.join(rows) + '</g></svg>').encode()
        payloads['README.md'] = ('# Selected research export\n\nOpen Project summary.md and records/ for readable content.\n'
            'Unselected dependencies are names and omission markers only. Removed/private references are unnamed.\n'
            'Original materials are included only when explicitly selected and requested. No private scratchpads, sessions, or journal are exported.\n'
            'Exact revisions and snapshot versions are recorded in manifest.json. This is a selected reading package, not the full audit export.\n'
            'R1/R2 are not granted by this package. Program results, local_claim, and process diagrams are not global proofs.\n'
            'After extraction and relocation run: python3 -B verify_bundle.py .\n').encode()
        payloads['verify_bundle.py'] = read_regular(HERE / 'verify_selection.py')
        require(sum(map(len, payloads.values())) <= MAX_BYTES, 'TOO_LARGE', 'Selection exceeds 100 MiB')
        for name, raw in payloads.items():
            if name != 'verify_bundle.py':
                guard_content(raw)
        require(library.snapshot(project_id)['version'] == expected_library_version, 'VERSION_CONFLICT', 'Library changed during export')
        manifest = {'format': FORMAT, 'captured_seq': expected_seq, 'library_version': expected_library_version,
                    'head_hash': snapshot['state']['head_hash'], 'selected_refs': selected_refs, 'material_refs': material_refs,
                    'options': {'include_dependencies': include_dependencies, 'include_material_originals': include_material_originals,
                                'include_process': include_process, 'include_summary': include_summary},
                    'files': {name: {'bytes': len(raw), 'sha256': _hash(raw)} for name, raw in payloads.items()},
                    'operation_model_requests': 0, 'R1': None, 'R2': None}
        payloads['manifest.json'] = canonical(manifest) + b'\n'
    destination = safe_path(projects.path(project_id), 'selection-exports/' + str(uuid.uuid4()))
    make_directory(destination.parent)
    with tempfile.TemporaryDirectory(prefix='.selection-', dir=destination.parent) as temp:
        temporary = Path(temp)
        for name, raw in payloads.items():
            write_bytes_new(safe_path(temporary, name), raw)
        _verify(temporary)
        zip_path = temporary / 'Selected research.zip'
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in payloads:
                archive.writestr(name, payloads[name])
        # Verify the actual archive after fresh extraction, then after relocation.
        with tempfile.TemporaryDirectory(prefix='dsh-selection-verify-') as check:
            extracted = Path(check) / 'fresh'
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(extracted)
            _verify(extracted)
            relocated = Path(check) / 'relocated'
            shutil.move(str(extracted), relocated)
            _verify(relocated)
        os.rename(temporary, destination)
    raw = read_regular(destination / 'Selected research.zip')
    return {'export_id': destination.name, 'directory': str(destination), 'zip_path': str(destination / 'Selected research.zip'),
            'name': 'Selected research.zip', 'bytes': len(raw), 'sha256': _hash(raw), 'format': FORMAT,
            'captured_seq': expected_seq, 'library_version': expected_library_version, 'operation_model_requests': 0}


def _verify(path):
    result = subprocess.run(['python3', '-B', 'verify_bundle.py', '.'], cwd=path, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, 'EXPORT_VERIFICATION_FAILED', result.stderr or result.stdout)


def read_export(projects, project_id, export_id, offset=0, limit=CHUNK, session_id=None):
    require(type(export_id) is str and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', export_id),
            'UNSAFE_PATH', 'Expected export receipt')
    require(type(offset) is int and type(limit) is int and 0 <= offset <= MAX_BYTES * 2 and 0 < limit <= CHUNK,
            'INVALID_SCHEMA', 'Invalid bounded download range')
    with projects.open_project(project_id, session_id=session_id, chat=session_id is not None) as (metadata, store):
        raw = read_regular(safe_path(projects.path(project_id), 'selection-exports/' + export_id + '/Selected research.zip'))
        require(len(raw) <= MAX_BYTES * 2 and offset <= len(raw), 'TOO_LARGE', 'Export download exceeds bounds')
        part = raw[offset:offset + limit]
        return {'name': 'Selected research.zip', 'bytes': len(raw), 'sha256': _hash(raw), 'offset': offset,
                'next_offset': offset + len(part) if offset + len(part) < len(raw) else None,
                'base64': base64.b64encode(part).decode('ascii')}

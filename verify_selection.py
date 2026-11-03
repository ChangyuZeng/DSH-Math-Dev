"""Standalone selected reading package verifier; Python standard library only."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys

MAX_BYTES = 100 * 1024 * 1024


def verify(root):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('Unsafe extraction root')
    def regular(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Nonregular package member')
        if info.st_size > MAX_BYTES:
            raise ValueError('Package member too large')
        return path.read_bytes()
    raw_manifest = regular(root / 'manifest.json')
    manifest = json.loads(raw_manifest)
    if manifest.get('format') != 'dsh-math-selection-v1' or type(manifest.get('captured_seq')) is not int:
        raise ValueError('Invalid selection manifest')
    if manifest.get('R1') is not None or manifest.get('R2') is not None or manifest.get('operation_model_requests') != 0:
        raise ValueError('Invalid export authority markers')
    options = manifest['options']
    required_options = {'include_dependencies', 'include_material_originals', 'include_process', 'include_summary'}
    if type(options) is not dict or set(options) != required_options or any(type(v) is not bool for v in options.values()):
        raise ValueError('Invalid export options')
    version = manifest['library_version']
    if type(version) is not dict or set(version) != {'seq', 'head_hash'} or type(version['seq']) is not int or version['seq'] < 0 or type(version['head_hash']) is not str:
        raise ValueError('Invalid library version')
    if manifest['captured_seq'] < 0 or type(manifest.get('head_hash')) is not str:
        raise ValueError('Invalid research version')
    files = manifest['files']
    if type(files) is not dict or not 1 <= len(files) <= 2000:
        raise ValueError('Invalid file inventory')
    actual = set()
    total = 0
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlink in package')
        if path.is_dir():
            continue
        name = path.relative_to(root).as_posix()
        actual.add(name)
        if name == 'manifest.json':
            continue
        if name not in files:
            raise ValueError('Unexpected file: ' + name)
        raw = regular(path)
        total += len(raw)
        if total > MAX_BYTES or files[name] != {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}:
            raise ValueError('File integrity mismatch: ' + name)
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('Missing package file')
    for name in files:
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name or str(p) != name or name == 'manifest.json':
            raise ValueError('Unsafe inventory path')
    selection = json.loads(regular(root / 'selection.json'))
    def key(ref):
        if type(ref) is not dict or set(ref) != {'id', 'rev'} or type(ref['id']) is not str or type(ref['rev']) is not int or ref['rev'] < 1:
            raise ValueError('Invalid exact reference')
        return ref['id'], ref['rev']
    if any(type(manifest[k]) is not list for k in ('selected_refs', 'material_refs')):
        raise ValueError('Invalid selected reference lists')
    requested = manifest['selected_refs'] + manifest['material_refs']
    selected = {key(r) for r in requested}
    if len(selected) != len(requested) or len(selected) > 500:
        raise ValueError('Invalid selected revisions')
    records = selection['records']
    if type(records) is not list or not 1 <= len(records) <= 500 or type(selection['omissions']) is not list:
        raise ValueError('Invalid selection records')
    included = {key(r['ref']) for r in records}
    if not selected or len(included) != len(records) or not selected <= included:
        raise ValueError('Invalid selection coverage')
    if not manifest['options']['include_dependencies'] and selected != included:
        raise ValueError('Unselected dependency content')
    by_ref = {key(r['ref']): r for r in records}
    encountered_omissions = []
    def validate_data(value, source):
        if type(value) is list:
            for child in value:
                validate_data(child, source)
        elif type(value) is dict:
            if value.get('status') == 'omitted':
                if value.get('reason') == 'not_selected':
                    if set(value) != {'status', 'reason', 'ref', 'title'} or key(value['ref']) in included or type(value['title']) is not str:
                        raise ValueError('Invalid named omission')
                elif value.get('reason') != 'unavailable' or set(value) != {'status', 'reason'}:
                    raise ValueError('Invalid private omission')
                encountered_omissions.append({'from': source, **value})
                return
            if 'ref' in value:
                if set(value) != {'ref', 'title', 'file'}:
                    raise ValueError('Invalid reference descriptor')
                target = by_ref.get(key(value['ref']))
                if target is None or value['file'] != target['file'] or value['title'] != target['title']:
                    raise ValueError('Reference target or file mismatch')
                return
            if set(value) == {'id', 'rev'}:
                raise ValueError('Unmarked content reference')
            if 'relative_path' in value:
                raise ValueError('Store path disclosure')
            for child in value.values():
                validate_data(child, source)
    record_paths = set()
    for record in records:
        if set(record) != {'ref', 'kind', 'title', 'file', 'selected', 'data'} or type(record['file']) is not str or record['file'] not in files or not record['file'].startswith('records/'):
            raise ValueError('Missing readable revision')
        if record['file'] in record_paths or type(record['title']) is not str or type(record['data']) is not dict or record['kind'] not in ('Problem', 'Note', 'Obligation', 'Evidence', 'Review'):
            raise ValueError('Invalid readable record')
        record_paths.add(record['file'])
        if type(record['selected']) is not bool or record['selected'] != (key(record['ref']) in selected):
            raise ValueError('Selection marker mismatch')
        validate_data(record['data'], record['ref'])
    if encountered_omissions != selection['omissions']:
        raise ValueError('Omission inventory mismatch')
    for marker in selection['omissions']:
        if marker.get('status') != 'omitted' or marker.get('reason') not in ('not_selected', 'unavailable'):
            raise ValueError('Invalid omission marker')
        if key(marker['from']) not in included or marker.get('ref') and key(marker['ref']) in included:
            raise ValueError('Invalid omitted reference')
        if marker['reason'] == 'unavailable' and ('ref' in marker or 'title' in marker):
            raise ValueError('Private omission disclosure')
    options = manifest['options']
    if not options['include_material_originals'] and any(n.startswith('materials/') for n in files):
        raise ValueError('Unrequested original material')
    if not options['include_process'] and any(n.startswith('process/') for n in files):
        raise ValueError('Unrequested process')
    if not options['include_summary'] and 'Project summary.md' in files:
        raise ValueError('Unrequested summary')
    return {'status': 'passed', 'format': manifest['format'], 'captured_seq': manifest['captured_seq'],
            'records': len(records), 'scope': 'Selected reading package integrity only; no mathematical acceptance'}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(sys.argv[1] if len(sys.argv) > 1 else '.'), ensure_ascii=False))
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)

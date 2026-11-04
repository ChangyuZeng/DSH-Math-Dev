"""Canonical cold-start inputs and fresh run directories; never a model launcher.

Only problem definitions, success criterion and submission shape are model input.
The operator's baseline, source audit, prior runs and review records are excluded
by a byte-for-byte allowlist, rather than a blacklist of known answer strings.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

PROTOCOL = 'four-harness-mac-cold-start-v2'
INFORMATION_POLICY = {
    'start': 'cold', 'external_network': 'gateway_only_no_retrieval',
    'materials': 'canonical_task_and_submission_only',
    'public_baseline_or_routes_visible': False, 'previous_runs_visible': False,
    'same_run_memory_allowed': True, 'cross_run_memory_allowed': False,
    'training_exposure': 'unknown', 'provider_cache_state': 'not_controlled',
    'midpoint_continuation': 'separate_future_experiment',
}
TASK = '''Find an ordered list of 15 pairs of integers (U_i, V_i), i = 1,...,15.
Let D = 1048576. Require U_i >= 0, V_i >= 0 and U_i + V_i <= D.
For each of the 2^15 independent choices Z_i in {V_i, -U_i}, define
T = sum_i Z_i and P_k = sum_{i=1}^k Z_i. Define
H(U,V) = min_Z max_{1 <= k <= 15} |2 P_k - T|.
The construction succeeds if H(U,V) >= 1179649.
All 15 positions and both coordinates may vary subject to these constraints.
'''
SMOKE_TASK = '''Non-target arithmetic task: compute 2 + 3 using a common tool,
then report the result in the native harness. This is an engineering check.
'''
SUBMISSION = '''Submit JSON of the exact shape {"pairs": [[U1,V1], ..., [U15,V15]]}.
Each coordinate must be a nonnegative JSON integer; no booleans or floats.
The common check/submit interface evaluates all 32768 choices, including k = 15.
Use only the supplied task, current-run working files and common tools.
'''
SMOKE_SUBMISSION = 'Report the arithmetic result through the native harness; no target construction is requested.\n'


class ColdStartError(ValueError):
    pass


def canonical_inputs(scope='formal'):
    if scope not in {'formal', 'smoke'}:
        raise ColdStartError('Cold input scope must be formal or smoke')
    return {'task.txt': (TASK if scope == 'formal' else SMOKE_TASK).encode('utf-8'),
            'submission.txt': (SUBMISSION if scope == 'formal' else SMOKE_SUBMISSION).encode('utf-8')}


def input_manifest(scope='formal'):
    files = {name: hashlib.sha256(data).hexdigest() for name, data in canonical_inputs(scope).items()}
    encoded = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
    return {'protocol': PROTOCOL, 'scope': scope, 'files_sha256': files,
            'materials_sha256': hashlib.sha256(encoded).hexdigest(),
            'information_policy': dict(INFORMATION_POLICY), 'execution_authorized': False}


def _safe(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ColdStartError('Cold-start path contains a symlink')
    return path


def material_specs(directory, scope='formal'):
    """Verify the entire directory and return the CommonTools material allowlist."""
    directory = _safe(directory)
    expected = canonical_inputs(scope)
    if not directory.is_dir() or {p.name for p in directory.iterdir()} != set(expected):
        raise ColdStartError('Cold material directory must contain exactly the canonical two files')
    result = {}
    for name, data in expected.items():
        path = _safe(directory / name)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.read_bytes() != data:
            raise ColdStartError('Cold material bytes or file identity differ from the canonical input')
        result[name] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
    return result


def write_materials(directory, scope='formal'):
    directory = _safe(directory)
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in canonical_inputs(scope).items():
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    return material_specs(directory, scope)


def prepare(directory, *, scope='formal'):
    """Prepare a new campaign without source history, credentials or execution."""
    root = _safe(directory)
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(mode=0o700, exist_ok=False)
    materials = write_materials(root / 'materials', scope)
    arms = {}
    for arm in 'CXRM':
        arm_root = root / arm
        arm_root.mkdir(mode=0o700)
        for name in ('home', 'workspace', 'state', 'cache', 'tmp'):
            (arm_root / name).mkdir(mode=0o700)
        arms[arm] = {'directory': arm, 'initial_state': 'empty', 'resume_allowed': False,
                     'materials_sha256': input_manifest(scope)['materials_sha256']}
    manifest = {**input_manifest(scope), 'arms': arms,
                'status': 'prepared_inputs_only', 'model_requests': 0,
                'os_isolation_qualified': False,
                'note': 'Directories and input hashes do not prove runtime isolation; native canary checks remain required.'}
    with (root / 'cold-start.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return manifest

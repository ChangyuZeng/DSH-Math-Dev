"""Separate persisted-workspace recovery evidence, never a formal certificate gate.

A fresh process reads exact rows/matrices identified by recovery.json, checks
adjacent transitions, then resumes a proper suffix from a persisted checkpoint.
This does not supply certificates to the solver or alter official submit results.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

FORMAT = 'dsh-math-workflow-recovery-v2'
MANIFEST = {'format': FORMAT, 'task_id': 'D5 or D6', 'resume_index': 'proper interior checkpoint index',
            'entries': [{'index': 'D5:1..m; D6:0..bit_length(exponent)-1', 'path': 'relative JSON path', 'sha256': 'exact file SHA-256'}]}


def raw_hash(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(row):
    return int.from_bytes(hashlib.sha256(json.dumps(row, separators=(',', ':')).encode()).digest(), 'big')


def read_file(workspace, relative):
    root = Path(workspace)
    if not root.is_absolute() or root != root.resolve() or not root.is_dir():
        raise ValueError('Workspace must be an exact absolute directory')
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in ('..', '.') for part in parts):
        raise ValueError('Recovery path must be workspace-relative')
    path = root
    for part in parts:
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_nlink != 1 and stat.S_ISREG(info.st_mode):
            raise ValueError('Recovery material cannot be a link')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024:
            raise ValueError('Bounded regular recovery file required')
        raw = stream.read(8 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('Recovery file changed')
    return raw


def matrix_mul(a, b, modulus):
    return [[sum(x * y for x, y in zip(row, col)) % modulus for col in zip(*b)] for row in a]


def inspect_persisted(task, workspace, candidate):
    """Runs only in the fresh audit process; accepts public task and candidate data."""
    manifest_raw = read_file(workspace, 'recovery.json')
    manifest = json.loads(manifest_raw)
    if manifest.get('format') != FORMAT or manifest.get('task_id') != task['id']:
        raise ValueError('Recovery manifest identity mismatch')
    p, kind = task['parameters'], task['kind']
    indices = list(range(1, p['m'] + 1)) if kind == 'partition_dp' else list(range(p['exponent'].bit_length())) if kind == 'matrix_power' else []
    entries = manifest.get('entries', [])
    if not indices or [row.get('index') for row in entries] != indices or len({row['path'] for row in entries}) != len(entries):
        raise ValueError('All ordered intermediate files are required')
    checkpoint = manifest.get('resume_index')
    if type(checkpoint) is not int or checkpoint not in indices[:-1]:
        raise ValueError('A proper persisted checkpoint is required')
    rows = []
    for entry in entries:
        raw = read_file(workspace, entry['path'])
        if raw_hash(raw) != entry['sha256']:
            raise ValueError('Persisted intermediate hash differs')
        rows.append(json.loads(raw))
    if kind == 'partition_dp':
        if any(type(row) is not list or len(row) != p['N'] + 1 or any(type(x) is not int or x < 0 for x in row) for row in rows):
            raise ValueError('DP row shape differs')
        previous = [1] + [0] * p['N']
        for index, row in zip(indices, rows):
            expected = [sum(previous[s - j * index] for j in (0, 1, 2) if s >= j * index) for s in range(p['N'] + 1)]
            if row != expected:
                raise ValueError('Persisted DP transition differs')
            previous = row
        resumed = rows[checkpoint - 1][:]
        for index in range(checkpoint + 1, p['m'] + 1):
            resumed = [sum(resumed[s - j * index] for j in (0, 1, 2) if s >= j * index) for s in range(p['N'] + 1)]
            if resumed != rows[index - 1]:
                raise ValueError('Resumed DP suffix differs')
        values = [resumed[p['N']], *map(fingerprint, rows)]
    else:
        size, modulus = len(p['A']), p['p']
        if any(type(row) is not list or len(row) != size or any(type(line) is not list or len(line) != size or
               any(type(x) is not int or not 0 <= x < modulus for x in line) for line in row) for row in rows):
            raise ValueError('Matrix chain shape differs')
        previous = p['A']
        for row in rows:
            if row != previous:
                raise ValueError('Persisted matrix transition differs')
            previous = matrix_mul(previous, previous, modulus)
        resumed = rows[checkpoint]
        for index in range(checkpoint + 1, len(rows)):
            resumed = matrix_mul(resumed, resumed, modulus)
            if resumed != rows[index]:
                raise ValueError('Resumed matrix suffix differs')
        product = [[int(i == j) for j in range(size)] for i in range(size)]
        for index, row in enumerate(rows):
            if (p['exponent'] >> index) & 1:
                product = matrix_mul(product, row, modulus)
        values = [*(sum(product[i][j] * p['b'][j] for j in range(size)) % modulus for i in range(size)), *map(fingerprint, rows)]
    # Recovery of persisted work is independent of whether a formal submission
    # exists. Keep the certificate comparison separate instead of erasing valid
    # recovery evidence when no accepted candidate is available.
    candidate_matches = candidate == {'values': values} if candidate is not None else None
    # Re-read all files after the computation to bind the observed stable bytes.
    for entry in entries:
        if raw_hash(read_file(workspace, entry['path'])) != entry['sha256']:
            raise ValueError('Persisted workspace changed during recovery')
    if read_file(workspace, 'recovery.json') != manifest_raw:
        raise ValueError('Recovery manifest changed')
    return {'persisted': True, 'resumed': True, 'fresh_process': True, 'checkpoint': checkpoint,
            'candidate_available': candidate is not None, 'candidate_matches': candidate_matches,
            'intermediate_files': len(rows), 'manifest_sha256': raw_hash(manifest_raw), 'pid': os.getpid()}


def verify_recovery(task, workspace, candidate):
    if task['id'] not in ('D5', 'D6'):
        return {'applicable': False, 'formal_success_unchanged': True}
    packet = {'task': task, 'workspace': str(Path(workspace).resolve()), 'candidate': candidate}
    try:
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--child'],
            input=json.dumps(packet), text=True, capture_output=True, timeout=60,
            env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
        report = json.loads(child.stdout)
        if child.returncode or report.get('pid') == os.getpid():
            raise ValueError('Fresh recovery process failed')
        return {'applicable': True, 'formal_success_unchanged': True, **report}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'applicable': True, 'formal_success_unchanged': True, 'persisted': False,
                'resumed': False, 'fresh_process': True, 'failure': 'missing_or_invalid_persisted_recovery'}


if __name__ == '__main__':
    if sys.argv[1:] != ['--child']:
        raise SystemExit('Only the explicit fresh-process audit entry is available')
    try:
        print(json.dumps(inspect_persisted(**json.load(sys.stdin))))
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as error:
        print(json.dumps({'persisted': False, 'resumed': False, 'failure': type(error).__name__, 'pid': os.getpid()}))
        raise SystemExit(1)

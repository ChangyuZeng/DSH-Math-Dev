"""Session 2 preregistration and immutable identity checks. No upstream calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from workflow_runtime import certificate_contract, qualified_spec, tool_contracts

FORMAT = 'dsh-math-session2-registration-v1'
ARMS = ('plain', 'math-current')
FORMAL_TASKS = ('D7', 'D8', 'D9')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def inventory():
    ignored = {'.git', 'node_modules', 'dist', '__pycache__', '.pytest_cache', '.offline'}
    rows = []
    for path in sorted(SOURCE.rglob('*')):
        relative = path.relative_to(SOURCE)
        if ignored.intersection(relative.parts) or not path.is_file() or path.is_symlink() or path.suffix in {'.pyc', '.pyo'}:
            continue
        rows.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'root': str(SOURCE), 'sha256': digest(rows), 'files': rows}


def exclusive_json(path, value):
    path = Path(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def qualification(path):
    """Freeze offline native adapter qualification and every pinned host/evidence file."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Plain qualification must be an absolute regular non-symlink file')
    raw = path.read_bytes()
    value = json.loads(raw)
    if (value.get('format') != 'dsh-math-session2-plain-qualification-v1' or
            value.get('qualified') is not True or value.get('fixture_only') is not True or
            not isinstance(value.get('native_version'), str) or not value['native_version']):
        raise ValueError('Plain native DSH offline qualification has not passed')
    seen = set()
    for key in ('native_files', 'evidence_files'):
        rows = value.get(key)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f'Plain qualification requires nonempty {key}')
        for row in rows:
            pinned = Path(row['path'])
            if not pinned.is_absolute() or pinned.is_symlink() or not pinned.is_file():
                raise ValueError(f'PLAIN_HOST_DRIFT: missing regular pinned file {pinned}')
            if row['path'] in seen:
                raise ValueError('Plain qualification contains duplicate pinned paths')
            seen.add(row['path'])
            if hashlib.sha256(pinned.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError(f'PLAIN_HOST_DRIFT: pinned native/evidence file changed: {pinned}')
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'value': value}


def registration(tasks, *, fixture=False, plain_qualification_path=None):
    if not fixture and tuple(tasks) != FORMAL_TASKS:
        raise ValueError('Formal batch must preregister exactly D7,D8,D9, both arms, one replicate; no held-out runs')
    if not tasks or len(tasks) != len(set(tasks)):
        raise ValueError('Task IDs must be nonempty and unique')
    if not fixture and plain_qualification_path is None:
        raise ValueError('Formal preregistration requires passed plain native offline qualification')
    plain = qualification(plain_qualification_path) if plain_qualification_path is not None else None
    cards = {}
    for task in tasks:
        contract = certificate_contract(task)
        spec = qualified_spec('registration-only', task, policy='model-directed-v1')
        cards[task] = {'task': contract['task'], 'checker': contract['checker'],
                       'native_checker_sha256': contract['native_checker_sha256'],
                       'stage_goal': spec['stage_goal']}
    policy = qualified_spec('registration-only', tasks[0])
    return {'format': FORMAT, 'created_at': datetime.now(timezone.utc).isoformat(),
            'purpose': 'offline-fixture' if fixture else 'formal-development-baseline',
            'source': inventory(), 'tasks': cards,
            'slots': [{'id': f'{task}--{arm}--r1', 'task': task, 'arm': arm, 'replicate': 1}
                      for task in tasks for arm in ARMS],
            'model_policy': policy['model_policy'], 'server_snapshot': None,
            'budget': policy['budget'], 'context_budget': policy['context_budget'],
            'verification_policy': 'model-directed-v1', 'tools': tool_contracts(),
            'network_policy': 'offline-compute; host-only model service',
            'resource_profile': 'capability-first', 'max_actions': None,
            'observation_window': 'entire native 120-physical-request lineage; no shorter wall/token window',
            'observation_points': {'physical_requests': 20, 'minutes': 30,
                'effect': 'record-only; continue effective progress with equal opportunity for both arms'}, 
            'extension': 'none in this batch; any resource change requires new preregistration and equal opportunity',
            'stop': ['first independently checked explicit submission', 'native model-directed stop',
                     'native no-progress stop', '120 physical requests', 'user interruption',
                     'infrastructure failure or unknown accounting'],
            'attempt_policy': 'one exclusive claim per slot; no cold replay, quota reset, or selective replacement',
            'acceptance': 'finite certificate local_claim only; no human Review or general-proof claim',
            'usage_policy': 'physical ledger authority; missing fields remain null/unknown; cached/reasoning are subsets',
            'plain_qualification': 'must pass native DSH Agent service and ReactLoopAgent adapter qualification before dispatch; never substitute workflow executor',
            'plain_adapter_qualification': plain}


def claim_slot(path, slot_id, registration_sha256, output):
    """Durable exclusive slot claim. Never remove it to replay a failed attempt."""
    path = Path(path)
    directory = path.parent / (path.name + '.claims')
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Registration claim directory must be a regular directory')
    if not slot_id or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in slot_id):
        raise ValueError('Invalid slot identity')
    exclusive_json(directory / (slot_id + '.json'), {'slot_id': slot_id,
        'registration_sha256': registration_sha256, 'output': str(output),
        'claimed_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid()})


def load_verified(path, slot_id, mode):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Registration must be an absolute regular non-symlink file')
    value = json.loads(path.read_text())
    if value.get('format') != FORMAT:
        raise ValueError('Unknown registration format')
    if mode not in {'real', 'fixture'}:
        raise ValueError('Mode must be real or fixture')
    if mode == 'real' and value.get('purpose') != 'formal-development-baseline':
        raise ValueError('Real calls cannot use fixture preregistration')
    if value.get('purpose') not in {'formal-development-baseline', 'offline-fixture'}:
        raise ValueError('Unknown preregistration purpose')
    tasks = list(value['tasks'])
    if value['purpose'] == 'formal-development-baseline' and tuple(tasks) != FORMAL_TASKS:
        raise ValueError('Formal preregistration must contain exactly D7,D8,D9')
    plain_path = (value.get('plain_adapter_qualification') or {}).get('path')
    expected = registration(tasks, fixture=value['purpose'] == 'offline-fixture', plain_qualification_path=plain_path)
    for key in expected.keys() - {'created_at', 'source'}:
        if value.get(key) != expected[key]:
            raise ValueError(f'Preregistered {key} differs from the frozen Session2 protocol')
    if value['source'] != inventory():
        raise ValueError('SOURCE_DRIFT: source changed after preregistration; freeze a new batch')
    slots = [slot for slot in value['slots'] if slot['id'] == slot_id]
    if len(slots) != 1:
        raise ValueError('Slot is not uniquely preregistered')
    slot = slots[0]
    contract = certificate_contract(slot['task'])
    spec = qualified_spec('registration-only', slot['task'])
    if value['tasks'][slot['task']] != {'task': contract['task'], 'checker': contract['checker'],
            'native_checker_sha256': contract['native_checker_sha256'], 'stage_goal': spec['stage_goal']}:
        raise ValueError('TASK_DRIFT: task/checker changed after preregistration')
    for key in ('model_policy', 'budget', 'context_budget', 'tools'):
        if value[key] != spec[key]:
            raise ValueError(f'Frozen {key} differs from production')
    if value['verification_policy'] != 'model-directed-v1' or value['max_actions'] is not None:
        raise ValueError('Formal entry preserves model-directed-v1 and native action freedom')
    return value, slot, digest(value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--tasks', default=','.join(FORMAL_TASKS))
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--plain-qualification', help='Absolute passed offline native adapter qualification artifact')
    args = parser.parse_args()
    path = Path(args.output)
    if not path.is_absolute():
        parser.error('--output must be absolute and new')
    exclusive_json(path, registration(args.tasks.split(','), fixture=args.fixture,
        plain_qualification_path=args.plain_qualification))
    print(canonical({'registration': str(path), 'sha256': digest(json.loads(path.read_text())), 'upstream_requests': 0}))

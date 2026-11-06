"""Frozen public protocol for the small second-round A/B campaign.

Importing this module is read-only. freeze() refuses dirty source and requires
Lead-produced offline qualification tied to the complete source/dependency tree.
No historical run data, private certificates or old promotion gates are read.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = SOURCE.parent
sys.path.insert(0, str(SOURCE))
from workflow_runtime import certificate_contract, rendered_checker, tool_contracts, save_json, sha_bytes
from workflow_v2 import MODEL, TIMEOUTS
from schema import require
from research_store import read_regular

FORMAT = 'dsh-math-workflow-campaign-v2'
REQUIRED_PREFLIGHT = ('transport_deadlines_and_retry_receipts', 'shared_executor_runtime',
    'candidate_independent_archive', 'exact_integer_roundtrip', 'isolated_compute_denials',
    'state_guard_and_cleanup', 'queue_crash_pause_recovery', 'd5_d6_persisted_recovery',
    'no_differential_defect')
GATE = {'formal': 'B>A OR (B==A AND (paired_token_improvement>0 OR paired_time_improvement>0))',
    'secondary_reduction': '1 - median_across_tasks(median_within_task(B/A paired-success ratios))',
    'minimum_paired_success_tasks': 3, 'unknown_costs': 'excluded_never_zero',
    'infrastructure': 'B<=A', 'comparability': ['checks', 'isolation', 'source'],
    'activation': 'finished_fresh_model_verifier OR revision_triggered',
    'holdout': 'only_after_positive_dev_and_no_differential_defect',
    'default_B': 'off_until_positive_holdout_consistency', 'historical_threshold': 'not_used'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return sha_bytes(canonical(value).encode())


def git(*args, source=SOURCE):
    return subprocess.check_output(['git', *args], cwd=source).decode().strip()


def source_manifest(source=SOURCE):
    source = Path(source).resolve()
    require(git('status', '--porcelain', '--untracked-files=all', source=source) == '',
            'DIRTY_SOURCE', 'Freeze/execution requires a clean committed source tree')
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=source).decode().split('\0')
    files = {}
    for name in sorted(filter(None, names)):
        path = source / name
        require(not path.is_symlink(), 'SOURCE_PATH', 'Frozen source cannot contain symlinks')
        files[name] = sha_bytes(read_regular(path))
    return {'commit': git('rev-parse', 'HEAD', source=source), 'files': files, 'sha256': digest(files)}


def dependencies(node='node'):
    binary = shutil.which(node)
    require(binary is not None, 'DEPENDENCY', 'Qualified Node executable is required')
    node_path = Path(binary).resolve()
    # parseExact requires JSON.rawJSON and reviver source; fail before a call.
    subprocess.run([str(node_path), '-e', "if(typeof JSON.rawJSON!=='function'||JSON.parse('1',(_,v,c)=>c.source)!=='1')process.exit(1)"], check=True, capture_output=True)
    python_path = Path(sys.executable).resolve()
    compute_python = python_path
    framework = Path(sys.base_prefix) / 'Resources' / 'Python.app' / 'Contents' / 'MacOS' / 'Python'
    if sys.platform == 'darwin' and framework.is_file():
        compute_python = framework.resolve()
    sandbox = '/usr/bin/sandbox-exec' if sys.platform == 'darwin' else shutil.which('bwrap')
    require(sandbox is not None and Path(sandbox).is_file(), 'DEPENDENCY', 'Qualified OS isolation executable is required')
    return {'python': platform.python_version(), 'python_sha256': sha_bytes(python_path.read_bytes()),
            'compute_python_sha256': sha_bytes(compute_python.read_bytes()),
            'sandbox_sha256': sha_bytes(Path(sandbox).resolve().read_bytes()),
            'node': subprocess.check_output([str(node_path), '--version']).decode().strip(),
            'node_sha256': sha_bytes(node_path.read_bytes()), 'platform': platform.platform(), 'machine': platform.machine()}


def public_tasks():
    result = []
    for prefix, count, split in (('D', 6, 'dev'), ('H', 3, 'holdout')):
        for index in range(1, count + 1):
            contract = certificate_contract(prefix + str(index))
            result.append({'id': prefix + str(index), 'split': split, 'card': contract['task'],
                'checker': contract['checker'], 'native_checker_sha256': contract['native_checker_sha256'],
                'certificate_source_sha256': contract['certificate_source_sha256']})
    # The three unused reserves are frozen public material, not registered or run.
    import importlib.util
    path = SOURCE / 'evaluation' / 'optimization-v1' / 'certificates.py'
    spec = importlib.util.spec_from_file_location('workflow_public_cards', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for card in module.cards():
        if card['id'].startswith('R'):
            result.append({'id': card['id'], 'split': 'reserve', 'card': card,
                'checker': {'id': None, 'code_sha256': sha_bytes(rendered_checker(card)), 'task_sha256': card['sha256']},
                'native_checker_sha256': None, 'certificate_source_sha256': sha_bytes(read_regular(path))})
    return result


def pair_queue(seed, phase, tasks):
    rng = random.Random(f'{seed}:{phase}')
    pairs = [(task, rep) for task in tasks for rep in range(1, 4)]
    rng.shuffle(pairs)
    # Difference at most one when there are nine holdout pairs.
    orders = [('A', 'B')] * ((len(pairs) + 1) // 2) + [('B', 'A')] * (len(pairs) // 2)
    rng.shuffle(orders)
    queue = []
    for (task, rep), order in zip(pairs, orders):
        pair = f'{phase}-{task}-r{rep}'
        for arm in order:
            queue.append({'slot_id': pair + '-' + arm, 'pair_id': pair, 'phase': phase,
                          'task': task, 'rep': rep, 'arm': arm, 'order': list(order)})
    return queue


def build_protocol(source, dependency, tasks, qualification, seed=20261106):
    require(type(seed) is int, 'PROTOCOL', 'Integer seed required')
    require([row['id'] for row in tasks] == [*(f'D{i}' for i in range(1, 7)), *(f'H{i}' for i in range(1, 4)), *(f'R{i}' for i in range(1, 4))],
            'PROTOCOL', 'Exactly six dev, three holdout and three reserve public cards required')
    return {'format': FORMAT, 'seed': seed, 'source': source, 'dependencies': dependency,
        'tasks': tasks, 'task_sha256': digest(tasks), 'tool_contracts': tool_contracts(),
        'tool_schema_sha256': digest(tool_contracts()), 'qualification': qualification,
        'offline_preflight_commands': [['python', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_workflow*.py'],
            ['node', '--test', 'tests/workflow-runtime-v2.test.mjs']],
        'model': deepcopy(MODEL), 'budget': {'physical_requests_per_solve': 120, 'total_tokens': None, 'max_output_tokens': 128000},
        'context_budget': {'max_tokens': None}, 'timeouts': deepcopy(TIMEOUTS), 'isolation': 'os-process',
        'arms': {'A': 'model-directed-v1', 'B': 'verify-revise-v1'}, 'max_revisions': 2,
        'execution': {'serial': True, 'clean_sessions': True, 'repetitions': 3, 'dev_solves': 36,
                      'conditional_holdout_solves': 18, 'reserve_solves': 0, 'retry_started_slots': False},
        'connectivity': {'maximum_physical_requests': 4, 'initial_maximum': 2, 'initial_required_successes': 2,
            'recovery_phases': 1, 'recovery_required_consecutive_successes': 2, 'hidden_retries': False,
            'separate_from_solve_costs': True},
        'circuit_breaker': {'consecutive_infrastructure_terminations': 2, 'immediate': ['auth', 'model_drift', 'protocol_drift'],
            'second_pause': 'stop', 'paused_pending_slots': 'untouched'},
        'gate': deepcopy(GATE), 'official_success': 'explicit_submit_fresh_process_exact_audit_rerun',
        'posthoc': 'separate_never_promotes_official_success',
        'recovery_evidence': {'D5': 'all DP rows persisted and resumed in a fresh process',
            'D6': 'all squaring-chain matrices persisted and resumed in a fresh process',
            'separate_from_mathematical_certificate': True},
        'queue': pair_queue(seed, 'dev', [f'D{i}' for i in range(1, 7)]) + pair_queue(seed, 'holdout', [f'H{i}' for i in range(1, 4)])}


def validate_qualification(qualification, source, dependency, tasks):
    require(type(qualification) is dict and qualification.get('format') == 'dsh-math-workflow-preflight-v2',
            'PREFLIGHT_REQUIRED', 'Lead-produced offline qualification is required')
    require(qualification.get('source_sha256') == source['sha256'] and qualification.get('dependency_sha256') == digest(dependency)
            and qualification.get('task_sha256') == digest(tasks) and qualification.get('tool_schema_sha256') == digest(tool_contracts()),
            'PREFLIGHT_DRIFT', 'Qualification hashes differ from frozen inputs')
    checks = qualification.get('checks', {})
    require(all(checks.get(name) is True for name in REQUIRED_PREFLIGHT), 'PREFLIGHT_FAILED', 'A required offline qualification did not pass')
    expected = {row['id']: {'checker': row['checker'], 'native_checker_sha256': row['native_checker_sha256'],
                            'certificate_source_sha256': row['certificate_source_sha256']} for row in tasks}
    require(qualification.get('certificate_hashes') == expected, 'PREFLIGHT_DRIFT', 'Qualified checker hashes differ')


def campaign_root(path):
    path = Path(path).absolute()
    require(path == path.resolve() and SOURCE not in path.parents and path != SOURCE,
            'CAMPAIGN_ROOT', 'Campaign artifacts must be outside source without symlink ancestors')
    # Never accept the historical optimization tree, even when explicitly supplied.
    require('dsh-math-optimization' not in path.parts and path != Path('/'), 'CAMPAIGN_ROOT', 'Historical/root destinations are forbidden')
    return path


def freeze(directory, qualification, *, node='node', seed=20261106):
    directory = campaign_root(directory)
    require(not (directory / 'protocol.json').exists(), 'NO_REPLAY', 'Protocol is already frozen')
    source, dependency, tasks = source_manifest(), dependencies(node), public_tasks()
    validate_qualification(qualification, source, dependency, tasks)
    protocol = build_protocol(source, dependency, tasks, qualification, seed)
    document = {'protocol': protocol, 'sha256': digest(protocol)}
    save_json(directory / 'protocol.json', document)
    return document


def verify_freeze(directory, *, node='node'):
    directory = campaign_root(directory)
    document = json.loads(read_regular(directory / 'protocol.json'))
    protocol = document['protocol']
    require(protocol.get('format') == FORMAT and document['sha256'] == digest(protocol), 'PROTOCOL_DRIFT', 'Frozen protocol hash differs')
    source, dependency, tasks = source_manifest(), dependencies(node), public_tasks()
    require(protocol['source'] == source and protocol['dependencies'] == dependency and protocol['tasks'] == tasks,
            'PROTOCOL_DRIFT', 'Frozen source, dependencies, or public tasks drifted')
    validate_qualification(protocol['qualification'], source, dependency, tasks)
    require(protocol == build_protocol(source, dependency, tasks, protocol['qualification'], protocol['seed']),
            'PROTOCOL_DRIFT', 'Frozen campaign policy differs from the implementation')
    return document

"""Durable serial queue, circuit breaker, and preregistered positive gate.

Started slots/probes are consumed before dispatch. A crash never replays one.
Pure queue methods do not launch subprocesses, probes, models or checkers.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import statistics
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workflow_runtime import save_json
from schema import require
from research_store import read_regular
from protocol import GATE, digest

IMMEDIATE = {'auth', 'model_drift', 'protocol_drift'}
COMPARABLE = ('checks', 'isolation', 'source')
USAGE_KEYS = ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens')


def usage_projection(rows):
    """Unknown core and optional columns remain null independently."""
    require(type(rows) is list and len({row['request_id'] for row in rows}) == len(rows), 'ACCOUNTING', 'Unique physical rows required')
    value = {'physical_requests': len(rows)}
    for key in USAGE_KEYS:
        values = [(row.get('usage') or {}).get(key) for row in rows]
        require(all(v is None or type(v) is int and v >= 0 for v in values), 'ACCOUNTING', 'Invalid usage column')
        value['known_' + key] = sum(v for v in values if v is not None)
        terminal = all((row.get('usage') or {}).get('complete') is True for row in rows)
        value[key] = sum(values) if terminal and all(v is not None for v in values) else None
    value['unknown_requests'] = sum((row.get('usage') or {}).get('complete') is not True or any(
        (row.get('usage') or {}).get(key) is None for key in USAGE_KEYS[:3]) for row in rows)
    value['core_complete'] = all(value[k] is not None for k in USAGE_KEYS[:3])
    value['optional_complete'] = all(value[k] is not None for k in USAGE_KEYS[3:])
    return value


def mechanism_active(result):
    events = result.get('execution_events', [])
    revision = any(event.get('type') == 'revision_triggered' for event in events)
    physical = {row['request_id'] for row in result.get('physical_requests', [])
                if row.get('role') == 'verifier' and row.get('status') == 'finished'}
    verified = any(event.get('type') == 'model_finished' and event.get('role') == 'verifier'
                   and event.get('request_id') in physical for event in events)
    return revision or verified


def paired_metric(results, metric):
    pairs = {}
    for result in results:
        pairs.setdefault(result['pair_id'], {})[result['arm']] = result
    tasks = {}
    for pair in pairs.values():
        if set(pair) != {'A', 'B'} or not all(row.get('formal_success') is True for row in pair.values()):
            continue
        a, b = pair['A'].get(metric), pair['B'].get(metric)
        # Unknown is excluded, never replaced with zero. Zero denominators do
        # not admit ratios or a secondary cost/time claim.
        if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in (a, b)) or a == 0:
            continue
        tasks.setdefault(pair['A']['task'], []).append(b / a)
    medians = {task: statistics.median(values) for task, values in sorted(tasks.items())}
    ratio = statistics.median(medians.values()) if len(medians) >= GATE['minimum_paired_success_tasks'] else None
    return {'task_medians_B_over_A': medians, 'task_count': len(medians), 'median_B_over_A': ratio,
            'improvement': None if ratio is None else 1 - ratio}


def positive_gate(protocol, results, phase='dev'):
    require(protocol['gate'] == GATE, 'PROTOCOL_DRIFT', 'Gate differs from the frozen new positive rule')
    slots = [row for row in protocol['queue'] if row['phase'] == phase]
    selected = [row for row in results if row['phase'] == phase]
    expected = {row['slot_id']: row for row in slots}
    complete = len(selected) == len(slots) and {row['slot_id'] for row in selected} == set(expected)
    for result in selected:
        require(result['slot_id'] in expected and all(result[key] == expected[result['slot_id']][key]
            for key in ('pair_id', 'task', 'rep', 'arm', 'phase')), 'RESULT_IDENTITY', 'Result differs from its frozen slot')
    formal = {arm: sum(row.get('formal_success') is True for row in selected if row['arm'] == arm) for arm in ('A', 'B')}
    infra = {arm: sum(row.get('infra_terminated') is True for row in selected if row['arm'] == arm) for arm in ('A', 'B')}
    comparable = complete and all(all(row.get('comparable', {}).get(key) is True for key in COMPARABLE) for row in selected)
    differential = any(row.get('differential_defect') is not False for row in selected)
    active = any(mechanism_active(row) for row in selected if row['arm'] == 'B')
    tokens, wall = paired_metric(selected, 'total_tokens'), paired_metric(selected, 'wall_seconds')
    secondary = any(metric['improvement'] is not None and metric['improvement'] > 0 for metric in (tokens, wall))
    directional = formal['B'] > formal['A'] or formal['B'] == formal['A'] and secondary
    positive = complete and comparable and not differential and active and infra['B'] <= infra['A'] and directional
    return {'phase': phase, 'positive': positive, 'complete': complete, 'formal': formal, 'infrastructure': infra,
            'comparable': comparable, 'differential_defect': differential, 'mechanism_active': active,
            'tokens': tokens, 'wall_time': wall, 'directional': directional,
            'rule': deepcopy(GATE), 'B_default_on': False}


def unknown_result(slot, reason='interrupted_started_slot'):
    return {**slot, 'formal_success': False, 'infra_terminated': True, 'termination': reason,
            'immediate': None, 'total_tokens': None, 'wall_seconds': None, 'physical_requests': [],
            'physical_count_known': False, 'usage': {key: None for key in USAGE_KEYS},
            'execution_events': [], 'comparable': {key: False for key in COMPARABLE},
            'differential_defect': False, 'posthoc': None, 'workflow_recovery': None}


class Campaign:
    def __init__(self, directory, document):
        self.directory, self.document = Path(directory), deepcopy(document)
        require(document['sha256'] == digest(document['protocol']), 'PROTOCOL_DRIFT', 'Protocol hash differs')
        self.protocol = self.document['protocol']
        self.path = self.directory / 'campaign-state.json'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._locked():
            if not self.path.exists():
                self._save({'format': 'dsh-math-workflow-queue-v2', 'protocol_sha256': document['sha256'],
                    'revision': 0, 'status': 'initial_connectivity', 'phase': 'dev', 'cursor': 0,
                    'active_slot': None, 'partial_pair': None, 'results': [], 'probes': [],
                    'active_probe': None, 'infra_streak': 0, 'pause_count': 0, 'recovery_used': False,
                    'recovery_preflight': None, 'pause_reason': None, 'gates': {}, 'B_default_on': False})
            self._load()

    @contextmanager
    def _locked(self, name='.queue.lock'):
        path = self.directory / name
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            import stat
            require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, 'UNSAFE_PATH', 'Unsafe campaign lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('SERIAL_ONLY: another campaign operation is active') from error
            yield fd
        finally:
            os.close(fd)

    def driver_lock(self):
        """Hold across dispatch, wait and all queue mutations; excludes other drivers."""
        return self._locked('.driver.lock')

    def _load(self):
        packet = json.loads(read_regular(self.path))
        require(packet['sha256'] == digest(packet['state']), 'QUEUE_CORRUPT', 'Queue checksum differs')
        state = packet['state']
        require(state['protocol_sha256'] == self.document['sha256'], 'PROTOCOL_DRIFT', 'Queue belongs to another protocol')
        return state

    def _save(self, state):
        state['revision'] += 1
        save_json(self.path, {'state': state, 'sha256': digest(state)})

    def read(self):
        with self._locked():
            return self._load()

    def _change(self, action):
        with self._locked():
            state = self._load()
            result = action(state)
            self._save(state)
            return deepcopy(result)

    @staticmethod
    def _pause(state, reason):
        state['pause_count'] += 1
        state['pause_reason'] = reason
        state['status'] = 'stopped' if state['pause_count'] >= 2 or state['recovery_used'] or len(state['probes']) >= 4 else 'paused'

    def pause(self, reason):
        return self._change(lambda state: self._pause(state, reason))

    def claim_probe(self):
        def action(state):
            require(state['status'] in {'initial_connectivity', 'recovery_connectivity'} and state['active_probe'] is None
                    and state['active_slot'] is None, 'PAUSED', 'Connectivity dispatch is not available')
            phase = 'initial' if state['status'] == 'initial_connectivity' else 'recovery'
            require(len(state['probes']) < 4 and sum(row['phase'] == phase for row in state['probes']) < 2,
                    'PROBE_LIMIT', 'Physical connectivity budget exhausted')
            row = {'probe_id': 'connectivity-' + str(len(state['probes']) + 1), 'phase': phase,
                   'status': 'started', 'success': None, 'usage': None, 'physical_requests': 1}
            state['probes'].append(row)
            state['active_probe'] = row['probe_id']
            return row
        return self._change(action)

    def finish_probe(self, probe_id, result):
        def action(state):
            require(state['active_probe'] == probe_id, 'NO_REPLAY', 'Probe is not the active physical reservation')
            row = next(row for row in state['probes'] if row['probe_id'] == probe_id)
            require(result.get('physical_requests') == 1 and type(result.get('success')) is bool,
                    'PROBE_ACCOUNTING', 'One physical receipt per reservation; retries prohibited')
            row.update(status='finished', success=result['success'], receipt=deepcopy(result), usage=result.get('usage'))
            state['active_probe'] = None
            phase = [item for item in state['probes'] if item['phase'] == row['phase']]
            if not row['success']:
                state['status'] = 'stopped'
                state['pause_reason'] = 'connectivity_failed_or_unknown'
            elif len(phase) == 2:
                require(all(item['success'] is True for item in phase), 'CONNECTIVITY', 'Two consecutive successes required')
                state['status'], state['infra_streak'] = 'running', 0
            return row
        return self._change(action)

    def begin_recovery(self, preflight_sha256):
        def action(state):
            require(state['status'] == 'paused' and not state['recovery_used'] and len(state['probes']) <= 2,
                    'RECOVERY_LIMIT', 'Only one recovery phase with two physical requests is permitted')
            require(preflight_sha256 == digest(self.protocol['qualification']), 'PREFLIGHT_FAILED', 'Frozen offline preflight must pass again')
            state.update(recovery_used=True, recovery_preflight=preflight_sha256, status='recovery_connectivity')
        return self._change(action)

    def claim_slot(self):
        def action(state):
            require(state['status'] == 'running' and state['active_slot'] is None and state['active_probe'] is None,
                    'PAUSED', 'No solve may dispatch while paused, gated, or already active')
            require(state['cursor'] < len(self.protocol['queue']), 'QUEUE_END', 'No pending solve')
            row = deepcopy(self.protocol['queue'][state['cursor']])
            require(row['phase'] == state['phase'], 'HOLDOUT_GATE', 'Holdout remains gated')
            state['cursor'] += 1  # durable consumption precedes any process or model
            state['active_slot'] = row
            state['partial_pair'] = {'pair_id': row['pair_id'], 'order': row['order'],
                'completed': [r['slot_id'] for r in state['results'] if r['pair_id'] == row['pair_id']], 'started': row['slot_id']}
            return row
        return self._change(action)

    def _finish_slot(self, state, result):
        slot = state['active_slot']
        require(slot is not None and all(result.get(key) == slot[key] for key in slot), 'RESULT_IDENTITY', 'Result differs from active slot')
        require(all(type(result.get(key)) is bool for key in ('formal_success', 'infra_terminated', 'differential_defect'))
                and not (result['formal_success'] and result['infra_terminated']), 'RESULT_SCHEMA', 'Official success and infrastructure termination must be distinct')
        state['results'].append(deepcopy(result))
        state['active_slot'] = None
        completed = [row['slot_id'] for row in state['results'] if row['pair_id'] == slot['pair_id']]
        state['partial_pair'] = None if len(completed) == 2 else {'pair_id': slot['pair_id'], 'order': slot['order'], 'completed': completed, 'started': None}
        state['infra_streak'] = state['infra_streak'] + 1 if result['infra_terminated'] else 0
        if result.get('immediate') in IMMEDIATE:
            self._pause(state, result['immediate'])
        elif state['infra_streak'] >= 2:
            self._pause(state, 'two_consecutive_infrastructure_terminations')
        elif state['cursor'] == len(self.protocol['queue']) or self.protocol['queue'][state['cursor']]['phase'] != state['phase']:
            state['status'] = 'awaiting_gate'
        return result

    def finish_slot(self, result):
        return self._change(lambda state: self._finish_slot(state, result))

    def reconcile_interrupted(self, recovered_result=None):
        """Only call while holding driver_lock after the previous owner exited."""
        def action(state):
            if state['active_probe']:
                row = next(row for row in state['probes'] if row['probe_id'] == state['active_probe'])
                row.update(status='unknown', success=False, usage=None)
                state.update(active_probe=None, status='stopped', pause_reason='interrupted_physical_probe')
            if state['active_slot']:
                self._finish_slot(state, recovered_result or unknown_result(state['active_slot']))
        return self._change(action)

    def evaluate(self):
        def action(state):
            require(state['status'] == 'awaiting_gate', 'GATE_STATE', 'Phase is not ready for evaluation')
            gate = positive_gate(self.protocol, state['results'], state['phase'])
            state['gates'][state['phase']] = gate
            if state['phase'] == 'dev' and gate['positive']:
                state.update(phase='holdout', status='running')
            else:
                state['status'] = 'completed'
                state['B_default_on'] = state['phase'] == 'holdout' and gate['positive'] and state['gates']['dev']['positive']
            return gate
        return self._change(action)

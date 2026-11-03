"""Pure advisory projections; never schedules calls or grants mathematical acceptance.

history contains earlier run snapshots in chronological order. Repeated run ids
are collapsed to their last snapshot, and run is authoritative for its own id.
A reuse candidate is returned only against an explicit requested context snapshot.
"""
from copy import deepcopy

from schema import StoreError, fields, integer, ref, validate_data
from workflow_context import freshness

NEXT_KINDS = {'derive', 'check', 'revise', 'decompose', 'stop'}
PROGRESS = {'new_argument', 'new_obligation', 'new_evidence', 'no_progress'}


def _advisory(proposal, problem_ref):
    """Reject malformed or authority-bearing proposals even in imported history."""
    try:
        fields(proposal, 'summary changes next_action next_target progress limitations')
        if (type(proposal['summary']) is not str or proposal['next_action'] not in NEXT_KINDS
                or proposal['progress'] not in PROGRESS or type(proposal['limitations']) is not list
                or not all(type(value) is str for value in proposal['limitations'])
                or type(proposal['changes']) is not list or len(proposal['changes']) > 8):
            return False
        if proposal['next_target'] is not None:
            ref(proposal['next_target'])
        ids = set()
        for change in proposal['changes']:
            fields(change, 'kind id expected_rev data')
            ref({'id': change['id'], 'rev': 1})
            integer(change['expected_rev'])
            if change['id'] in ids or change['kind'] not in ('Note', 'Obligation'):
                return False
            ids.add(change['id'])
            validate_data(change['kind'], change['data'])
            if change['data']['problem_ref'] != problem_ref:
                return False
            if change['kind'] == 'Obligation' and change['data']['state'] != 'open':
                return False
        return True
    except (StoreError, TypeError, KeyError):
        return False


def _refs_fresh(records, references):
    if type(references) is not list or not references:
        return False
    try:
        for reference in references:
            ref(reference)
        return not freshness(records, {'refs': references})
    except (StoreError, TypeError, KeyError):
        return False


def _runs(run, history):
    latest = {}
    for item in [*(history or []), run]:
        latest.pop(item['run_id'], None)
        latest[item['run_id']] = item
    return list(latest.values())


def reuse_candidates(run, records, context_snapshot, *, history=None):
    """Return readable, fresh model-advisory matches; never skip an action."""
    requested = context_snapshot or {}
    key = requested.get('verification_key')
    kind = requested.get('action', {}).get('kind')
    if (not isinstance(key, str) or not key or kind not in ('check', 'reconstruct')
            or not _refs_fresh(records, requested.get('refs'))):
        return []
    candidates = []
    for source in _runs(run, history):
        if (source['project_id'] != run['project_id']
                or source['spec']['problem_ref'] != run['spec']['problem_ref']
                or source['spec']['model_policy'] != run['spec']['model_policy']):
            continue
        for attempt in source['actions']:
            snapshot = attempt.get('context_snapshot', {})
            proposal = attempt.get('proposal')
            if (attempt['status'] != 'committed' or attempt['kind'] != kind
                    or snapshot.get('verification_key') != key
                    or snapshot.get('action', {}).get('kind') != kind
                    or snapshot.get('action', {}).get('target_ref') != requested.get('action', {}).get('target_ref')
                    or snapshot.get('refs') != requested.get('refs')
                    or not _refs_fresh(records, snapshot.get('refs'))
                    or not _advisory(proposal, run['spec']['problem_ref'])):
                continue
            outputs = attempt.get('output_refs', [])
            expected = [{'id': change['id'], 'rev': change['expected_rev'] + 1} for change in proposal['changes']]
            if outputs != expected or outputs and not _refs_fresh(records, outputs):
                continue
            candidates.append({'run_id': source['run_id'], 'action_id': attempt['action_id'],
                               'kind': kind, 'verification_key': key, 'state': 'reused_advisory_candidate',
                               'summary': proposal['summary'], 'limitations': deepcopy(proposal['limitations']),
                               'refs': deepcopy(snapshot['refs']), 'output_refs': deepcopy(outputs),
                               'authority': 'model_advisory', 'human_accepted': False, 'auto_skip': False})
    return candidates


def derive_strategy(run, records, *, history=None, context_snapshot=None):
    """Project current-run routes and cross-run reusable checks without mutations.

Two consecutive committed no_progress results require reassessment. Intervening
technical failures do not count as mathematical outcomes. A later successful
progress result resets the streak; stale latest premises take precedence.
"""
    route_ids = [route['id'] for route in run['spec'].get('routes', [])]
    if not route_ids or any(a['context_snapshot']['action']['route_id'] is None for a in run['actions']):
        route_ids.append(None)
    routes = []
    for route_id in route_ids:
        attempts = [a for a in run['actions'] if a['context_snapshot']['action']['route_id'] == route_id]
        committed = [a for a in attempts if a['status'] == 'committed']
        last = committed[-1] if committed else None
        context_attempt = next((a for a in reversed(attempts) if a['status'] in ('queued', 'running', 'proposed', 'committed')), None)
        stale = freshness(records, context_attempt['context_snapshot']) if context_attempt else []
        streak = 0
        for attempt in reversed(committed):
            if not _advisory(attempt.get('proposal'), run['spec']['problem_ref']) or attempt['proposal']['progress'] != 'no_progress':
                break
            streak += 1
        state = 'needs_recheck' if stale else 'needs_reassessment' if streak >= 2 else 'active'
        proposal = last['proposal'] if last and _advisory(last.get('proposal'), run['spec']['problem_ref']) else None
        target = last['context_snapshot']['action']['target_ref'] if last else None
        if state == 'needs_recheck':
            suggestion = {'kind': 'check', 'target_ref': context_attempt['context_snapshot']['action']['target_ref'],
                          'reason': 'dependency_revision_changed', 'rebuild_context_required': True}
        elif state == 'needs_reassessment':
            suggestion = {'kind': 'stop', 'target_ref': target, 'reason': 'two_committed_no_progress_results_require_reassessment'}
        elif proposal:
            suggestion = {'kind': proposal['next_action'], 'target_ref': proposal['next_target'], 'reason': 'last_committed_advisory'}
        else:
            suggestion = {'kind': 'derive', 'target_ref': None, 'reason': 'no_committed_outcome'}
        routes.append({'route_id': route_id, 'state': state, 'consecutive_no_progress': streak,
                       'committed_actions': len(committed),
                       'technical_errors': sum(a['status'] in ('failed', 'unknown') for a in attempts),
                       'last_action_id': last['action_id'] if last else None,
                       'context_action_id': context_attempt['action_id'] if context_attempt else None, 'stale_refs': stale,
                       'next_action': {**suggestion, 'route_id': route_id},
                       'mathematical_verdict': 'undetermined'})
    priority = {'needs_recheck': 0, 'needs_reassessment': 1, 'active': 2}
    next_action = min(routes, key=lambda row: priority[row['state']])['next_action'] if routes else None
    if run['status'] in ('completed', 'cancelled', 'needs_reconcile'):
        next_action = {'kind': 'stop', 'target_ref': None, 'route_id': None, 'reason': 'run_' + run['status']}
    return deepcopy({'routes': routes, 'next_action': next_action,
                     'reuse_candidates': reuse_candidates(run, records, context_snapshot, history=history),
                     'authority': 'model_advisory', 'human_accepted': False, 'auto_execute': False,
                     'scientific_status': 'candidate_pending_review'})

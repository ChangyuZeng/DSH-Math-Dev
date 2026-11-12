"""Host-owned qualified certificate runtime, shared by UI/chat and experiments.

No model policy lives here. The existing JS executor owns reasoning, archival and
stop decisions. A durable lineage claim is deliberately never removed: there is
no safe cold restart of a partly used Gateway/CommonTools lineage. Recovery must
reconcile its retained evidence, never reset its quota or replay a tool effect.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import uuid

SOURCE = Path(__file__).resolve().parent
PRIMITIVES = SOURCE / 'evaluation' / 'four-harness-v1'
if str(PRIMITIVES) not in sys.path:
    sys.path.insert(0, str(PRIMITIVES))

from backend import Projects, make_directory
from research_store import read_regular, safe_path, sync_directory
from schema import StoreError, identifier, require
from workflow_service import dispatch_internal, transaction
from workflow_v2 import EFFECTS, MODEL, TIMEOUTS
from ledger import Ledger, OPTIMIZATION_BUDGET, canonical
from gateway import Gateway, FixtureTransport, TOOL_SCHEMAS, TOOL_NAMES, capability
from tools import CommonTools, ToolLimits
from state_guard import StateGuard
from native import isolated_environment

CANDIDATE_SCHEMA = {'type': 'object', 'properties': {'values': {'type': 'array', 'items': {'type': 'integer'}}},
                    'required': ['values'], 'additionalProperties': False}
ROLES = ('worker', 'verifier', 'reviser', 'summary')


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def save_json(path, value):
    """Private, fsynced, atomic evidence; existing files must be regular files."""
    path = Path(path)
    make_directory(path.parent)
    if path.exists() or path.is_symlink():
        read_regular(path)
    pending = safe_path(path.parent, '.pending-' + str(uuid.uuid4()))
    raw = (canonical(value) + '\n').encode()
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        sync_directory(path.parent)
    finally:
        # A failed write is retained for diagnosis rather than silently deleted.
        pass


def certificate_contract(task_id):
    # Deferred: the checker integration can be installed after this module.
    from workflow_candidates import certificate_contract as contract
    return contract(task_id)


def rendered_checker(card):
    """Exactly the existing public experiment.task_checker TASK substitution."""
    raw = (SOURCE / 'evaluation' / 'optimization-v1' / 'certificates.py').read_text()
    require(raw.count('TASK = None') == 1, 'CHECKER_DRIFT', 'Public checker template changed')
    return raw.replace('TASK = None', 'TASK = ' + repr(card), 1).encode()


def tool_contracts():
    result = []
    for name, _, parameters in TOOL_SCHEMAS:
        parameters = deepcopy(parameters)
        if name in {'check', 'submit'}:
            parameters['properties']['candidate'] = deepcopy(CANDIDATE_SCHEMA)
        result.append({'name': name, 'version': '1', 'read_only': EFFECTS[name] == 'read',
                       'effect': EFFECTS[name], 'schema_sha256': sha_bytes(canonical(parameters).encode())})
    return result


def qualified_spec(project_id, task_id, policy='model-directed-v1', problem_ref=None):
    """Prepare a RunSpec; this neither starts a runtime nor authorizes a call."""
    contract = certificate_contract(task_id)
    stage_goal = 'Construct and explicitly submit an exact certificate for the frozen public task. Preserve useful intermediate work in the isolated workspace.'
    if task_id in {'D5', 'D6'}:
        stage_goal += (' For separate workflow recovery evidence, retain every intermediate DP row (D5, indices 1..m) or squaring-chain matrix (D6, indices 0..bit_length(exponent)-1) as JSON files.'
                       ' Each D5 file must be a bare JSON array of N+1 nonnegative integers; each D6 file must be a bare JSON array of size-by-size row arrays with integers satisfying 0 <= value < p, where size is the number of rows of A. The top-level value is the array itself.'
                       ' Write recovery.json with format dsh-math-workflow-recovery-v2, task_id, resume_index (a proper interior checkpoint), and ordered entries [{index,path,sha256}] identifying the persisted files. sha256 is the SHA-256 of the exact file bytes. This evidence does not change mathematical certificate acceptance.')
    return {'format': 'dsh-math-workflow-v2', 'project_id': project_id,
            'problem_ref': problem_ref or {'id': 'P', 'rev': 1}, 'model_policy': deepcopy(MODEL),
            'budget': {'requests': 120, 'total_tokens': None, 'max_output_tokens': 128000},
            'context_budget': {'max_tokens': None}, 'tools': tool_contracts(),
            'stage_goal': stage_goal,
            'execution': {'transport': 'metered-codex', 'accounting_authority': 'gateway',
                          'verification_policy': policy, 'max_revisions': 2, 'timeouts': deepcopy(TIMEOUTS),
                          'isolation': 'os-process', 'checker': deepcopy(contract['checker'])}}


class WorkflowRuntime:
    """Trusted host startup. Use start_offline_fixture explicitly in tests only."""
    def __init__(self, root, project_id, run_id):
        self.root, self.project_id, self.run_id = Path(root), project_id, run_id
        self.gateway = self.ledger = self.guard = self.host = None
        self.directory = None
        self.done = threading.Event()
        self.monitor = None
        self.closed = False
        self.close_lock = threading.Lock()
        self.environment = None

    def start(self):
        return self._start(None)

    def start_offline_fixture(self, responses):
        require(type(responses) is list, 'FIXTURE_SCHEMA', 'Explicit fixture responses required')
        return self._start(deepcopy(responses))

    def _start(self, fixture):
        identifier(self.project_id)
        identifier(self.run_id)
        # Hold the real service transaction across validation and the one-time claim.
        with transaction(self.root, self.project_id):
            from workflow_store import WorkflowStore
            run = WorkflowStore(self.root).dispatch({'action': 'read', 'project_id': self.project_id, 'run_id': self.run_id})
            spec = run['spec']
            require(spec['format'] == 'dsh-math-workflow-v2' and run['status'] in {'prepared', 'running'},
                    'RUNTIME_STATE', 'Runtime requires a prepared or started v2 run')
            require(not run.get('parent') and not run['physical_requests'] and not run['actions']
                    and not run['execution_events'] and run['usage']['requests'] == 0,
                    'RECONCILE_REQUIRED', 'Used or continued lineages cannot start a new quota')
            checker = spec['execution']['checker']
            require(type(checker) is dict and re.fullmatch(r'workflow_certificate_v2_(?:D[1-9]|H[1-3]|S1)', checker.get('id', '')),
                    'RUNTIME_CAPABILITY', 'Runtime supports qualified public certificate tasks only')
            task_id = checker['id'].rsplit('_', 1)[1]
            contract = certificate_contract(task_id)
            require(checker == contract['checker'], 'CHECKER_DRIFT', 'Frozen certificate binding differs')
            require(spec['budget'] == {'requests': 120, 'total_tokens': None, 'max_output_tokens': 128000}
                    and spec['context_budget'] == {'max_tokens': None}, 'RUNTIME_BUDGET', 'Runtime requires the qualified exact budget')
            require(spec['tools'] == tool_contracts(), 'TOOL_CAPABILITY', 'Frozen tools differ from the runtime')
            projects = Projects(self.root)
            with projects.open_project(self.project_id, require_writable=True) as (_, research):
                problem = research.get(spec['problem_ref']['id'], spec['problem_ref']['rev'])
                require(problem['kind'] == 'Problem' and all(problem['data'].get(k) == v for k, v in contract['problem_fields'].items()),
                        'TASK_DRIFT', 'ResearchStore target differs from the public certificate task')
            card = contract['task']
            source = rendered_checker(card)
            require(sha_bytes(source) == checker['code_sha256'], 'CHECKER_DRIFT', 'Rendered CommonTools checker differs')
            base = safe_path(projects.path(self.project_id), 'workflow-runtime')
            make_directory(base)
            self.directory = safe_path(base, run['lineage_root_run_id'])
            try:
                self.directory.mkdir(mode=0o700)
            except FileExistsError as error:
                raise StoreError('RECONCILE_REQUIRED', 'Existing runtime claim must be reconciled; no cold replay') from error
            sync_directory(base)
            save_json(self.directory / 'claim.json', {'format': 'dsh-math-runtime-claim-v2', 'project_id': self.project_id,
                'run_id': self.run_id, 'lineage_root_run_id': run['lineage_root_run_id'], 'spec': spec,
                'created_unix': time.time(), 'mode': 'offline-fixture' if fixture is not None else 'metered-codex'})
        try:
            isolated = self.directory / 'isolated'
            isolated.mkdir(mode=0o700)
            self.environment = isolated_environment(isolated, token=capability())
            self.environment.pop('FOUR_HARNESS_TOKEN')
            self.environment.pop('FOUR_HARNESS_OFFLINE_FIXTURE')
            public = canonical({key: card[key] for key in ('id', 'statement', 'parameters', 'certificate')}).encode()
            inputs = isolated / 'inputs'
            inputs.mkdir(mode=0o700)
            task_path = inputs / 'task.json'
            self._write_bytes(task_path, public)
            checker_path = self.directory / 'checker.py'
            self._write_bytes(checker_path, source)
            campaign = 'workflow-' + self.run_id
            self.ledger = Ledger(self.directory / 'ledger.sqlite')
            self.ledger.create_campaign(campaign, max_requests=120, max_tokens=None, scope='optimization')
            self.ledger.create_run(self.run_id, campaign, 'M', budget=OPTIMIZATION_BUDGET,
                frozen={'model': MODEL, 'task_sha256': checker['task_sha256'], 'workflow_spec_sha256': sha_bytes(canonical(spec).encode())})
            self.host = CommonTools(self.run_id, isolated / 'common',
                materials={'task.json': {'path': task_path, 'sha256': sha_bytes(public)}}, checker_path=checker_path,
                limits=ToolLimits(cpu_seconds=None, timeout_seconds=None, artifact_bytes=1073741824))
            self.guard = StateGuard([self.directory], max_bytes=1073741824, on_stop=self._stop, interval=.25)
            if fixture is None:
                from transport import HostTransport
                transport = HostTransport(profile='workflow', max_output_tokens=128000,
                    evidence_sink=self._transport_evidence)
            else:
                transport = FixtureTransport(fixture)
            bindings = {role: {'token': capability(), 'path': '/M/' + role + '/'} for role in ROLES}
            execution = {'mode': 'authorized_optimization', 'arm': 'M', 'run_id': self.run_id,
                         'campaign_id': campaign, 'model_requests': 120, 'campaign_max_requests': 120}
            self.gateway = Gateway(self.ledger, transport=transport,
                bindings={value['token']: {'run_id': self.run_id, 'arm': 'M', 'role': role} for role, value in bindings.items()},
                tools={self.run_id: self.host}, allowed_tools={self.run_id: set(TOOL_NAMES)}, inject_output_cap=True,
                execution=execution if fixture is None else None, state_guards={self.run_id: self.guard},
                frozen_materials={self.run_id: self.host.material_inventory()}, candidate_schema=CANDIDATE_SCHEMA,
                audit_sink=self._audit, drain_timeout=30).start()
            metadata = {'format': 'dsh-math-runtime-v2', 'project_id': self.project_id, 'run_id': self.run_id,
                'directory': str(self.directory), 'workspace': str(self.host.workspace.path), 'task_id': task_id,
                'task_sha256': checker['task_sha256'], 'checker_sha256': checker['code_sha256'],
                'mode': 'offline-fixture' if fixture is not None else 'metered-codex',
                'capabilities': {'qualified_certificates_only': True, 'reattach_used_lineage': False,
                    'model_surface': 'frozen-context-and-common-tools', 'compute': self.host.capabilities(),
                    'host_process': 'trusted', 'per_run_environment': True,
                    'qualification': 'requires-frozen-offline-preflight'}}
            save_json(self.directory / 'metadata.json', metadata)
            self.guard.start()
            self.monitor = threading.Thread(target=self._monitor, daemon=True)
            self.monitor.start()
            # These ephemeral credentials belong on the private fd, never in metadata/logs.
            return {'gateway': self.gateway.url, 'bindings': bindings, 'metadata': metadata}
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _write_bytes(path, raw):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(path.parent)

    def _transport_evidence(self, value):
        # HostTransport can emit synchronously while dispatch_request owns the
        # SQLite write transaction. Journal independently; the gateway appends
        # the same evidence to its ledger only after dispatch releases that lock.
        request_id = value.get('request_id')
        require(isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', request_id),
                'AUDIT_ID', 'Invalid transport receipt identity')
        raw_hash = sha_bytes(canonical(value).encode())
        save_json(self.directory / 'transport' / (request_id + '.' + raw_hash + '.json'), value)

    def _audit(self, kind, request_id, value):
        require(re.fullmatch(r'[a-z_]+', kind) and re.fullmatch(r'[A-Za-z0-9_-]+', request_id), 'AUDIT_ID', 'Invalid audit identity')
        raw_hash = sha_bytes(canonical(value).encode())
        save_json(self.directory / 'audit' / (request_id + '.' + kind + '.' + raw_hash + '.json'), value)
        self.ledger.record(self.run_id, 'payload_audited', request_id=request_id, payload_kind=kind, sha256=raw_hash)

    def _stop(self, reason):
        if self.gateway:
            self.gateway.cancel_event.set()
        if self.ledger:
            self.ledger.stop(self.run_id, reason)

    def _monitor(self):
        while not self.done.wait(.25):
            try:
                if self.ledger.snapshot(self.run_id)['terminal']:
                    self.gateway.cancel_event.set()
                    return
            except Exception:
                self._stop('runtime_monitor_failure')
                return

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
            self.done.set()
            if self.monitor:
                self.monitor.join(2)
            # Gateway drains physical receipts before durable final capture. Retain all
            # isolated dirs, submissions and logs; cleanup only our owned processes.
            try:
                if self.gateway:
                    self.gateway.close()
            finally:
                guard = self.guard.stop() if self.guard else None
                if self.ledger:
                    self.ledger.stop(self.run_id, 'runtime_closed')
                    save_json(self.directory / 'final.json', {'ledger': self.ledger.snapshot(self.run_id),
                        'events': self.ledger.events(), 'state_guard': guard,
                        'tools': self.host.capabilities() if self.host else None})


def main():
    """Private fd3 protocol. stdout/stderr never carry gateway credentials."""
    runtime = None
    # A regular terminal must never receive readiness tokens by accident.
    import stat
    require(stat.S_ISFIFO(os.fstat(3).st_mode) or stat.S_ISSOCK(os.fstat(3).st_mode),
            'PRIVATE_PIPE', 'Runtime requires an inherited private readiness pipe')
    with os.fdopen(3, 'w', buffering=1) as replies:
        try:
            packet = json.loads(sys.stdin.readline())
            runtime = WorkflowRuntime(packet['root'], packet['projectId'], packet['runId'])
            fixture_mode = sys.argv[1:] == ['--offline-fixture']
            require(not sys.argv[1:] or fixture_mode, 'RUNTIME_OPTIONS', 'Unknown runtime mode')
            if fixture_mode:
                require(packet.get('fixture', {}).get('format') == 'dsh-math-offline-responses-v1', 'FIXTURE_SCHEMA', 'Explicit fixture envelope required')
                ready = runtime.start_offline_fixture(packet['fixture']['responses'])
            else:
                require('fixture' not in packet, 'RUNTIME_OPTIONS', 'Product runtime does not accept test transport')
                ready = runtime.start()
            os.environ.clear()
            os.environ.update(runtime.environment)
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
            replies.write(canonical({'ok': True, **ready}) + '\n')
            for line in sys.stdin:
                require(json.loads(line) == {'op': 'close'}, 'RUNTIME_OPTIONS', 'Unknown control message')
                break
        except BaseException as error:
            # Never forward transport/auth exception text, stderr, or request bodies.
            replies.write(canonical({'ok': False, 'code': getattr(error, 'code', 'RUNTIME_FAILED')}) + '\n')
        finally:
            if runtime:
                runtime.close()


if __name__ == '__main__':
    main()

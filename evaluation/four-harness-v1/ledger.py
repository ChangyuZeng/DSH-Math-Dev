"""Host-owned, transactional accounting for the four-harness experiment.

This module cannot call a model. Reservations are committed before dispatch; a
lost outcome keeps its reservation and closes the entire campaign to dispatch.
No method resumes, retries, replaces, or deletes a request. SQLite serializes
all roles/processes, including native harness descendants.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

POLICY = {"provider": "openai-codex", "model": "gpt-6-astra", "effort": "max"}
FORMAL_BUDGET = dict(wall_seconds=7200, model_requests=120, total_tokens=1000000,
                     max_output_tokens=16384, context_tokens=65536, tool_calls=500,
                     artifact_bytes=1073741824, tool_timeout_seconds=60)
OPTIMIZATION_BUDGET = dict(wall_seconds=None, model_requests=120, total_tokens=None,
                           max_output_tokens=128000, context_tokens=272000, tool_calls=1000000,
                           artifact_bytes=1073741824, tool_timeout_seconds=None)
SMOKE_BUDGET = {**FORMAL_BUDGET, 'wall_seconds': 180, 'model_requests': 2, 'total_tokens': 131072,
                'max_output_tokens': 2048, 'tool_calls': 8,
                'tool_timeout_seconds': 10, 'artifact_bytes': 16777216}
RESOURCE_POLICY = {'mode': 'observed', 'scope': 'per_tool_and_sampled_native_process_tree',
                   'equal_cpu_or_memory_budget': False, 'whole_process_tree_complete': False,
                   'cpu_core_limit': None, 'aggregate_cpu_limit': None,
                   'host_protection_is_fair_budget': False,
                   'missing_observations': None, 'nested_tool_cpu_additive': False,
                   'process_and_tool_cpu_additive': False}
ROLES = {"C": {"agent", "compaction"}, "X": {"coordinator", "explorer", "verifier", "compaction"},
         "R": {"generator", "strategy", "verifier", "repair", "compaction"},
         "M": {"worker", "verifier", "reviser", "summary", "compaction"}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class GateError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise GateError(code, message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def normalize_usage(raw, *, source="responses.completed", terminal=True):
    """Responses API semantics: cached/reasoning are subsets, never additions.

    Missing details remain null. Core totals can be known while detail_complete
    is false; missing input/output/total prevents further dispatch. Inconsistent
    counters are also unknown, with raw evidence retained.
    """
    raw = raw if isinstance(raw, dict) else {}
    fields = {key: raw.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")}
    for key in fields:
        if not integer(fields[key]):
            fields[key] = None
    details_in = raw.get("input_tokens_details") or {}
    details_out = raw.get("output_tokens_details") or {}
    cached = details_in.get("cached_tokens") if isinstance(details_in, dict) else None
    reasoning = details_out.get("reasoning_tokens") if isinstance(details_out, dict) else None
    cached = cached if integer(cached) else None
    reasoning = reasoning if integer(reasoning) else None
    complete = terminal and all(value is not None for value in fields.values())
    if complete:
        complete = fields["total_tokens"] == fields["input_tokens"] + fields["output_tokens"]
        complete = complete and (cached is None or cached <= fields["input_tokens"])
        complete = complete and (reasoning is None or reasoning <= fields["output_tokens"])
    return {**fields, "cached_input_tokens": cached, "reasoning_tokens": reasoning,
            "source": source, "complete": bool(complete),
            "detail_complete": bool(complete and cached is not None and reasoning is not None)}


def native_identity(raw):
    """Keep only audited Codex lineage fields, not workspace or installation data."""
    require(isinstance(raw, dict), "NATIVE_METADATA", "Native turn metadata must be an object")
    keys = ("thread_id", "turn_id", "parent_thread_id", "parent_turn_id", "root_turn_id", "subagent_kind", "request_kind")
    result = {key: raw.get(key) for key in keys}
    require(all(value is None or (isinstance(value, str) and 0 < len(value) <= 200 and
                all(ord(char) >= 32 for char in value)) for value in result.values()),
            "NATIVE_METADATA", "Invalid native lineage identifier")
    require(result["thread_id"] is not None, "NATIVE_METADATA", "Native thread identity is required")
    require(result["request_kind"] in {None, "turn", "compaction"}, "NATIVE_KIND_UNQUALIFIED", "Native prewarm/memory path is not qualified")
    require(not raw.get("forked_from_thread_id"), "NATIVE_FORK_UNQUALIFIED", "Native fork ancestry must be qualified separately")
    require(result["parent_turn_id"] is None or result["parent_thread_id"] is not None,
            "NATIVE_METADATA", "Parent turn requires a parent thread")
    require(result["subagent_kind"] is None or result["parent_thread_id"] is not None,
            "NATIVE_PARENT_UNKNOWN", "Native child must identify its parent thread")
    return result


TRANSIENT_CODES = frozenset({'server_is_overloaded', 'server_error', 'internal_server_error',
    'rate_limit_exceeded', 'service_unavailable_error', 'http_429', 'http_500', 'http_502', 'http_503', 'http_504'})
NONTRANSIENT_CODES = frozenset({'invalid_api_key', 'authentication_error', 'unauthorized', 'permission_denied',
    'insufficient_quota', 'billing_hard_limit_reached', 'model_not_found', 'invalid_request_error',
    'invalid_parameter', 'unsupported_parameter', 'context_length_exceeded'})


def retry_error_schema_valid(error):
    return (error is None or (isinstance(error, dict) and
        all(error.get(key) is None or isinstance(error[key], str) for key in ('code', 'type')) and
        (error.get('headers') is None or isinstance(error['headers'], dict))))


def usage_schema_valid(raw):
    """Missing counters stay unknown; malformed or contradictory counters are not retry proof."""
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return False
    counters = {key: raw.get(key) for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    for details, key in (('input_tokens_details', 'cached_tokens'), ('output_tokens_details', 'reasoning_tokens')):
        value = raw.get(details)
        if value is not None and not isinstance(value, dict):
            return False
        counters[key] = value.get(key) if isinstance(value, dict) else None
    if any(value is not None and (type(value) is not int or value < 0) for value in counters.values()):
        return False
    incoming, outgoing, total = (counters[key] for key in ('input_tokens', 'output_tokens', 'total_tokens'))
    if all(value is not None for value in (incoming, outgoing, total)) and total != incoming + outgoing:
        return False
    return all(part is None or whole is None or part <= whole for part, whole in
               ((incoming, total), (outgoing, total), (counters['cached_tokens'], incoming),
                (counters['reasoning_tokens'], outgoing), (counters['cached_tokens'], total),
                (counters['reasoning_tokens'], total)))


def retryable_response(response):
    if (not isinstance(response, dict) or response.get('status') != 'failed' or response.get('output') != [] or
            response.get('object') not in (None, 'response') or
            ('id' in response and (not isinstance(response['id'], str) or not response['id'])) or
            not usage_schema_valid(response.get('usage'))):
        return False
    error = response.get('error')
    reasoning = response.get('reasoning')
    if reasoning is not None and not isinstance(reasoning, dict):
        return False
    effort = reasoning.get('effort') if isinstance(reasoning, dict) else None
    output_tokens = normalize_usage(response.get('usage'), terminal=False)['output_tokens']
    return (isinstance(error, dict) and retry_error_schema_valid(error) and error.get('code') in TRANSIENT_CODES
            and error.get('type') not in NONTRANSIENT_CODES
            and output_tokens in (None, 0) and not response.get('partial_output_observed')
            and response.get('model') in (None, POLICY['model']) and effort in (None, POLICY['effort']))


def retryable_transport_failure(error):
    """Only instrumented connection failures with proof that HTTP send never began."""
    evidence = getattr(error, 'evidence', None)
    return (isinstance(error, GateError) and error.code == 'TRANSPORT_SEND' and isinstance(evidence, dict)
            and evidence.get('format') == 'four-harness-transport-v2'
            and evidence.get('stage') in {'dns', 'tcp', 'tls'} and evidence.get('send_state') == 'not_started'
            and evidence.get('failure_kind') in {'dns_temporary', 'timeout', 'connection_refused',
                'connection_reset', 'network_unreachable', 'host_unreachable'}
            and not evidence.get('partial_output_observed'))


def retry_delay(response, attempt):
    raw = response.get('error', {}).get('headers')
    headers = {str(k).lower(): v for k, v in raw.items()} if isinstance(raw, dict) else {}
    for key, scale in [('retry-after-ms', .001), ('retry-after', 1.)]:
        try:
            value = float(headers[key]) * scale
            if math.isfinite(value) and value >= 0:
                return max(1., value)
        except (KeyError, ValueError, TypeError):
            pass
    if 'retry-after' in headers:
        try:
            from email.utils import parsedate_to_datetime
            return max(1., parsedate_to_datetime(headers['retry-after']).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            pass
    return min(60., 5. * 2 ** min(max(0, attempt - 1), 4))


class Ledger:
    def __init__(self, path):
        self.path = Path(path).absolute()
        for part in [self.path, *self.path.parents]:
            if part.is_symlink():
                raise GateError("UNSAFE_PATH", "Ledger path contains a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists():
            info = self.path.stat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_PATH", "Unsafe ledger file")
        else:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign (id TEXT PRIMARY KEY, spec TEXT NOT NULL, terminal TEXT);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, campaign TEXT NOT NULL REFERENCES campaign(id),
                    arm TEXT NOT NULL, spec TEXT NOT NULL, created REAL NOT NULL, monotonic REAL NOT NULL, terminal TEXT);
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id),
                    role TEXT NOT NULL, parent TEXT, thread TEXT, state TEXT NOT NULL, reservation INTEGER NOT NULL,
                    input_bound INTEGER NOT NULL, output_cap INTEGER NOT NULL, payload_hash TEXT NOT NULL,
                    usage TEXT, raw_usage TEXT, response_id TEXT);
                CREATE TABLE IF NOT EXISTS tools (id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id),
                    state TEXT NOT NULL, cpu_reservation REAL NOT NULL, cpu REAL, metrics TEXT);
                CREATE TABLE IF NOT EXISTS native_threads (run TEXT NOT NULL REFERENCES runs(id), thread TEXT NOT NULL,
                    parent_thread TEXT, parent_turn TEXT, spawn_parent TEXT, PRIMARY KEY(run,thread));
                CREATE TABLE IF NOT EXISTS native_turns (run TEXT NOT NULL REFERENCES runs(id), thread TEXT NOT NULL,
                    turn TEXT NOT NULL, latest_request TEXT NOT NULL REFERENCES requests(id), PRIMARY KEY(run,thread,turn));
                CREATE TABLE IF NOT EXISTS native_requests (request TEXT PRIMARY KEY REFERENCES requests(id),
                    run TEXT NOT NULL REFERENCES runs(id), thread TEXT NOT NULL, turn TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_turn_ancestry (run TEXT NOT NULL REFERENCES runs(id), thread TEXT NOT NULL,
                    turn TEXT NOT NULL, parent_thread TEXT, parent_turn TEXT, origin_parent TEXT,
                    PRIMARY KEY(run,thread,turn));
                CREATE TABLE IF NOT EXISTS dispatches (request TEXT PRIMARY KEY REFERENCES requests(id));
                CREATE TABLE IF NOT EXISTS request_context (request TEXT PRIMARY KEY REFERENCES requests(id), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS response_opaque (request TEXT PRIMARY KEY REFERENCES requests(id), items TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, hash TEXT NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _event(self, db, run_id, kind, **data):
        last = db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone() if run_id else None
        event = dict(event_id=str(uuid.uuid4()), run_id=run_id, seq=last["seq"] + 1 if last else 1,
                     type=kind, utc_time=datetime.now(timezone.utc).isoformat(),
                     monotonic_elapsed=max(0, time.monotonic() - run["monotonic"]) if run else None,
                     previous_hash=last["hash"] if last else "0" * 64, **data)
        db.execute("INSERT INTO events(event,hash) VALUES(?,?)", (canonical(event), digest(event)))
        return event

    def create_campaign(self, campaign_id, *, max_requests, max_tokens, scope="offline"):
        require(scope in {"offline", "smoke", "formal", "optimization"} and integer(max_requests, 1)
                and (integer(max_tokens, 1) or scope == 'optimization' and max_tokens is None),
                "INVALID_SPEC", "Invalid campaign limits")
        if scope == 'optimization':
            require(max_requests <= 120 and max_tokens is None, 'BUDGET',
                    'Optimization campaigns contain exactly one solve with at most 120 physical requests')
        if scope == "smoke":
            require(max_requests <= 8 and max_tokens <= 524288, "BUDGET", "Smoke campaign ceiling is eight requests and 524288 tokens")
        if scope == "formal":
            require(max_requests <= 2400 and max_tokens <= 20000000, "BUDGET", "Formal campaign exceeds the frozen twenty-run ceiling")
        with self.transaction() as db:
            require(db.execute("SELECT 1 FROM campaign WHERE id=?", (campaign_id,)).fetchone() is None,
                    "ALREADY_EXISTS", "Campaign identity may not be reused")
            db.execute("INSERT INTO campaign VALUES(?,?,NULL)",
                       (campaign_id, canonical(dict(max_requests=max_requests, max_tokens=max_tokens, scope=scope))))
            self._event(db, None, "campaign_created", campaign_id=campaign_id, scope=scope)

    def create_run(self, run_id, campaign_id, arm, *, budget=None, frozen=None):
        budget = dict(FORMAL_BUDGET if budget is None else budget)
        require(arm in ROLES and set(budget) == set(FORMAL_BUDGET) and
                all(integer(v, 1) or key in {'wall_seconds', 'total_tokens', 'tool_timeout_seconds'} and v is None
                    for key, v in budget.items()), "INVALID_SPEC", "Invalid arm or complete budget")
        require(budget["max_output_tokens"] < budget["context_tokens"], "INVALID_SPEC", "Context must include output")
        with self.transaction() as db:
            campaign = db.execute("SELECT * FROM campaign WHERE id=?", (campaign_id,)).fetchone()
            require(campaign is not None and campaign["terminal"] is None, "CAMPAIGN_CLOSED", "Campaign unavailable")
            spec = json.loads(campaign["spec"])
            require(not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone(),
                    "ALREADY_EXISTS", "Never reuse run identity or reset its costs")
            if spec['scope'] == 'optimization':
                require(arm == 'M' and budget == OPTIMIZATION_BUDGET, 'BUDGET', 'Optimization budget must be exact')
                require(not db.execute('SELECT 1 FROM runs WHERE campaign=?', (campaign_id,)).fetchone(),
                        'NO_REPLACEMENT', 'One optimization solve per campaign')
            else:
                require(all(integer(value, 1) for value in budget.values()), 'INVALID_SPEC',
                        'Legacy scopes retain finite budgets')
            if spec["scope"] in {"smoke", "formal"}:
                ceiling = SMOKE_BUDGET if spec["scope"] == "smoke" else FORMAL_BUDGET
                require(all(budget[key] <= ceiling[key] for key in ceiling), "BUDGET", "Run exceeds its frozen scope budget")
                slots = 1 if spec["scope"] == "smoke" else 5
                occupied = db.execute("SELECT COUNT(*) FROM runs WHERE campaign=? AND arm=?", (campaign_id, arm)).fetchone()[0]
                require(occupied < slots, "NO_REPLACEMENT", "All authorized arm slots are already occupied")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,NULL)",
                       (run_id, campaign_id, arm, canonical(dict(budget=budget, frozen=frozen or {}, model=POLICY)),
                        time.time(), time.monotonic()))
            self._event(db, run_id, "run_created", arm=arm, frozen=frozen or {}, budget=budget,
                        resource_policy=RESOURCE_POLICY)

    def _run(self, db, run_id, *, active=True):
        run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        require(run is not None, "NOT_FOUND", "Run not found")
        if active:
            campaign = db.execute("SELECT terminal FROM campaign WHERE id=?", (run["campaign"],)).fetchone()
            require(run["terminal"] is None and campaign["terminal"] is None, "STOPPED", "Dispatch is permanently closed")
            budget = json.loads(run["spec"])["budget"]
            elapsed = max(time.time() - run["created"], time.monotonic() - run["monotonic"])
            require(time.monotonic() >= run["monotonic"], "CLOCK_CHANGED", "Monotonic clock changed; reconcile only")
            require(budget['wall_seconds'] is None or elapsed < budget["wall_seconds"], "WALL_BUDGET", "Run wall budget exhausted")
        return run

    @staticmethod
    def _known_lower_bound(usage):
        if not usage:
            return 0
        # Subsets also prove lower bounds when enclosing counters are absent or
        # contradictory. Never add a subset twice when its enclosing count exists.
        observed_input = max(usage.get("input_tokens") or 0, usage.get("cached_input_tokens") or 0)
        observed_output = max(usage.get("output_tokens") or 0, usage.get("reasoning_tokens") or 0)
        return max(usage.get("total_tokens") or 0, observed_input + observed_output)

    @staticmethod
    def _cost(rows):
        total = 0
        for row in rows:
            usage = json.loads(row["usage"]) if row["usage"] else None
            known = Ledger._known_lower_bound(usage)
            total += known if usage and usage["complete"] else max(row["reservation"], known)
        return total

    @staticmethod
    def _native_lookup(db, run_id, thread_id, turn_id=None, *, unique_physical=False, explicit_request=None):
        # A parent turn may contain several physical requests. A child arriving
        # after its parent continues cannot be attributed to the newest request.
        query = ("SELECT request AS latest_request FROM native_requests" if unique_physical else
                 "SELECT latest_request FROM native_turns") + " WHERE run=? AND thread=?"
        args = [run_id, thread_id]
        if turn_id is not None:
            query += " AND turn=?"
            args.append(turn_id)
        if unique_physical and explicit_request:
            query += " AND request=?"
            args.append(explicit_request)
        matches = db.execute(query, args).fetchall()
        require(len(matches) == 1, "NATIVE_PARENT_UNKNOWN", "Native thread/turn parent is unknown or ambiguous")
        request_id = matches[0]["latest_request"]
        request = db.execute("SELECT state FROM requests WHERE id=? AND run=?", (request_id, run_id)).fetchone()
        require(request is not None and request["state"] == "finished", "NATIVE_PARENT_UNKNOWN", "Native parent has no completed metered receipt")
        return request_id

    def resolve_native_request(self, run_id, metadata):
        """Resolve an MCP caller's native identity; never guess across threads/runs."""
        metadata = native_identity(metadata)
        with self.connection() as db:
            self._run(db, run_id, active=False)
            return self._native_lookup(db, run_id, metadata["thread_id"], metadata.get("turn_id"))

    @staticmethod
    def _context_bound(db, run_id, role, payload, parent_request_id, thread_id, lineage_kind):
        """Measured identical-prefix continuation; no tokenizer alias or truncation.

        An opaque reasoning item can hide more tokens than its serialized bytes.
        Charge the parent's entire measured output again in addition to every new
        serialized byte. Missing/inexact prefix evidence retains the full-byte bound.
        """
        full = len(canonical(payload).encode()) + 4096
        evidence = {'method':'full_utf8_plus4096','full_utf8_bound':full}
        if not parent_request_id:
            return full, evidence
        parent = db.execute('SELECT * FROM requests WHERE id=? AND run=? AND role=? AND state=\'finished\'',
                            (parent_request_id,run_id,role)).fetchone()
        context = db.execute('SELECT payload FROM request_context WHERE request=?',(parent_request_id,)).fetchone()
        if (parent is None or context is None or parent['usage'] is None or parent['thread']!=thread_id or
                role=='compaction' or lineage_kind not in {None,'same_native_turn_continuation'}):
            return full, evidence
        usage = json.loads(parent['usage'])
        if not usage.get('detail_complete'):
            return full, evidence
        previous = json.loads(context['payload'])
        require(digest(previous)==parent['payload_hash'],'CONTEXT_EVIDENCE_DRIFT','Stored parent context changed')
        fixed = lambda value:{key:item for key,item in value.items() if key not in {'input','client_metadata'}}
        before, after = previous.get('input'), payload.get('input')
        if (fixed(previous)!=fixed(payload) or type(before) is not list or not before or type(after) is not list or
                len(after)<=len(before) or after[:len(before)]!=before):
            return full, evidence
        suffix = after[len(before):]
        opaque = [{'id':item.get('id'),'encrypted_sha256':digest(item['encrypted_content'])}
                  for item in suffix if isinstance(item,dict) and item.get('type')=='reasoning' and
                  item.get('encrypted_content') is not None]
        if opaque:
            stored = db.execute('SELECT items FROM response_opaque WHERE request=?',(parent_request_id,)).fetchone()
            if stored is None or any(item not in json.loads(stored['items']) for item in opaque):
                return full, evidence
        appended = len(canonical(suffix).encode())
        metadata = (len(canonical(payload.get('client_metadata')).encode())
                    if previous.get('client_metadata')!=payload.get('client_metadata') else 0)
        bound = usage['input_tokens'] + usage['output_tokens'] + appended + metadata + 4096
        if bound >= full:
            return full, evidence
        return bound, {'method':'measured_identical_prefix_plus_output_and_utf8_v1','parent_request_id':parent_request_id,
            'parent_payload_sha256':parent['payload_hash'],'provider_input_tokens':usage['input_tokens'],
            'provider_output_tokens':usage['output_tokens'],'appended_utf8_bytes':appended,
            'appended_sha256':digest(suffix),'metadata_utf8_bytes':metadata,'framing_tokens_reserved':4096,
            'fixed_fields_sha256':digest(fixed(previous)),'prefix_sha256':digest(before),
            'provider_response_id':parent['response_id'],'opaque_item_bindings':opaque,'full_utf8_bound':full}

    def reserve_request(self, run_id, role, payload, *, parent_request_id=None, thread_id=None, native_metadata=None, retry_of=None):
        require(isinstance(payload, dict), "INVALID_REQUEST", "Expected Responses request object")
        native = native_identity(native_metadata) if native_metadata is not None else None
        with self.transaction() as db:
            run = self._run(db, run_id)
            budget = json.loads(run["spec"])["budget"]
            require(role in ROLES[run["arm"]], "UNKNOWN_ROLE", "Every model caller needs a frozen role")
            require(payload.get("model") == POLICY["model"] and
                    isinstance(payload.get("reasoning"), dict) and payload["reasoning"].get("effort") == POLICY["effort"],
                    "MODEL_DRIFT", "Request differs from frozen model/effort")
            output_cap = payload.get("max_output_tokens")
            require(integer(output_cap, 1) and output_cap <= budget["max_output_tokens"],
                    "OUTPUT_CAP", "Explicit bounded output is required")
            require(not payload.get("previous_response_id"), "HIDDEN_CONTEXT", "Full request context is required for reservation")
            lineage_kind = None
            retry = None
            if retry_of:
                retry = db.execute('SELECT * FROM requests WHERE id=?', (retry_of,)).fetchone()
                receipt = db.execute("SELECT event FROM events WHERE json_extract(event,'$.request_id')=? AND json_extract(event,'$.retryable')=1 ORDER BY seq DESC LIMIT 1", (retry_of,)).fetchone()
                used = db.execute("SELECT 1 FROM events WHERE json_extract(event,'$.retry_of')=?", (retry_of,)).fetchone()
                require(budget == OPTIMIZATION_BUDGET and retry is not None and receipt is not None and not used
                        and retry['run'] == run_id and retry['role'] == role and retry['payload_hash'] == digest(payload)
                        and retry['state'] in {'unknown', 'finished'}, 'RETRY_IDENTITY',
                        'Retry requires an unused explicit transient receipt and identical same-role request')
            if native:
                require(thread_id is None or thread_id == native["thread_id"], "NATIVE_METADATA", "Explicit and native thread identities disagree")
                thread_id = native["thread_id"]
                prior = db.execute("SELECT * FROM native_threads WHERE run=? AND thread=?", (run_id, thread_id)).fetchone()
                if prior:
                    require(prior["parent_thread"] == native["parent_thread_id"],
                            "NATIVE_LINEAGE_DRIFT", "Native parent thread changed")
                previous = db.execute("SELECT latest_request FROM native_turns WHERE run=? AND thread=? AND turn=?",
                                      (run_id, thread_id, native["turn_id"] or "")).fetchone()
                if previous:
                    ancestry = db.execute("SELECT * FROM native_turn_ancestry WHERE run=? AND thread=? AND turn=?",
                                          (run_id, thread_id, native["turn_id"] or "")).fetchone()
                    require(ancestry is not None, "NATIVE_LINEAGE_UNKNOWN", "Prior native turn lacks immutable ancestry evidence")
                    require(ancestry["parent_thread"] == native["parent_thread_id"] and ancestry["parent_turn"] == native["parent_turn_id"],
                            "NATIVE_LINEAGE_DRIFT", "Parent ancestry changed within one native turn")
                    spawn_parent = ancestry["origin_parent"]
                    if retry is not None:
                        require(previous['latest_request'] == retry_of and retry['thread'] == thread_id,
                                'RETRY_IDENTITY', 'Retry must follow the latest failed attempt in this native turn')
                        resolved_parent = retry['parent']
                        lineage_kind = 'native_provider_retry'
                    else:
                        resolved_parent = self._native_lookup(db, run_id, thread_id, native["turn_id"] or "")
                        lineage_kind = "same_native_turn_continuation"
                else:
                    # Codex can reuse a child thread from a later parent turn.
                    # Preserve original spawn evidence, bind each new native turn separately.
                    if native["parent_thread_id"]:
                        require(native["parent_thread_id"] != thread_id, "NATIVE_METADATA", "Native child cannot parent itself")
                        spawn_parent = self._native_lookup(db, run_id, native["parent_thread_id"], native["parent_turn_id"],
                                                           unique_physical=True, explicit_request=parent_request_id)
                    else:
                        spawn_parent = parent_request_id
                    resolved_parent = spawn_parent
                    lineage_kind = ("native_child_followup" if prior else "native_spawn") if native["parent_thread_id"] else "host_parent_or_root"
                require(parent_request_id is None or parent_request_id == resolved_parent or
                        (previous is not None and parent_request_id == spawn_parent),
                        "NATIVE_LINEAGE_DRIFT", "Explicit parent contradicts native lineage")
                parent_request_id = resolved_parent
            if parent_request_id:
                parent = db.execute("SELECT run FROM requests WHERE id=?", (parent_request_id,)).fetchone()
                require(parent is not None and parent["run"] == run_id, "CROSS_RUN", "Parent request belongs to another run")
            rows = db.execute("SELECT * FROM requests WHERE run=?", (run_id,)).fetchall()
            require(len(rows) < budget["model_requests"], "REQUEST_BUDGET", "All-role request budget exhausted")
            input_bound, context_evidence = self._context_bound(db,run_id,role,payload,parent_request_id,thread_id,lineage_kind)
            # UTF-8 length is a byte safety estimate, not measured model tokens.
            # The optimization profile delegates token-window rejection to the server.
            if budget != OPTIMIZATION_BUDGET:
                require(input_bound + output_cap <= budget['context_tokens'], 'CONTEXT_BUDGET', 'Request exceeds context budget')
            reservation = input_bound + output_cap
            require(budget['total_tokens'] is None or self._cost(rows) + reservation <= budget["total_tokens"], "TOKEN_BUDGET", "Cannot reserve next request")
            campaign = db.execute("SELECT * FROM campaign WHERE id=?", (run["campaign"],)).fetchone()
            caps = json.loads(campaign["spec"])
            all_rows = db.execute("SELECT requests.* FROM requests JOIN runs ON requests.run=runs.id WHERE runs.campaign=?",
                                  (run["campaign"],)).fetchall()
            require(len(all_rows) < caps["max_requests"] and
                    (caps['max_tokens'] is None or self._cost(all_rows) + reservation <= caps["max_tokens"]),
                    "CAMPAIGN_BUDGET", "Shared campaign budget exhausted")
            request_id = str(uuid.uuid4())
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,'reserved',?,?,?,?,NULL,NULL,NULL)",
                       (request_id, run_id, role, parent_request_id, thread_id, reservation, input_bound, output_cap, digest(payload)))
            db.execute('INSERT INTO request_context VALUES(?,?)',(request_id,canonical(payload)))
            if native:
                db.execute("INSERT INTO native_requests VALUES(?,?,?,?)", (request_id, run_id, thread_id, native["turn_id"] or ""))
                if prior is None:
                    db.execute("INSERT INTO native_threads VALUES(?,?,?,?,?)",
                               (run_id, thread_id, native["parent_thread_id"], native["parent_turn_id"], spawn_parent))
                if previous is None:
                    db.execute("INSERT INTO native_turn_ancestry VALUES(?,?,?,?,?,?)",
                               (run_id, thread_id, native["turn_id"] or "", native["parent_thread_id"], native["parent_turn_id"], spawn_parent))
                db.execute("INSERT INTO native_turns VALUES(?,?,?,?) ON CONFLICT(run,thread,turn) DO UPDATE SET latest_request=excluded.latest_request",
                           (run_id, thread_id, native["turn_id"] or "", request_id))
            self._event(db, run_id, "request_reserved", request_id=request_id, role=role,
                        parent_request_id=parent_request_id, thread_id=thread_id, input_bound=input_bound,
                        output_cap=output_cap, reservation=reservation, payload_sha256=digest(payload), model=POLICY,
                        native_metadata=native, lineage_kind=lineage_kind, context_bound_evidence=context_evidence, retry_of=retry_of)
            return request_id

    def start_request(self, request_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            require(row is not None and row["state"] == "reserved", "NO_REPLAY", "Request already dispatched or unknown")
            self._run(db, row["run"])
            db.execute("UPDATE requests SET state='started' WHERE id=?", (request_id,))
            self._event(db, row["run"], "request_started", request_id=request_id, role=row["role"], model=POLICY,
                        parent_request_id=row["parent"], thread_id=row["thread"])

    def dispatch_request(self,request_id,start, *, recover_not_sent=False):
        """Serialize the real synchronous send with permanent campaign closure.

        The starter must return promptly after initiating I/O, never wait for a
        provider receipt or mutate this ledger synchronously. Reservation is
        already durable; a send exception remains an unknown started request.
        """
        failure=None
        with self.transaction() as db:
            row=db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            require(row is not None,'NOT_FOUND','Request not found')
            self._run(db,row['run'])
            require(row['state']=='started','NO_REPLAY','Only a started request may dispatch')
            require(not db.execute('SELECT 1 FROM dispatches WHERE request=?',(request_id,)).fetchone(),
                    'NO_REPLAY','Request dispatch was already admitted')
            db.execute('INSERT INTO dispatches VALUES(?)',(request_id,))
            try:
                result=start()
            except BaseException as error:
                failure=error
                db.execute("UPDATE requests SET state='unknown' WHERE id=?",(request_id,))
                budget = json.loads(self._run(db, row['run'], active=False)['spec'])['budget']
                recoverable = (recover_not_sent and budget == OPTIMIZATION_BUDGET and retryable_transport_failure(error))
                # Legacy callers still close on every synchronous failure. The gateway
                # alone opts into a new physical attempt; this method never resends.
                self._event(db,row['run'],'request_unknown',request_id=request_id,role=row['role'],
                            reservation_retained=row['reservation'],reason=getattr(error,'code','transport_start_failed'),
                            retryable=bool(recoverable))
                if not recoverable:
                    self._close(db,row['run'],getattr(error,'code','transport_start_failed'),campaign=True)
            else:
                self._event(db,row['run'],'request_dispatched',request_id=request_id,
                            boundary='synchronous_transport_start_under_campaign_write_lock')
        if failure is not None:
            raise failure
        return result

    def finish_request(self, request_id, response, *, terminal=True, source="responses.completed", late_receipt=False, recoverable=False):
        """Record a receipt without dispatch; late reconciliation never reopens a run."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            allowed = {"started", "unknown"} if late_receipt else {"started"}
            require(row is not None and row["state"] in allowed, "NO_REPLAY", "Request has no open dispatch")
            late = row["state"] == "unknown"
            if late:
                run = self._run(db, row["run"], active=False)
                require(run["terminal"] is not None, "NO_REPLAY", "Late receipt requires a permanently stopped run")
            response = response if isinstance(response, dict) else {}
            usage = normalize_usage(response.get("usage"), source=source, terminal=terminal)
            if row["usage"]:
                previous_usage = json.loads(row["usage"])
                fields = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")
                require(all(previous_usage[key] is None or
                            (usage[key] is not None and usage[key] >= previous_usage[key]) for key in fields),
                        "RECEIPT_REGRESSION", "Late receipt cannot erase or reduce an observed counter")
                require(row["response_id"] is None or response.get("id") == row["response_id"],
                        "RECEIPT_IDENTITY", "Late receipt must describe the same provider request")
            model_ok = response.get("model") == POLICY["model"]
            reasoning = response.get("reasoning")
            actual_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
            budget = json.loads(self._run(db, row['run'], active=False)['spec'])['budget']
            recoverable = bool(recoverable and terminal and retryable_response(response) and budget == OPTIMIZATION_BUDGET)
            reason = None
            if recoverable:
                reason = None
            elif not usage["complete"]:
                reason = "usage_unknown"
            elif not model_ok:
                reason = "response_model_drift"
            elif actual_effort is not None and actual_effort != POLICY["effort"]:
                reason = "response_effort_drift"
            elif usage["output_tokens"] > row["output_cap"] or (budget != OPTIMIZATION_BUDGET and usage["input_tokens"] > row["input_bound"]):
                reason = "reservation_exceeded"
            state = "finished" if usage["complete"] else "unknown"
            db.execute("UPDATE requests SET state=?,usage=?,raw_usage=?,response_id=? WHERE id=?",
                       (state, canonical(usage), canonical(response.get("usage")), response.get("id"), request_id))
            if state=='finished' and reason is None:
                opaque=[{'id':item.get('id'),'encrypted_sha256':digest(item['encrypted_content'])}
                    for item in response.get('output',[]) if isinstance(item,dict) and item.get('type')=='reasoning' and
                    item.get('encrypted_content') is not None] if isinstance(response.get('output'),list) else []
                db.execute('INSERT OR REPLACE INTO response_opaque VALUES(?,?)',(request_id,canonical(opaque)))
            self._event(db, row["run"], "request_finished" if state == "finished" else "request_unknown",
                        request_id=request_id, role=row["role"], parent_request_id=row["parent"], thread_id=row["thread"],
                        usage=usage, raw_usage=response.get("usage"), actual_model=response.get("model"), actual_effort=actual_effort,
                        provider_request_id=response.get("id"), finish_reason=response.get("status"), anomaly=reason,
                         late_receipt=late, retryable=recoverable)
            if reason:
                self._close(db, row["run"], reason, campaign=True)
            return usage

    def unknown_request(self, request_id, reason="transport_lost"):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            require(row is not None, "NOT_FOUND", "Unknown request identity")
            if row["state"] in {"unknown", "finished"}:
                return
            db.execute("UPDATE requests SET state='unknown' WHERE id=?", (request_id,))
            self._event(db, row["run"], "request_unknown", request_id=request_id, role=row["role"],
                        reservation_retained=row["reservation"], reason=reason)
            self._close(db, row["run"], reason, campaign=True)

    def _close(self, db, run_id, reason, *, campaign=False):
        run = self._run(db, run_id, active=False)
        if run["terminal"] is None:
            db.execute("UPDATE runs SET terminal=? WHERE id=?", (reason, run_id))
            self._event(db, run_id, "terminal", reason=reason)
        if campaign:
            db.execute("UPDATE campaign SET terminal=COALESCE(terminal,?) WHERE id=?", (reason, run["campaign"]))

    def stop(self, run_id, reason="cancelled"):
        with self.transaction() as db:
            self._close(db, run_id, reason)
        self.reconcile(run_id)

    def reconcile(self, run_id):
        """Crash closeout only; no dispatch path and no automatic cost release."""
        with self.transaction() as db:
            self._run(db, run_id, active=False)
            rows = db.execute("SELECT * FROM requests WHERE run=? AND state IN ('reserved','started')", (run_id,)).fetchall()
            for row in rows:
                db.execute("UPDATE requests SET state='unknown' WHERE id=?", (row["id"],))
                self._event(db, run_id, "request_unknown", request_id=row["id"], role=row["role"],
                            reason="crash_reconciliation", reservation_retained=row["reservation"])
            tools = db.execute("SELECT * FROM tools WHERE run=? AND state='started'", (run_id,)).fetchall()
            for tool in tools:
                db.execute("UPDATE tools SET state='unknown' WHERE id=?", (tool["id"],))
                self._event(db, run_id, "tool_unknown", tool_id=tool["id"], cpu_reservation=tool["cpu_reservation"])
            if rows or tools:
                self._close(db, run_id, "needs_reconcile", campaign=bool(rows))
        return self.snapshot(run_id)

    def tool_event(self, run_id, event):
        """CommonTools callback: reservation happens before a tool is executed."""
        with self.transaction() as db:
            kind = event.get("type", event.get("event"))
            tool_id = event.get("request_id", event.get("tool_id"))
            require(isinstance(tool_id, str) and tool_id, "TOOL_SCHEMA", "Tool identity required")
            if kind == "tool_started":
                run = self._run(db, run_id)
                budget = json.loads(run["spec"])["budget"]
                rows = db.execute("SELECT * FROM tools WHERE run=?", (run_id,)).fetchall()
                require(len(rows) < budget["tool_calls"], "TOOL_BUDGET", "Tool call limit exhausted")
                # CPU is an observation under the Mac cold-start protocol, not a
                # fair budget. The legacy column records zero reserved CPU only;
                # an absent observation remains SQL NULL and is never cost zero.
                db.execute("INSERT INTO tools VALUES(?,?,'started',0,NULL,NULL)", (tool_id, run_id))
            elif kind == "tool_finished":
                row = db.execute("SELECT * FROM tools WHERE id=? AND run=?", (tool_id, run_id)).fetchone()
                require(row is not None and row["state"] in {"started", "unknown"}, "TOOL_SCHEMA", "Unreserved tool completion")
                if row["state"] == "unknown":
                    require(self._run(db, run_id, active=False)["terminal"] is not None,
                            "TOOL_SCHEMA", "Late tool receipt requires a stopped run")
                    event = {**event, "late_receipt": True}
                metrics = event.get("metrics", event)
                cpu = metrics.get("cpu_seconds")
                require(cpu is None or (type(cpu) in {int, float} and math.isfinite(cpu) and cpu >= 0),
                        "TOOL_USAGE", "CPU observation must be nonnegative or null")
                flags = [metrics[key] for key in ('cpu_observation_complete', 'cpu_accounting_complete') if key in metrics]
                require(all(flag is None or type(flag) is bool for flag in flags), 'TOOL_USAGE', 'CPU completeness must be boolean or null')
                complete = (False if cpu is None or False in flags else
                            True if flags and all(flag is True for flag in flags) else None)
                metrics = {**metrics, 'cpu_observation_complete': complete,
                           'resource_scope': 'observed_not_equal_budget'}
                event = {**event, 'metrics': metrics}
                db.execute("UPDATE tools SET state='finished',cpu=?,metrics=? WHERE id=?", (cpu, canonical(metrics), tool_id))
            else:
                raise GateError("TOOL_SCHEMA", "Unknown tool event")
            self._event(db, run_id, kind, tool=event)

    def record(self, run_id, kind, **data):
        with self.transaction() as db:
            self._run(db, run_id, active=False)
            return self._event(db, run_id, kind, **data)

    def snapshot(self, run_id):
        with self.connection() as db:
            run = self._run(db, run_id, active=False)
            rows = db.execute("SELECT * FROM requests WHERE run=?", (run_id,)).fetchall()
            usages = [json.loads(row["usage"]) for row in rows if row["usage"]]
            tools = db.execute("SELECT * FROM tools WHERE run=?", (run_id,)).fetchall()
            resource_observations = {**RESOURCE_POLICY, 'tool_count': len(tools),
                'tool_cpu_complete': all(tool['state'] == 'finished' and tool['cpu'] is not None and
                    tool['metrics'] is not None and json.loads(tool['metrics']).get('cpu_observation_complete') is True
                    for tool in tools),
                'tools': [{'id': tool['id'], 'state': tool['state'], 'cpu_seconds': tool['cpu'],
                           'metrics': json.loads(tool['metrics']) if tool['metrics'] else None} for tool in tools],
                'aggregate_cpu_seconds': None}
            complete = all(row["state"] == "finished" and row["usage"] and json.loads(row["usage"])["complete"] for row in rows)
            return dict(run_id=run_id, arm=run["arm"], terminal=run["terminal"], requests=len(rows),
                        known_total_tokens=sum(self._known_lower_bound(u) for u in usages),
                        total_tokens=sum(u["total_tokens"] for u in usages) if complete else None,
                        charged_or_reserved_tokens=self._cost(rows), accounting_complete=complete,
                        detail_complete=complete and all(u["detail_complete"] for u in usages),
                        request_states=[dict(row) for row in rows], resource_observations=resource_observations)

    def events(self):
        with self.connection() as db:
            previous = "0" * 64
            result = []
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                event = json.loads(row["event"])
                require(event["seq"] == row["seq"] and event["previous_hash"] == previous and digest(event) == row["hash"],
                        "CORRUPT_LEDGER", "Event chain mismatch")
                previous = row["hash"]
                result.append({**event, "event_hash": row["hash"]})
            return result

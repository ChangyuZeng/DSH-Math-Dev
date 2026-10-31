"""Single-writer, append-only research storage for local POSIX filesystems."""

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import uuid

from domain import ResearchView, reference

from schema import (SCHEMA_VERSION, StoreError, canonical, context, decode, digest,
                    choice, fields, identifier, integer, ref, reference_fields, require,
                    sha, string, validate_data)


def now():
    return datetime.now(timezone.utc).isoformat()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def safe_path(root, relative):
    """Reject traversal and existing symlink components in a trusted local root."""
    require(type(relative) is str and relative and "\\" not in relative,
            "UNSAFE_PATH", "Expected a relative POSIX path")
    parts = relative.split("/")
    require(not relative.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            "UNSAFE_PATH", "Path traversal is not allowed")
    path = root
    for part in parts:
        path = path / part
        require(not path.is_symlink(), "UNSAFE_PATH", "Symlink component: " + str(path))
    return path


def read_regular(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise StoreError("FILE_UNAVAILABLE", str(path) + ": " + str(error)) from error
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                "UNSAFE_PATH", "Expected a regular, single-link file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        require(written > 0, "WRITE_FAILED", "Short write")
        view = view[written:]


class ResearchStore:
    """Host owns actor/origin. These never come from an object's data payload.

    Readers take a shared OS lock; writers hold an exclusive lock for the entire
    context. The persistent lock inode is never deleted (including after a kill).
    """

    def __init__(self, root, *, actor=None, origin=None, writable=False, create=False):
        self.root = Path(root).absolute()
        self.writable = writable
        self.actor = deepcopy(actor or {"kind": "human", "id": "local-user"})
        self.origin = deepcopy(origin or {"source": "offline-cli", "run_id": None,
                                         "session_id": None, "tool_call_id": None})
        context(self.actor, self.origin)
        self.lock_fd = None
        self.poisoned = False
        require(not create or writable, "READ_ONLY", "Creation requires a writer")
        # Reject symlinked ancestors before making any campaign files.
        for parent in [self.root, *self.root.parents]:
            require(not parent.is_symlink(), "UNSAFE_PATH", "Symlink campaign path")
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(self.root.is_dir(), "NOT_FOUND", "Campaign directory does not exist")
        try:
            lock = safe_path(self.root, ".writer.lock")
            # A shared lock needs no write-capable descriptor. Keep readers usable
            # under real read-only OS policies and on read-only source copies.
            flags = os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_RDWR | os.O_CREAT if writable else os.O_RDONLY)
            self.lock_fd = os.open(lock, flags, 0o600)
            info = os.fstat(self.lock_fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    "UNSAFE_PATH", "Unsafe lock inode")
            try:
                fcntl.flock(self.lock_fd, (fcntl.LOCK_EX if writable else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StoreError("LOCKED", "Campaign is in use") from error
            if writable:
                os.ftruncate(self.lock_fd, 0)
                write_all(self.lock_fd, canonical({"pid": os.getpid(), "owner": str(uuid.uuid4()), "time": now()}))
                os.fsync(self.lock_fd)
            if create:
                for name in ("staging", "attachments", "attachments/sha256", "recoveries"):
                    safe_path(self.root, name).mkdir(mode=0o700, exist_ok=True)
                log = safe_path(self.root, "events.jsonl")
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                sync_directory(self.root)
            self._load()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def _writer(self):
        require(self.lock_fd is not None and self.writable, "READ_ONLY", "Writer required")
        require(not self.poisoned, "REOPEN_REQUIRED", "Reopen after an ambiguous write")
        require(not self.tail, "INCOMPLETE_TAIL", "Explicit recovery required before writing")
        require(not self.integrity_issues(), "ATTACHMENT_INTEGRITY", "Committed attachments are missing or corrupt")

    def _load(self):
        self.records = {}
        self.operations = {}
        self.events = []
        self.campaign_id = None
        self.head_hash = "0" * 64
        raw = read_regular(safe_path(self.root, "events.jsonl"))
        end = raw.rfind(b"\n") + 1
        self.prefix = raw[:end]
        self.tail = raw[end:]
        for index, line in enumerate(self.prefix.splitlines(), 1):
            try:
                event = decode(line)
                self._apply_event(event)
            except StoreError as error:
                raise StoreError("CORRUPT_LOG", f"Line {index}: {error.code}: {error}") from error
        # A valid JSON event without its newline is ambiguous, not disposable.
        if self.tail:
            try:
                decode(self.tail)
            except StoreError:
                self.tail_is_partial = True
            else:
                self.tail_is_partial = False
        self.campaign_id = self.campaign_id or str(uuid.uuid4())

    @staticmethod
    def _request(changes, actor, origin):
        return {"actor": actor, "origin": origin, "changes": changes}

    def _validate_changes(self, changes, actor, *, review=False):
        require(type(changes) is list and changes, "INVALID_SCHEMA", "Empty transaction")
        candidate = deepcopy(self.records)
        seen = set()
        for change in changes:
            fields(change, "kind id expected_rev data")
            identifier(change["id"])
            integer(change["expected_rev"])
            require(change["id"] not in seen, "INVALID_SCHEMA", "Duplicate id in transaction")
            seen.add(change["id"])
            kind, data = change["kind"], change["data"]
            validate_data(kind, data)
            require(actor["kind"] != "model" or kind in ("Note", "Obligation", "Evidence", "Review"),
                    "FORBIDDEN", "Model context can only submit research drafts and advisory reviews")
            old = candidate.get(change["id"], [])
            require(len(old) == change["expected_rev"], "VERSION_CONFLICT", "Version conflict: " + change["id"])
            require(not old or old[-1]["kind"] == kind, "INVALID_SCHEMA", "Object kind cannot change")
            require(kind != "Problem" or actor["kind"] == "human",
                    "FORBIDDEN", "Only the host human entry may change a Problem")
            require((kind == "Review") == review, "FORBIDDEN", "Reviews require the dedicated host entry")
            if review:
                require(len(changes) == 1 and not old, "FORBIDDEN", "Reviews are immutable; append a new review or withdraw")
                self._validate_review(data, actor)
            if kind == "Obligation":
                require(data["state"] in ("open", "candidate"), "FORBIDDEN", "Acceptance states are derived, not writable")
            if kind == "Problem" and old:
                require(data["parent"] == old[0]["data"]["parent"], "INVALID_SCHEMA", "Problem parent cannot change")
            candidate.setdefault(change["id"], []).append({"kind": kind, "id": change["id"],
                "rev": len(old) + 1, "data": deepcopy(data)})
        roots = [v[-1] for v in candidate.values() if v[-1]["kind"] == "Problem"
                 and v[-1]["data"]["parent"] is None]
        require(len(roots) <= 1, "INVALID_SCHEMA", "One root Problem per campaign")
        graph = {}
        for versions in candidate.values():
            for obj in versions:
                kind, data = obj["kind"], obj["data"]
                key = (obj["id"], obj["rev"])
                graph[key] = [(r["id"], r["rev"]) for r in data.get("uses", [])]
                if kind == "Problem" and data["parent"]:
                    graph[key].append((data["parent"]["id"], data["parent"]["rev"]))
                for reference, allowed in reference_fields(kind, data):
                    versions_ref = candidate.get(reference["id"], [])
                    require(len(versions_ref) >= reference["rev"], "DANGLING_REFERENCE", str(reference))
                    target = versions_ref[reference["rev"] - 1]
                    require(target["kind"] in allowed, "REFERENCE_KIND", str(reference))
                    if kind in ("Note", "Obligation", "Evidence") and target["kind"] != "Problem":
                        require(target["data"].get("problem_ref") == data["problem_ref"],
                                "TARGET_MISMATCH", "Reference belongs to another target version")
        visiting, visited = set(), set()

        def visit(key):
            require(key not in visiting, "DEPENDENCY_CYCLE", "Cycle in versioned premises")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in graph:
            visit(key)
        return candidate

    def _apply_event(self, event):
        fields(event, "schema_version kind campaign_id event_id seq operation_id request_hash time actor origin changes result previous_hash event_hash recovery control")
        require(type(event["schema_version"]) is int and event["schema_version"] == SCHEMA_VERSION,
                "UNKNOWN_SCHEMA", "Unsupported event schema")
        integer(event["seq"], 1)
        require(event["seq"] == len(self.events) + 1, "SEQUENCE_CONFLICT", "Noncontiguous sequence")
        for name in ("campaign_id", "event_id", "operation_id"):
            identifier(event[name])
        string(event["time"])
        context(event["actor"], event["origin"])
        for name in ("request_hash", "previous_hash", "event_hash"):
            sha(event[name])
        require(event["previous_hash"] == self.head_hash, "HASH_MISMATCH", "Broken event chain")
        require(digest({k: v for k, v in event.items() if k != "event_hash"}) == event["event_hash"],
                "HASH_MISMATCH", "Event bytes changed")
        require(self.campaign_id in (None, event["campaign_id"]), "CAMPAIGN_MISMATCH", "Mixed campaign ids")
        require(event["operation_id"] not in self.operations, "DUPLICATE_OPERATION", "Repeated committed operation")
        require(all(e["event_id"] != event["event_id"] for e in self.events), "INVALID_SCHEMA", "Repeated event id")
        require(type(event["changes"]) is list, "INVALID_SCHEMA", "Expected event changes list")
        if event["kind"] in ("commit", "review"):
            require(event["recovery"] is None and event["control"] == {}, "INVALID_SCHEMA", "Unexpected control/recovery data")
            changes = []
            for obj in event["changes"]:
                fields(obj, "schema_version kind id rev created_at actor origin data")
                require(type(obj["schema_version"]) is int and obj["schema_version"] == SCHEMA_VERSION,
                        "UNKNOWN_SCHEMA", "Unsupported object schema")
                integer(obj["rev"], 1)
                require(obj["created_at"] == event["time"] and obj["actor"] == event["actor"]
                        and obj["origin"] == event["origin"], "INVALID_SCHEMA", "Provenance mismatch")
                changes.append({"kind": obj["kind"], "id": obj["id"],
                                "expected_rev": obj["rev"] - 1, "data": obj["data"]})
            require(digest(self._request(changes, event["actor"], event["origin"])) == event["request_hash"],
                    "HASH_MISMATCH", "Request hash mismatch")
            self._validate_changes(changes, event["actor"], review=event["kind"] == "review")
            expected = {"event_id": event["event_id"], "seq": event["seq"],
                        "versions": [{"id": c["id"], "rev": c["rev"]} for c in event["changes"]]}
            require(event["result"] == expected, "INVALID_SCHEMA", "Receipt mismatch")
        elif event["kind"] == "control":
            require(event["changes"] == [] and event["recovery"] is None, "INVALID_SCHEMA", "Control cannot mutate objects")
            self._validate_control(event["control"], event["actor"])
            require(event["request_hash"] == digest({"actor": event["actor"], "origin": event["origin"], "control": event["control"]}),
                    "HASH_MISMATCH", "Control hash mismatch")
            require(event["result"] == {"event_id": event["event_id"], "seq": event["seq"], "versions": []},
                    "INVALID_SCHEMA", "Control receipt mismatch")
        elif event["kind"] == "recovery":
            require(event["control"] == {}, "INVALID_SCHEMA", "Unexpected recovery control")
            require(event["changes"] == [], "INVALID_SCHEMA", "Recovery cannot mutate research objects")
            fields(event["recovery"], "backup_dir original_sha256 tail_sha256 valid_bytes")
            safe_path(self.root, event["recovery"]["backup_dir"])
            for name in ("original_sha256", "tail_sha256"):
                sha(event["recovery"][name])
            integer(event["recovery"]["valid_bytes"])
            require(event["request_hash"] == digest(event["recovery"]), "HASH_MISMATCH", "Recovery hash mismatch")
            require(event["result"] == {"event_id": event["event_id"], "seq": event["seq"], "versions": []},
                    "INVALID_SCHEMA", "Recovery receipt mismatch")
        else:
            raise StoreError("UNKNOWN_SCHEMA", "Unknown event kind")
        for obj in event["changes"]:
            self.records.setdefault(obj["id"], []).append(deepcopy(obj))
        self.operations[event["operation_id"]] = deepcopy(event)
        self.events.append(deepcopy(event))
        self.campaign_id = event["campaign_id"]
        self.head_hash = event["event_hash"]

    def _event(self, operation_id, request_hash, changes, recovery=None, *, control=None, review=False):
        time = now()
        objects = [{"schema_version": SCHEMA_VERSION, "kind": c["kind"], "id": c["id"],
                    "rev": c["expected_rev"] + 1, "created_at": time,
                    "actor": self.actor, "origin": self.origin, "data": c["data"]} for c in changes]
        event_id = str(uuid.uuid4())
        seq = len(self.events) + 1
        event = {"schema_version": SCHEMA_VERSION, "kind": "recovery" if recovery else "control" if control else "review" if review else "commit",
                 "campaign_id": self.campaign_id, "event_id": event_id, "seq": seq,
                 "operation_id": operation_id, "request_hash": request_hash, "time": time,
                 "actor": self.actor, "origin": self.origin, "changes": objects,
                 "previous_hash": self.head_hash, "recovery": recovery, "control": control or {},
                 "result": {"event_id": event_id, "seq": seq,
                            "versions": [{"id": o["id"], "rev": o["rev"]} for o in objects]}}
        event["event_hash"] = digest(event)
        return event

    def _append(self, event):
        # On any I/O ambiguity, this handle cannot make another write.
        self.poisoned = True
        fd = os.open(safe_path(self.root, "events.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            write_all(fd, canonical(event) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        self._apply_event(event)
        self.poisoned = False

    def commit(self, operation_id, changes):
        return self._commit(operation_id, changes)

    def _commit(self, operation_id, changes, *, review=False):
        self._writer()
        identifier(operation_id)
        # Roundtrip rejects non-JSON/non-finite inputs and disconnects caller mutation.
        changes = decode(canonical(changes))
        request_hash = digest(self._request(changes, self.actor, self.origin))
        previous = self.operations.get(operation_id)
        if previous:
            require(previous["kind"] == ("review" if review else "commit") and previous["request_hash"] == request_hash,
                    "OPERATION_CONFLICT", "Same operation id with different payload or context")
            return deepcopy(previous["result"])
        self._validate_changes(changes, self.actor, review=review)
        for change in changes:
            if change["kind"] == "Evidence":
                for descriptor in change["data"]["attachments"]:
                    self._verify_attachment(descriptor)
        event = self._event(operation_id, request_hash, changes, review=review)
        self._append(event)
        return deepcopy(event["result"])

    def archive(self, staging_relative):
        self._writer()
        staging = safe_path(self.root, "staging")
        source = safe_path(staging, staging_relative)
        content = read_regular(source)
        return self._archive_bytes(content, source.name)

    def _archive_bytes(self, content, logical_name):
        """Host-only archival of fixed checker bytes; never a model command."""
        self._writer()
        string(logical_name)
        value = hashlib.sha256(content).hexdigest()
        path = safe_path(self.root, "attachments/sha256/" + value)
        descriptor = {"sha256": value, "relative_path": "attachments/sha256/" + value,
                      "bytes": len(content), "logical_name": logical_name}
        if path.exists():
            self._verify_attachment(descriptor)
            return descriptor
        temporary = path.parent / (".tmp-" + str(uuid.uuid4()))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temporary, path)
        sync_directory(path.parent)
        return descriptor

    def _verify_attachment(self, descriptor):
        content = read_regular(safe_path(self.root, descriptor["relative_path"]))
        require(len(content) == descriptor["bytes"] and hashlib.sha256(content).hexdigest() == descriptor["sha256"],
                "ATTACHMENT_INTEGRITY", "Attachment hash/size mismatch: " + descriptor["sha256"])

    def integrity_issues(self):
        issues = []
        for versions in self.records.values():
            for obj in versions:
                if obj["kind"] != "Evidence":
                    continue
                for descriptor in obj["data"]["attachments"]:
                    try:
                        self._verify_attachment(descriptor)
                    except StoreError as error:
                        issues.append({"id": obj["id"], "rev": obj["rev"], "sha256": descriptor["sha256"],
                                       "code": error.code, "message": str(error)})
        for event in self.events:
            if event["control"].get("action") == "check.started":
                descriptor = event["control"]["checker_source"]
                try:
                    self._verify_attachment(descriptor)
                except StoreError as error:
                    issues.append({"id": event["event_id"], "rev": 1, "sha256": descriptor["sha256"],
                                   "code": error.code, "message": str(error)})
        return issues

    def get(self, object_id, rev=None):
        versions = self.records.get(object_id, [])
        require(bool(versions), "NOT_FOUND", object_id)
        if rev is not None:
            integer(rev, 1)
        revision = rev if rev is not None else len(versions)
        require(revision <= len(versions), "NOT_FOUND", "No such version")
        return deepcopy(versions[revision - 1])

    def receipt(self, operation_id):
        require(operation_id in self.operations, "NOT_FOUND", "Operation was not committed")
        return deepcopy(self.operations[operation_id]["result"])

    def snapshot(self):
        issues = self.integrity_issues()
        state = {"campaign_id": self.campaign_id if self.events else None,
                 "seq": len(self.events), "head_hash": self.head_hash,
                 "objects": deepcopy(self.records), "research": ResearchView(self).snapshot()}
        return {"state": state, "state_sha256": digest(state), "integrity_issues": issues,
                "trailing_bytes": len(self.tail), "usable": not issues and not self.tail,
                "model_requests": 0, "acceptance_rules": "p2c_human_and_fixed_program_v1"}

    def recover_tail(self):
        require(self.lock_fd is not None and self.writable and not self.poisoned,
                "READ_ONLY", "Healthy writer required")
        require(self.tail and self.tail_is_partial, "RECOVERY_REFUSED",
                "Only a non-JSON final unterminated fragment may be truncated")
        require(not self.integrity_issues(), "ATTACHMENT_INTEGRITY", "Repair attachments first")
        # Preserve bytes and a recovery intent before changing the authoritative file.
        directory = safe_path(self.root, "recoveries/" + str(uuid.uuid4()))
        directory.mkdir(mode=0o700)
        original = self.prefix + self.tail
        metadata = {"backup_dir": str(directory.relative_to(self.root)),
                    "original_sha256": hashlib.sha256(original).hexdigest(),
                    "tail_sha256": hashlib.sha256(self.tail).hexdigest(), "valid_bytes": len(self.prefix)}
        for name, content in (("original.jsonl", original), ("tail.bin", self.tail),
                              ("intent.json", canonical(metadata) + b"\n")):
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                write_all(fd, content)
                os.fsync(fd)
            finally:
                os.close(fd)
        sync_directory(directory)
        sync_directory(directory.parent)
        event = self._event("recovery-" + directory.name, digest(metadata), [], metadata)
        # Replace prefix + recovery event together; no crash gap after truncation.
        temporary = self.root / (".recovery-" + directory.name)
        self.poisoned = True
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            write_all(fd, self.prefix + canonical(event) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, safe_path(self.root, "events.jsonl"))
        sync_directory(self.root)
        self.tail = b""
        self._apply_event(event)
        self.prefix = self.prefix + canonical(event) + b"\n"
        self.poisoned = False
        return deepcopy(event["result"])

    def _validate_review(self, data, actor):
        if data["method"] == "program":
            from checking import validate_review
            validate_review(self, data, actor)
            return
        require(data["method"] in ("human", "model") and data["method"] == actor["kind"],
                "FORBIDDEN", "Review authority is fixed by the host, not the payload")
        require(data["reviewer"] == actor["id"] and data["checker_version"] is None,
                "FORBIDDEN", "Reviewer identity must match the host context")
        require(bool(data["findings"]), "INVALID_SCHEMA", "Review must record findings")
        view = ResearchView(self, check_files=False)
        preview = view.preview(data["target"], data["candidate"])
        require(self.get(data["target"]["id"])["rev"] == data["target"]["rev"],
                "VERSION_CONFLICT", "Only the current target version can be reviewed")
        require(data["basis"] == preview["basis"] and data["basis_hash"] == preview["basis_hash"],
                "REVIEW_CONFLICT", "Review basis has changed; preview and read it again")
        require(data["evidence"] == data["basis"]["evidence"]
                and data["dependencies"] == data["basis"]["dependencies"],
                "INVALID_SCHEMA", "Review references must equal the exact basis")
        require(data["scope"] == preview["required_scope"], "SCOPE_MISMATCH", "Scope must equal the stored target scope")
        if data["method"] == "human" and data["verdict"] == "pass":
            require(preview["eligible_for_pass"], "REVIEW_BLOCKED", ", ".join(preview["blockers"]))

    def review_preview(self, target, candidate=None):
        require(not self.tail, "INCOMPLETE_TAIL", "Recover the log before reviewing")
        return ResearchView(self).preview(target, candidate)

    def review(self, operation_id, review_id, target, candidate, verdict, scope, findings, basis_hash):
        require(self.actor["kind"] == "human", "FORBIDDEN", "Human review requires the trusted host entry")
        return self._record_review(operation_id, review_id, target, candidate, verdict, scope, findings, basis_hash)

    def advisory(self, operation_id, review_id, target, verdict, scope, findings):
        """Host adapter for advisory records; never grants human authority."""
        require(self.actor["kind"] == "model", "FORBIDDEN", "Advisory host context must be model")
        return self._record_review(operation_id, review_id, target, None, verdict, scope, findings, None)

    def _record_review(self, operation_id, review_id, target, candidate, verdict, scope, findings, basis_hash):
        self._writer()
        identifier(operation_id)
        identifier(review_id)
        # On retries reconstruct from the original basis, not the now-current state.
        previous = self.operations.get(operation_id)
        if previous and previous["kind"] == "review":
            basis = previous["changes"][0]["data"]["basis"]
        else:
            basis = ResearchView(self).basis(target, candidate)
        if basis_hash is None and self.actor["kind"] == "model":
            basis_hash = digest(basis)
        require(basis_hash == digest(basis), "REVIEW_CONFLICT", "Stale preview; read the exact basis before submitting")
        data = {"target": target, "candidate": candidate, "evidence": basis["evidence"],
                "dependencies": basis["dependencies"], "method": self.actor["kind"],
                "verdict": verdict, "error_code": "advisory_error" if verdict == "error" else None,
                "scope": scope, "findings": findings, "reviewer": self.actor["id"],
                "checker_version": None, "basis": basis, "basis_hash": basis_hash, "execution": None}
        return self._commit(operation_id, [{"kind": "Review", "id": review_id, "expected_rev": 0, "data": data}], review=True)

    def _validate_control(self, control, actor):
        if type(control) is dict and control.get("action") == "check.started":
            from checking import validate_start
            validate_start(self, control, actor)
            return
        require(actor["kind"] == "human", "FORBIDDEN", "Control events require the host human entry")
        require(type(control) is dict, "INVALID_SCHEMA", "Expected control payload")
        action = control.get("action")
        if action == "review.withdraw":
            fields(control, "action review_id reason")
            identifier(control["review_id"])
            review = self.get(control["review_id"])
            require(review["kind"] == "Review" and review["data"]["method"] in ("human", "program"),
                    "REFERENCE_KIND", "Only an existing authoritative review can be withdrawn")
            require(control["review_id"] not in ResearchView(self, check_files=False).withdrawn,
                    "VERSION_CONFLICT", "Review already withdrawn")
            string(control["reason"])
        elif action == "route.update":
            fields(control, "action route_id expected_rev problem_ref status reason next_action")
            identifier(control["route_id"])
            integer(control["expected_rev"])
            ref(control["problem_ref"])
            problem = self.get(control["problem_ref"]["id"], control["problem_ref"]["rev"])
            require(problem["kind"] == "Problem", "REFERENCE_KIND", "Route requires a Problem")
            choice(control["status"], "active paused abandoned")
            string(control["reason"])
            string(control["next_action"])
            versions = [e["control"] for e in self.events if e["control"].get("action") == action
                        and e["control"]["route_id"] == control["route_id"]]
            require(len(versions) == control["expected_rev"], "VERSION_CONFLICT", "Route version conflict")
            require(not versions or versions[0]["problem_ref"] == control["problem_ref"],
                    "TARGET_MISMATCH", "Route target is immutable")
        else:
            raise StoreError("INVALID_SCHEMA", "Unknown control action")

    def control(self, operation_id, control):
        self._writer()
        identifier(operation_id)
        control = decode(canonical(control))
        request_hash = digest({"actor": self.actor, "origin": self.origin, "control": control})
        previous = self.operations.get(operation_id)
        if previous:
            require(previous["kind"] == "control" and previous["request_hash"] == request_hash,
                    "OPERATION_CONFLICT", "Control operation id already used")
            return deepcopy(previous["result"])
        self._validate_control(control, self.actor)
        event = self._event(operation_id, request_hash, [], control=control)
        self._append(event)
        return deepcopy(event["result"])

    def research_read(self, *, kind=None, state=None, text="", object_id=None, rev=None):
        """Search current objects, or inspect an exact version plus its assessment."""
        view = ResearchView(self)
        health = {"usable": not self.tail and not view.bad_evidence, "trailing_bytes": len(self.tail),
                  "integrity_issues": self.integrity_issues(), "seq": len(self.events), "model_requests": 0}
        if object_id is not None:
            identifier(object_id)
            obj = self.get(object_id, rev)
            return {**health, "object": obj, "assessment": view.assess(reference(obj))
                    if obj["kind"] in ("Note", "Obligation") else None}
        require(rev is None, "INVALID_SCHEMA", "Revision needs an object id")
        if kind is not None:
            choice(kind, "Problem Note Obligation Evidence Review Run")
        if state is not None:
            choice(state, "open candidate unreviewed failed_attempt accepted closed rejected needs_recheck")
        string(text, empty=True)
        result = []
        for versions in self.records.values():
            obj = versions[-1]
            if kind is not None and obj["kind"] != kind:
                continue
            if text.lower() not in canonical(obj["data"]).decode("utf-8").lower():
                continue
            assessment = view.assess(reference(obj)) if obj["kind"] in ("Note", "Obligation") else None
            if state is not None and (assessment is None or assessment["state"] != state):
                continue
            result.append({"object": deepcopy(obj), "assessment": assessment})
        return {**health, "results": result}

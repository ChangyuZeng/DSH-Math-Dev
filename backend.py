import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
BASE = HERE / "runtime/p2c"
require_python = sys.version_info >= (3, 10)
if not require_python:
    raise SystemExit("dsh-math requires Python 3.10+; configure the plugin python path")
if not BASE.is_dir():
    raise SystemExit("Incomplete dsh-math installation: bundled runtime/p2c is missing")
sys.path.append(str(BASE))
from schema import StoreError, canonical, decode, digest, fields, identifier, require, validate_data
from research_store import ResearchStore, read_regular, safe_path, sync_directory, write_all
from domain import ResearchView, reference


def checked_root(path):
    root = Path(path).absolute()
    require(all(not p.is_symlink() for p in [root, *root.parents]), "UNSAFE_PATH", "Symlink root")
    return Path(os.path.abspath(root))


def make_directory(path):
    """Create trusted local directories and durably publish each new entry."""
    path = checked_root(path)
    if path.exists():
        require(path.is_dir(), "UNSAFE_PATH", "Expected directory: " + str(path))
        return path
    make_directory(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)
    checked_root(path)
    require(path.is_dir(), "UNSAFE_PATH", "Expected directory")
    sync_directory(path.parent)
    return path


def write_bytes_new(path, raw):
    path = checked_root(path)
    make_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        write_all(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)


def write_new(path, value):
    write_bytes_new(path, canonical(value) + b"\n")


def sync_tree(path):
    """Do not publish a project containing links or non-regular files."""
    path = checked_root(path)
    for child in sorted(path.iterdir()):
        checked_root(child)
        if child.is_dir():
            sync_tree(child)
        else:
            read_regular(child)  # Also rejects hardlinks and special files.
            fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    sync_directory(path)


def copy_regular_tree(source, destination):
    source, destination = checked_root(source), checked_root(destination)
    make_directory(destination)
    for child in sorted(source.iterdir()):
        checked_root(child)
        target = safe_path(destination, child.name)
        if child.is_dir():
            copy_regular_tree(child, target)
        else:
            write_bytes_new(target, read_regular(child))
    sync_directory(destination)


def research_version(path):
    raw = read_regular(safe_path(checked_root(path), "events.jsonl"))
    require(not raw or raw.endswith(b"\n"), "INCOMPLETE_TAIL", "Read stopped at incomplete journal")
    version = decode(raw.splitlines()[0])["schema_version"] if raw else 4
    require(type(version) is int and version in (3, 4), "UNKNOWN_SCHEMA", "Schema 1/2 require their original reader; no migration")
    return version


@contextmanager
def open_store(path, **kwargs):
    version = research_version(path)
    if version == 4:
        with ResearchStore(path, **kwargs) as store:
            yield store
    else:
        require(not kwargs.get("writable"), "READ_ONLY", "Schema 3 is read-only in dsh-math")
        import subprocess
        result = subprocess.run([sys.executable, "-B", str(BASE / "cli.py"), str(path), "replay"],
                                capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, "LEGACY_READ_FAILED", result.stderr)
        yield decode(result.stdout)


class Projects:
    def __init__(self, root):
        self.root = checked_root(root)

    def path(self, project_id):
        identifier(project_id)
        return safe_path(self.root, "projects/" + project_id)

    def metadata(self, project_id):
        value = decode(read_regular(safe_path(self.path(project_id), "project.json")))
        require(type(value) is dict and value.get("format") == "dsh-math-project-v1"
                and value.get("id") == project_id, "INVALID_SCHEMA", "Invalid project metadata")
        identifier(value["session_id"])
        identifier(value["campaign_id"])
        require(type(value.get("schema_version")) is int and value["schema_version"] in (3, 4),
                "UNKNOWN_SCHEMA", "Unsupported project metadata schema")
        return value

    def listing(self, include_removed=False):
        directory = safe_path(self.root, "projects")
        if not directory.exists():
            return []
        from library_store import Library
        library = Library(self.root)
        result = []
        for path in sorted(directory.iterdir()):
            checked_root(path)
            if path.is_dir() and safe_path(path, "project.json").exists():
                metadata = self.metadata(path.name)
                library_metadata = library.metadata(path.name, {"kind": "project", "id": path.name})
                if include_removed or not library_metadata["removed"]:
                    result.append({**metadata, "library": library_metadata} if library_metadata["metadata_rev"] or include_removed else metadata)
        return result

    @contextmanager
    def open_project(self, project_id, *, writable=False, require_writable=False,
                     actor=None, origin=None, session_id=None, model=False, chat=False):
        """Single gate; only ordinary chat may use human-authorized associations."""
        metadata = self.metadata(project_id)
        if chat:
            from project_access import ProjectAccess
            with ProjectAccess(self.root).authorized(metadata, session_id):
                with self.open_project(project_id, writable=writable, require_writable=require_writable,
                                       actor=actor, origin=origin) as opened:
                    yield opened
            return
        if model:
            require(metadata["session_id"] == session_id, "PROJECT_SCOPE", "Choose a project owned by this exact session; cross-session access is forbidden")
        if writable or require_writable:
            require(metadata["schema_version"] == 4 and metadata.get("mode") == "daily_research",
                    "READ_ONLY", "Project is not a schema4 daily_research project")
        path = self.path(project_id) / "research"
        require(research_version(path) == metadata["schema_version"], "UNKNOWN_SCHEMA", "Project metadata/research schema mismatch")
        with open_store(path, writable=writable, actor=actor, origin=origin) as store:
            snapshot = store.snapshot() if isinstance(store, ResearchStore) else store
            require(snapshot["usable"], "UNUSABLE", "Research log or attachments failed integrity")
            require(snapshot["state"]["campaign_id"] == metadata["campaign_id"], "CAMPAIGN_MISMATCH", "Project metadata/research campaign mismatch")
            if writable or require_writable:
                require(isinstance(store, ResearchStore), "READ_ONLY", "Actual research schema must be 4")
            yield metadata, store

    def _staging(self):
        parent = make_directory(safe_path(self.root, "project-staging"))
        path = safe_path(parent, str(uuid.uuid4()))
        make_directory(path)
        return path

    def _publish(self, staging, project_id):
        staging = checked_root(staging)
        parent = safe_path(self.root, "project-staging")
        require(staging.parent == parent and staging.is_dir(), "UNSAFE_PATH", "Publish only a fresh project staging directory")
        destination = self.path(project_id)
        make_directory(destination.parent)
        require(not destination.exists(), "PROJECT_EXISTS", "Project destination already exists")
        sync_tree(staging)
        # Both absolute paths are validated; unfinished stages stay outside projects.
        os.rename(staging, destination)
        sync_directory(destination.parent)
        sync_directory(parent)

    def _sync_published(self, destination):
        # A prior process may have stopped after rename but before parent fsync.
        # Recovery must make publication durable before recording completion.
        sync_tree(destination)
        sync_directory(destination.parent)
        parent = safe_path(self.root, "project-staging")
        if parent.exists():
            sync_directory(parent)

    def create(self, session_id, problem):
        return self._create_project(session_id, problem, str(uuid.uuid4()))

    def _create_project(self, session_id, problem, project_id):
        identifier(session_id)
        identifier(project_id)
        validate_data("Problem", problem)
        require(problem["parent"] is None, "INVALID_SCHEMA", "New project requires a root Problem")
        destination = self.path(project_id)
        if destination.exists():
            with self.open_project(project_id, require_writable=True) as (metadata, store):
                require(metadata["session_id"] == session_id and metadata.get("title") == problem["title"]
                        and metadata.get("execution_authorized") is False and store.get("P", 1)["data"] == problem,
                        "OPERATION_CONFLICT", "Published project does not match frozen creation")
                self._sync_published(destination)
                return metadata
        path = self._staging()
        with ResearchStore(path / "research", writable=True, create=True,
                           origin={"source": "dsh-math-human", "session_id": session_id,
                                   "run_id": None, "tool_call_id": None}) as store:
            store.commit("create", [{"kind": "Problem", "id": "P", "expected_rev": 0, "data": problem}])
            require(store.snapshot()["usable"], "UNUSABLE", "New project failed integrity")
            campaign_id = store.campaign_id
        metadata = {"format": "dsh-math-project-v1", "id": project_id, "campaign_id": campaign_id,
                    "session_id": session_id, "title": problem["title"], "schema_version": 4,
                    "mode": "daily_research", "execution_authorized": False}
        write_new(path / "project.json", metadata)
        self._publish(path, project_id)
        return metadata

    def _legacy_snapshot(self, campaign):
        require(type(campaign) is str and campaign.strip(), "INVALID_SCHEMA", "Expected legacy campaign path")
        source = checked_root(campaign)
        require(research_version(source) == 3, "UNKNOWN_SCHEMA", "Legacy import requires exact schema 3")
        journal = safe_path(source, "events.jsonl")
        raw = read_regular(journal)
        result = subprocess.run([sys.executable, "-B", str(BASE / "cli.py"), str(source), "replay"], capture_output=True, timeout=30)
        require(result.returncode == 0, "LEGACY_READ_FAILED", result.stderr.decode("utf-8", "replace"))
        snapshot = decode(result.stdout)
        require(snapshot["usable"], "UNUSABLE", "Legacy source is not usable")
        require(read_regular(journal) == raw, "SOURCE_CHANGED", "Legacy source changed during preview; retry preview")
        events = [decode(line) for line in raw.splitlines()]
        require(len(events) == snapshot["state"]["seq"] and events
                and events[-1]["event_hash"] == snapshot["state"]["head_hash"],
                "SOURCE_CHANGED", "Legacy snapshot does not match the observed journal")
        versions = [obj for history in snapshot["state"]["objects"].values() for obj in history]
        require(versions and all(type(obj["schema_version"]) is int and obj["schema_version"] == 3 for obj in versions),
                "UNKNOWN_SCHEMA", "Legacy import requires nonempty exact schema 3")
        roots = [history[-1] for history in snapshot["state"]["objects"].values()
                 if history[-1]["kind"] == "Problem" and history[-1]["data"]["parent"] is None]
        require(len(roots) == 1, "INVALID_SCHEMA", "Legacy import requires exactly one root Problem")
        attachments = {a["sha256"] for obj in versions if obj["kind"] == "Evidence" for a in obj["data"]["attachments"]}
        # Match export's referenced archive set; no directory scan or checker run.
        attachments.update(event["control"]["checker_source"]["sha256"] for event in events
                           if event["control"].get("action") == "check.started")
        preview = {"title": roots[0]["data"]["title"], "campaign_id": snapshot["state"]["campaign_id"],
                   "schema_version": 3, "seq": snapshot["state"]["seq"], "head_hash": snapshot["state"]["head_hash"],
                   "fingerprint": digest({"format": "dsh-math-legacy-preview-v1", "snapshot": snapshot}),
                   "object_count": len(snapshot["state"]["objects"]), "attachment_count": len(attachments)}
        return source, snapshot, preview

    def import_legacy_preview(self, campaign):
        # Deliberately read-only: no root creation, export, copy, or sidecar write.
        return {"preview": self._legacy_snapshot(campaign)[2]}

    def _import_legacy_project(self, session_id, campaign, preview, project_id):
        identifier(session_id)
        identifier(project_id)
        destination = self.path(project_id)
        if destination.exists():
            metadata = self.metadata(project_id)
            require(metadata["schema_version"] == 3 and metadata.get("mode") == "legacy_readonly_copy"
                    and metadata["session_id"] == session_id and metadata["campaign_id"] == preview["campaign_id"]
                    and metadata.get("title") == preview["title"] and metadata.get("execution_authorized") is False,
                    "OPERATION_CONFLICT", "Published import does not match intent")
            _, _, copied = self._legacy_snapshot(str(destination / "research"))
            require(copied == preview, "SOURCE_CHANGED", "Published import differs from confirmed preview")
            self._sync_published(destination)
            return metadata
        source, snapshot, current = self._legacy_snapshot(campaign)
        require(current == preview, "SOURCE_CHANGED", "Legacy source changed; preview and confirm again")
        path = self._staging()
        exported = safe_path(path, "legacy-import")
        result = subprocess.run([sys.executable, "-B", str(BASE / "cli.py"), str(source), "export", str(exported),
                                 "--expected-seq", str(preview["seq"])], capture_output=True, timeout=60)
        require(result.returncode == 0, "LEGACY_READ_FAILED", result.stderr.decode("utf-8", "replace"))
        require(decode(read_regular(safe_path(exported, "snapshot.json"))) == snapshot, "SOURCE_CHANGED", "Legacy source changed during import")
        copy_regular_tree(safe_path(exported, "research"), safe_path(path, "research"))
        _, _, copied = self._legacy_snapshot(str(path / "research"))
        require(copied == preview, "SOURCE_CHANGED", "Staged legacy copy differs from confirmed preview")
        metadata = {"format": "dsh-math-project-v1", "id": project_id, "campaign_id": preview["campaign_id"],
                    "session_id": session_id, "title": preview["title"], "schema_version": 3,
                    "mode": "legacy_readonly_copy", "execution_authorized": False}
        write_new(path / "project.json", metadata)
        self._publish(path, project_id)
        return metadata

    def import_legacy_confirm(self, session_id, campaign, expected_fingerprint, operation_id):
        from workbench_store import Workbench
        return Workbench(self.root).import_legacy_confirm(session_id, campaign, expected_fingerprint, operation_id)

    def import_legacy(self, session_id, campaign):
        preview = self.import_legacy_preview(campaign)["preview"]
        return self.import_legacy_confirm(session_id, campaign, preview["fingerprint"], str(uuid.uuid4()))

    def overview(self, project_id):
        with self.open_project(project_id) as (metadata, store):
            snapshot = store.snapshot() if isinstance(store, ResearchStore) else store
            require(snapshot["usable"], "UNUSABLE", "Research log or attachments failed integrity")
            objects = [o for versions in snapshot["state"]["objects"].values() for o in versions]
            assessments = {v["ref"]["id"]: v for v in snapshot["state"]["research"]["claims"]}
            current = [versions[-1] for versions in snapshot["state"]["objects"].values()]
            groups = {k: [] for k in ("targets", "accepted", "candidates", "obligations", "failed_routes", "notes")}
            for obj in current:
                kind, data = obj["kind"], obj["data"]
                assessment = assessments.get(obj["id"])
                row = {"object": obj, "assessment": assessment}
                if kind == "Problem":
                    groups["targets"].append(row)
                elif kind in ("Note", "Obligation"):
                    state = assessment["state"]
                    bucket = "accepted" if state in ("accepted", "closed") else "obligations" if kind == "Obligation" else "failed_routes" if data["note_type"] == "failed_attempt" else "candidates" if data["candidate_for"] else "notes"
                    groups[bucket].append(row)
            links = []
            for obj in objects:
                for value in obj["data"].get("evidence", []):
                    links.append({"from": reference(obj), "to": value, "relation": "evidence"})
                if obj["kind"] == "Evidence" and obj["data"]["citation"]:
                    for condition in obj["data"]["citation"]["condition_map"]:
                        if condition["obligation"]:
                            links.append({"from": reference(obj), "to": condition["obligation"], "relation": "condition_gap"})
            return {"project": metadata, "snapshot": snapshot, "groups": groups, "objects": objects,
                    "links": links, "routes": snapshot["state"]["research"]["routes"],
                    "operation_model_requests": 0, "session_model_requests": None}

    def attachment(self, project_id, object_id, rev, sha256):
        view = self.overview(project_id)
        obj = next((o for o in view["objects"] if o["id"] == object_id and o["rev"] == rev and o["kind"] == "Evidence"), None)
        require(obj is not None, "NOT_FOUND", "Evidence revision not found")
        descriptor = next((a for a in obj["data"]["attachments"] if a["sha256"] == sha256), None)
        require(descriptor is not None, "NOT_FOUND", "Attachment is not referenced by this evidence revision")
        raw = read_regular(safe_path(self.path(project_id) / "research", descriptor["relative_path"]))
        require(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() == sha256,
                "ATTACHMENT_INTEGRITY", "Attachment bytes changed")
        try:
            text = raw.decode("utf-8") if len(raw) <= 1024 * 1024 else None
        except UnicodeError:
            text = None
        return {"descriptor": descriptor, "text": text, "preview": "utf8_literal" if text is not None else "binary_or_over_1MiB_export_only"}

    def mutate(self, project_id, operation, arguments):
        require(operation in ("commit", "review-preview", "review", "evidence"), "FORBIDDEN", "Unknown human action")
        metadata = self.metadata(project_id)
        with self.open_project(project_id, writable=operation != "review-preview", require_writable=True,
                               actor={"kind": "human", "id": "local-user"},
                               origin={"source": "dsh-math-human", "session_id": metadata["session_id"], "run_id": None, "tool_call_id": None}) as (_, store):
            if operation == "review-preview":
                fields(arguments, "target candidate")
                return store.review_preview(**arguments)
            if operation == "review":
                fields(arguments, "operation_id review_id target candidate verdict scope findings basis_hash")
                return store.review(**arguments)
            if operation == "evidence":
                import base64
                arguments = {"files": [], **arguments}
                fields(arguments, "operation_id id expected_rev data excerpt logical_name files")
                data = {**arguments["data"], "attachments": list(arguments["data"]["attachments"])}
                require(type(arguments["files"]) is list and len(arguments["files"]) <= 10, "INVALID_SCHEMA", "Select at most 10 files")
                total = 0
                for item in arguments["files"]:
                    fields(item, "logical_name base64")
                    name = item["logical_name"]
                    require(type(name) is str and name and "/" not in name and "\\" not in name and not name.lower().startswith(".env")
                            and not any(word in name.lower() for word in ("credential", "oauth", "auth.json", "private_key")),
                            "SENSITIVE_CONTENT", "Unsafe attachment name")
                    raw = base64.b64decode(item["base64"], validate=True)
                    total += len(raw)
                    require(total <= 384 * 1024, "UNSUPPORTED", "Attachments exceed 384 KiB")
                    from exporting import guard_content
                    guard_content(raw)
                    data["attachments"].append(store._archive_bytes(raw, name))
                if arguments["excerpt"] is not None:
                    require(type(arguments["excerpt"]) is str and len(arguments["excerpt"].encode()) <= 384 * 1024, "INVALID_SCHEMA", "Expected bounded excerpt text")
                    descriptor = store._archive_bytes(arguments["excerpt"].encode(), arguments["logical_name"])
                    data["attachments"].append(descriptor)
                    if data["citation"]:
                        data["citation"] = {**data["citation"], "excerpt_sha256": descriptor["sha256"]}
                data["attachments"] = list({a["sha256"]: a for a in data["attachments"]}.values())
                return store.commit(arguments["operation_id"], [{"kind": "Evidence", "id": arguments["id"],
                                    "expected_rev": arguments["expected_rev"], "data": data}])
            fields(arguments, "operation_id changes")
            require(all(c["kind"] in ("Note", "Obligation", "Problem") for c in arguments["changes"]),
                    "FORBIDDEN", "Use dedicated evidence and human review entries")
            return store.commit(**arguments)


def dispatch(root, request):
    fields(request, "action args")
    action, args = request["action"], request["args"]
    projects = Projects(root)
    if action in ("project-context", "project-associate", "project-disassociate", "project-preferences"):
        from project_access import dispatch_access
        return dispatch_access(root, action, args)
    if action in ("material-begin", "material-chunk", "material-preview", "material-confirm", "material-cleanup", "material-read", "material-bytes"):
        from material_store import dispatch_material
        return dispatch_material(root, action, args)
    if action in ("library-list", "library-read", "library-update", "library-remove", "library-restore"):
        from library_store import dispatch_library
        return dispatch_library(root, action, args)
    if action == "selection-export":
        from selection_export import export_selection
        args = {"material_refs": None, "include_material_originals": False, "include_dependencies": False,
                "include_process": False, "include_summary": True, **args}
        fields(args, "project_id expected_seq expected_library_version selected_refs material_refs include_material_originals include_dependencies include_process include_summary session_id")
        identifier(args["session_id"])
        return export_selection(projects, **args)
    if action == "selection-bytes":
        from selection_export import read_export
        fields(args, "project_id export_id offset limit session_id")
        identifier(args["session_id"])
        return read_export(projects, **args)
    if action == "list":
        require(type(args) is dict and set(args) <= {"include_removed"}, "INVALID_SCHEMA", "Expected optional include_removed")
        require(type(args.get("include_removed", False)) is bool, "INVALID_SCHEMA", "Expected include_removed boolean")
        return projects.listing(**args)
    if action == "create":
        fields(args, "session_id problem")
        return projects.create(**args)
    if action == "import-legacy":
        fields(args, "session_id campaign")
        return projects.import_legacy(**args)
    if action == "import-legacy-preview":
        fields(args, "campaign")
        return projects.import_legacy_preview(**args)
    if action == "import-legacy-confirm":
        fields(args, "session_id campaign expected_fingerprint operation_id")
        return projects.import_legacy_confirm(**args)
    if action in ("project-draft-read", "project-draft-save", "finalize-draft", "scratchpad-read",
                  "scratchpad-save", "scratchpad-restore", "scratchpad-promote-link"):
        from workbench_store import Workbench
        workbench = Workbench(root)
        require(type(args) is dict, "INVALID_SCHEMA", "Expected action arguments")
        if action == "project-draft-read":
            args = {"draft_id": None, **args}
            fields(args, "session_id draft_id")
            return workbench.draft_read(**args)
        if action == "project-draft-save":
            args = {"draft_id": None, **args}
            fields(args, "session_id draft_id expected_rev operation_id patch")
            return workbench.draft_save(**args)
        if action == "finalize-draft":
            fields(args, "session_id draft_id expected_rev operation_id")
            return workbench.finalize_draft(**args)
        if action == "scratchpad-read":
            args = {"rev": None, **args}
            fields(args, "session_id project_id rev")
            return workbench.scratchpad_read(**args)
        if action == "scratchpad-save":
            fields(args, "session_id project_id expected_rev operation_id title body")
            return workbench.scratchpad_save(**args)
        if action == "scratchpad-restore":
            fields(args, "session_id project_id expected_rev operation_id restore_rev")
            return workbench.scratchpad_restore(**args)
        fields(args, "session_id project_id scratchpad_rev target operation_id")
        return workbench.scratchpad_promote_link(**args)
    if action == "overview":
        fields(args, "project_id")
        return projects.overview(**args)
    if action == "attachment":
        fields(args, "project_id object_id rev sha256")
        return projects.attachment(**args)
    if action in ("commit", "review-preview", "review", "evidence"):
        fields(args, "project_id request")
        return projects.mutate(args["project_id"], action, args["request"])
    if action == "replay":
        from replay import aggregate
        fields(args, "project_id sources")
        return aggregate(projects, **args)
    if action == "export":
        from bundle import export_project
        require(set(args) in ({"project_id", "sources", "expected_seq"}, {"project_id", "sources", "expected_seq", "run"}),
                "INVALID_SCHEMA", "Expected export arguments with optional run evidence")
        return export_project(projects, **args)
    raise StoreError("FORBIDDEN", "Unknown action")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    options = parser.parse_args()
    try:
        print(json.dumps({"ok": True, "value": dispatch(options.root, decode(sys.stdin.buffer.read()))}, ensure_ascii=False))
    except (StoreError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}, ensure_ascii=False))
        sys.exit(1)

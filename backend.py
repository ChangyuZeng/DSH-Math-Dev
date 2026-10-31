import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
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
from research_store import ResearchStore, read_regular, safe_path
from domain import ResearchView, reference


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def checked_root(path):
    root = Path(path).absolute()
    require(all(not p.is_symlink() for p in [root, *root.parents]), "UNSAFE_PATH", "Symlink root")
    return root


@contextmanager
def open_store(path, **kwargs):
    raw = read_regular(safe_path(checked_root(path), "events.jsonl"))
    require(not raw or raw.endswith(b"\n"), "INCOMPLETE_TAIL", "Read stopped at incomplete journal")
    version = decode(raw.splitlines()[0])["schema_version"] if raw else 4
    require(version in (3, 4), "UNKNOWN_SCHEMA", "Schema 1/2 require their original reader; no migration")
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
        return decode(read_regular(safe_path(self.path(project_id), "project.json")))

    def listing(self):
        directory = self.root / "projects"
        return [self.metadata(p.name) for p in sorted(directory.iterdir()) if p.is_dir()] if directory.exists() else []

    def create(self, session_id, problem):
        identifier(session_id)
        validate_data("Problem", problem)
        project_id = str(uuid.uuid4())
        path = self.path(project_id)
        path.mkdir(parents=True, mode=0o700)
        with ResearchStore(path / "research", writable=True, create=True,
                           origin={"source": "dsh-math-human", "session_id": session_id,
                                   "run_id": None, "tool_call_id": None}) as store:
            store.commit("create", [{"kind": "Problem", "id": "P", "expected_rev": 0, "data": problem}])
            campaign_id = store.campaign_id
        metadata = {"format": "dsh-math-project-v1", "id": project_id, "campaign_id": campaign_id,
                    "session_id": session_id, "title": problem["title"], "schema_version": 4,
                    "mode": "daily_research", "execution_authorized": False}
        write_new(path / "project.json", metadata)
        return metadata

    def import_legacy(self, session_id, campaign):
        identifier(session_id)
        source = checked_root(campaign)
        import subprocess
        import shutil
        snapshot_result = subprocess.run([sys.executable, "-B", str(BASE / "cli.py"), str(source), "replay"], capture_output=True, timeout=30)
        require(snapshot_result.returncode == 0, "LEGACY_READ_FAILED", snapshot_result.stderr.decode())
        snapshot = decode(snapshot_result.stdout)
        require(snapshot["usable"], "UNUSABLE", "Legacy source is not usable")
        project_id = str(uuid.uuid4())
        destination = self.path(project_id)
        destination.mkdir(parents=True, mode=0o700)
        exported = destination / "legacy-import"
        result = subprocess.run([sys.executable, "-B", str(BASE / "cli.py"), str(source), "export", str(exported), "--expected-seq", str(snapshot["state"]["seq"])], capture_output=True, timeout=60)
        require(result.returncode == 0, "LEGACY_READ_FAILED", result.stderr.decode())
        require(decode(read_regular(exported / "snapshot.json")) == snapshot, "SOURCE_CHANGED", "Legacy source changed")
        shutil.copytree(exported / "research", destination / "research")
        root_problem = next(v[-1] for v in snapshot["state"]["objects"].values() if v[-1]["kind"] == "Problem" and v[-1]["data"]["parent"] is None)
        metadata = {"format": "dsh-math-project-v1", "id": project_id, "campaign_id": snapshot["state"]["campaign_id"],
                    "session_id": session_id, "title": root_problem["data"]["title"], "schema_version": 3,
                    "mode": "legacy_readonly_copy", "execution_authorized": False}
        write_new(destination / "project.json", metadata)
        return metadata

    def overview(self, project_id):
        metadata = self.metadata(project_id)
        with open_store(self.path(project_id) / "research") as store:
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
        with open_store(self.path(project_id) / "research", writable=operation != "review-preview",
                        actor={"kind": "human", "id": "local-user"},
                        origin={"source": "dsh-math-human", "session_id": metadata["session_id"], "run_id": None, "tool_call_id": None}) as store:
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
    if action == "list":
        fields(args, "")
        return projects.listing()
    if action == "create":
        fields(args, "session_id problem")
        return projects.create(**args)
    if action == "import-legacy":
        fields(args, "session_id campaign")
        return projects.import_legacy(**args)
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
    except (StoreError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}, ensure_ascii=False))
        sys.exit(1)

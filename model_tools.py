"""DSH model tool entrypoint. Actor, origin, project and paths come only from the host."""

from copy import deepcopy
import argparse
import json
from pathlib import Path
import sys

from backend import HERE, Projects
from isolated_tools import file_read, file_write, material_read, python_run, workspace_size
from m_runner import load_manifest, verify_sources
from run_ledger import Halt, Ledger
from schema import StoreError, decode, fields, require

TOOL_NAMES = ("research_read", "note_save", "obligation_save", "evidence_register", "check_witness",
              "material_read", "file_read", "file_write", "python_run", "research_finish")


def _execute(projects, store, manifest, name, arguments):
    if name == "research_read":
        fields(arguments, "kind state text object_id rev")
        return store.research_read(**arguments)
    if name in ("note_save", "obligation_save"):
        fields(arguments, "operation_id id expected_rev data")
        kind = "Note" if name == "note_save" else "Obligation"
        return store.commit(arguments["operation_id"], [{"kind": kind, "id": arguments["id"],
                            "expected_rev": arguments["expected_rev"], "data": arguments["data"]}])
    if name == "evidence_register":
        fields(arguments, "operation_id id expected_rev data attachment_text")
        data = deepcopy(arguments["data"])
        require(type(data) is dict and data.get("attachments") == [], "INVALID_SCHEMA",
                "Set data.attachments to exactly []; the host archives attachment_text and replaces it with the stored attachment")
        text = arguments["attachment_text"]
        require(type(text) is str and len(text.encode("utf-8")) <= 384 * 1024, "UNSUPPORTED", "Evidence text exceeds 384 KiB")
        from exporting import guard_content
        raw = text.encode("utf-8")
        guard_content(raw)
        data["attachments"] = [store._archive_bytes(raw, "model-evidence.txt")]
        if data.get("citation") is not None:
            data["citation"] = {**data["citation"], "excerpt_sha256": data["attachments"][0]["sha256"]}
        return store.commit(arguments["operation_id"], [{"kind": "Evidence", "id": arguments["id"],
                            "expected_rev": arguments["expected_rev"], "data": data}])
    if name == "check_witness":
        from checking import run_check
        fields(arguments, "operation_id checker_id target witness")
        return run_check(store, **arguments)
    if name == "material_read":
        fields(arguments, "name")
        return material_read(manifest, arguments["name"])
    if name == "file_read":
        fields(arguments, "path")
        return file_read(manifest, arguments["path"])
    if name == "file_write":
        fields(arguments, "path text")
        return file_write(manifest, arguments["path"], arguments["text"])
    if name == "python_run":
        fields(arguments, "code")
        return python_run(manifest, arguments["code"])
    if name == "research_finish":
        fields(arguments, "classification summary references")
        require(arguments["classification"] in ("candidate", "no_progress", "local_claim", "known_reproduction", "valid_below_target"),
                "INVALID_SCHEMA", "Unknown research classification")
        require(type(arguments["summary"]) is str and 0 < len(arguments["summary"]) <= 16000,
                "INVALID_SCHEMA", "Bounded research summary required")
        from schema import refs
        refs(arguments["references"])
        for reference in arguments["references"]:
            store.get(reference["id"], reference["rev"])
        return {**arguments, "scientific_status": "candidate_pending_review", "request_stop": True}
    raise StoreError("FORBIDDEN", "Tool is not on the model allowlist")


def dispatch(root, request):
    anchored = "manifest_sha256" in request
    fields(request, "project_id session_id run_id tool_call_id name arguments manifest" + (" manifest_sha256" if anchored else ""))
    require(request["name"] in TOOL_NAMES, "FORBIDDEN", "Tool is not on the model allowlist")
    require(type(request["arguments"]) is dict, "INVALID_SCHEMA", "Expected tool arguments object")
    manifest, raw = load_manifest(request["manifest"])
    if manifest["protocol_id"] == "DSH-MATH-M-SELF-DIRECTED-v1":
        from m_runner import manifest_sha256
        require(anchored and request["manifest_sha256"] == manifest_sha256(raw), "MANIFEST_CHANGED", "Host manifest anchor changed")
    expected = {"project_id": manifest["project_id"], "session_id": manifest["session_id"], "run_id": manifest["run_id"]}
    require(all(request[key] == value for key, value in expected.items()), "FORBIDDEN", "Host/run identity mismatch")
    projects = Projects(root)
    metadata = projects.metadata(manifest["project_id"])
    require(metadata["session_id"] == manifest["session_id"] and metadata["schema_version"] == 4,
            "FORBIDDEN", "Project/session binding mismatch")
    verify_sources(manifest)
    output = Path(manifest["output_dir"])
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    Path(manifest["workspace"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = Ledger(output / "ledger", manifest)
    try:
        admission = ledger.tool_start(request["tool_call_id"], request["name"], request["arguments"])
        if admission["status"] == "completed":
            return {"result": {**admission["result"], "idempotent": True}, "operation_model_requests": 0}
        if admission["status"] != "started":
            return {"result": admission, "operation_model_requests": 0}
        import resource
        started_cpu = resource.getrusage(resource.RUSAGE_SELF)
        started_children = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu_seconds = 0.0
        try:
            from backend import open_store
            with open_store(projects.path(manifest["project_id"]) / "research", writable=request["name"] != "research_read",
                            actor={"kind": "model", "id": manifest["actor_id"]},
                            origin={"source": "dsh-math-model", "run_id": manifest["run_id"],
                                    "session_id": manifest["session_id"], "tool_call_id": request["tool_call_id"]}) as store:
                result = _execute(projects, store, manifest, request["name"], request["arguments"])
            ended_cpu = resource.getrusage(resource.RUSAGE_SELF)
            ended_children = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_seconds = sum((ended_cpu.ru_utime - started_cpu.ru_utime, ended_cpu.ru_stime - started_cpu.ru_stime,
                               ended_children.ru_utime - started_children.ru_utime, ended_children.ru_stime - started_children.ru_stime))
            receipt = {"status": "completed", "value": result, "idempotent": False}
            ledger.finish_tool(request["tool_call_id"], receipt, cpu_seconds=cpu_seconds,
                               artifact_bytes=workspace_size(manifest["workspace"]))
            if request["name"] == "research_finish":
                ledger.seal("model_finished:" + result["classification"])
            if request["name"] == "check_witness" and result.get("review"):
                checked = result["review"]["data"]["execution"]["result"]
                if checked["verdict"] == "pass" and checked.get("values", {}).get("target_met") is True:
                    ledger.seal("target_candidate_pending_review")
            return {"result": receipt, "operation_model_requests": 0}
        except (StoreError, OSError, ValueError, KeyError, TypeError, Halt) as error:
            failure = {"status": "error", "error": getattr(error, "code", type(error).__name__), "message": str(error)}
            ended_cpu = resource.getrusage(resource.RUSAGE_SELF)
            ended_children = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_seconds = sum((ended_cpu.ru_utime - started_cpu.ru_utime, ended_cpu.ru_stime - started_cpu.ru_stime,
                               ended_children.ru_utime - started_children.ru_utime, ended_children.ru_stime - started_children.ru_stime))
            if any(row["kind"] == "tool.finished" and row["call_id"] == request["tool_call_id"] for row in ledger.rows):
                raise
            ledger.finish_tool(request["tool_call_id"], failure, cpu_seconds=cpu_seconds,
                               artifact_bytes=workspace_size(manifest["workspace"]))
            raise
    finally:
        ledger.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    options = parser.parse_args()
    try:
        value = dispatch(options.root, decode(sys.stdin.buffer.read()))
        print(json.dumps({"ok": True, "value": value}, ensure_ascii=False))
    except (StoreError, OSError, ValueError, KeyError, TypeError, Halt) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

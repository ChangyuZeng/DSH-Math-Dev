"""Meter the original Rethlas MCP applications without replacing their tools.

This module is copied next to the pinned upstream tree. FastMCP owns dispatch;
Rethlas owns memory, BM25, branch state, report validation and report writing.
Only its external-service transport and tool admission are adapted. No model
loop or credential discovery is implemented here.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
SITE = Path(os.environ["FOUR_HARNESS_PYTHON_SITE"]).resolve()
if not SITE.is_dir() or os.environ.get("FOUR_HARNESS_OFFLINE_FIXTURE") != "1":
    raise RuntimeError("R MCP currently requires the isolated offline qualification environment")
sys.path.insert(0, str(SITE))
sys.modules.setdefault("four_harness_mcp", sys.modules[__name__])
# FastMCP registers a Markdown MIME type at import. Use Python's built-in
# table; host Apache/MIME configuration is outside the isolated environment.
mimetypes.knownfiles = []
mimetypes.init(files=[])

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import Tool

COMMON = {"materials_list", "materials_read", "workspace_read", "workspace_write", "compute", "check", "submit"}
NATIVE = {
    "generation": {"search_arxiv_theorems", "verify_proof_service", "memory_init", "memory_append", "memory_search", "branch_update"},
    "verification": {"search_arxiv_theorems", "memory_init", "memory_append", "memory_query", "validate_verification_output", "write_verification_output"},
}
ACTIVE = ContextVar("four_harness_rethlas_mcp", default=None)


def encoded(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("Gateway redirects are forbidden")


def post(role, endpoint, payload, *, metadata=None, parent=None):
    routes = json.loads(os.environ["FOUR_HARNESS_ROUTES"])
    route = routes[role]
    parsed = urlsplit(route["url"])
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path != "/R/" + role or parsed.query or parsed.fragment or parsed.username):
        raise RuntimeError("R MCP requires the bound numeric localhost role gateway")
    headers = {"Authorization": "Bearer " + route["token"], "Content-Type": "application/json"}
    if metadata is not None:
        headers["X-Codex-Turn-Metadata"] = encoded(metadata).decode()
    if parent is not None:
        headers["X-Four-Parent-Request-Id"] = parent
    request = Request(route["url"] + "/" + endpoint, data=encoded(payload), headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=75) as response:
            result = json.load(response)
    except HTTPError as error:
        result = json.load(error)
        raise RuntimeError("Gateway rejected the R MCP operation: " + str(result.get("error"))) from error
    if not isinstance(result, dict):
        raise RuntimeError("Gateway returned an invalid response object")
    if result.get("error") or result.get("ok") is False:
        raise RuntimeError("Gateway rejected the R MCP operation: " + str(result.get("error")))
    return result


def active_call():
    call = ACTIVE.get()
    if call is None:
        raise RuntimeError("Original R service may only execute inside a metered MCP call")
    return call


def common_call(name, arguments):
    call = active_call()
    result = post(call["role"], "tools", {"name": name, "arguments": arguments},
                  metadata=call["metadata"], parent=call.get("parent_request_id"))
    return result["result"]


def _cpu():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


class MeteredOriginalTools(Middleware):
    def __init__(self, mode):
        self.mode = mode

    async def on_call_tool(self, context, call_next):
        request_context = context.fastmcp_context.request_context
        if request_context is None or request_context.meta is None:
            raise RuntimeError("Native MCP request metadata is required before any tool executes")
        meta = request_context.meta.model_dump(by_alias=True, exclude_none=True)
        native = meta.get("x-codex-turn-metadata")
        if not isinstance(native, dict) or not native.get("thread_id") or not native.get("turn_id"):
            raise RuntimeError("Native MCP must identify its actual thread and turn")
        role = "verifier" if self.mode == "verification" else (
            "strategy" if native.get("subagent_kind") == "thread_spawn" and native.get("parent_thread_id")
            else os.environ.get("FOUR_HARNESS_ROLE", "generator"))
        if role not in ({"verifier"} if self.mode == "verification" else {"generator", "strategy", "repair"}):
            raise RuntimeError("MCP role differs from the frozen original module")
        name = context.message.name
        if name not in COMMON | NATIVE[self.mode]:
            raise RuntimeError("Unregistered native tool")
        call_id = meta.get("callId")
        if not isinstance(call_id, str) or not call_id:
            raise RuntimeError("Native model tool callId is required")
        current = {"role": role, "metadata": native, "call_id": call_id}
        token = ACTIVE.set(current)
        try:
            if name in COMMON:
                return await call_next(context)
            identity = {"name": "rethlas_" + self.mode + "_" + name, "call_id": call_id}
            receipt = await asyncio.to_thread(post, role, "intrinsic", {
                **identity, "phase": "start", "args_sha256": digest(context.message.arguments or {}),
            }, metadata=native)
            parent = receipt.get("parent_request_id")
            if not isinstance(parent, str) or not parent:
                raise RuntimeError("Gateway did not bind the intrinsic tool to a physical model request")
            current["parent_request_id"] = parent
            started, cpu = time.monotonic(), _cpu()
            outcome = {"error": "original tool did not finish"}
            status = "failed"
            try:
                result = await call_next(context)
                outcome, status = result, "succeeded"
                return result
            except BaseException as error:
                outcome = {"error_type": type(error).__name__, "error": str(error)}
                raise
            finally:
                await asyncio.to_thread(post, role, "intrinsic", {
                    **identity, "phase": "finish", "result_sha256": digest(outcome), "status": status,
                    "cpu_seconds": max(0.0, _cpu() - cpu), "wall_seconds": time.monotonic() - started,
                }, metadata=native, parent=parent)
        finally:
            ACTIVE.reset(token)


class LocalServiceResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned original module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verification_environment():
    call = active_call()
    parent = call.get("parent_request_id")
    if not parent:
        raise RuntimeError("Verifier subprocess lacks an admitted physical parent request")
    return {**os.environ, "FOUR_HARNESS_ROLE": "verifier", "FOUR_HARNESS_PARENT_REQUEST_ID": parent,
            "FOUR_HARNESS_PARENT_NATIVE_METADATA": encoded(call["metadata"]).decode()}


class VerifierSubprocess:
    """Only add the admitted parent to the original subprocess.run invocation."""
    TimeoutExpired = subprocess.TimeoutExpired
    STDOUT = subprocess.STDOUT

    @staticmethod
    def run(*args, **kwargs):
        if "env" in kwargs:
            raise RuntimeError("Original verifier unexpectedly supplied an environment")
        return subprocess.run(*args, **kwargs, env=verification_environment())


class OriginalServiceTransport:
    """Adapt only the two known service calls; never forward arbitrary URLs."""
    def __init__(self, mode):
        self.mode = mode

    def post(self, endpoint, *, json, timeout):
        if endpoint == "https://leansearch.net/thm/search":
            # Preserve upstream query validation and response normalization. The
            # corpus comes exclusively through the same metered material tools.
            query = json["query"].casefold()
            terms = set(re.findall(r"[\w]+", query))
            documents = common_call("materials_list", {})["materials"]
            matches = []
            for document in documents:
                if document["size"] > 65536:
                    raise RuntimeError("Frozen reference exceeds one bounded window; use common materials_read explicitly")
                window = common_call("materials_read", {"id": document["id"], "length": max(1, document["size"])})
                text = window.get("text")
                if not isinstance(text, str):
                    raise RuntimeError("Frozen reference is not UTF-8 text")
                if terms.intersection(re.findall(r"[\w]+", text.casefold())):
                    matches.append({"title": document["id"], "theorem": text, "arxiv_id": "",
                                    "theorem_id": "frozen-sha256:" + document["sha256"]})
            return LocalServiceResponse(matches[:json["num_results"]])
        if self.mode == "generation" and endpoint == "http://127.0.0.1:8091/verify":
            active_call()  # Guard before importing or dispatching the original verifier.
            api = _load_module("four_harness_original_verifier_api", ROOT / "agents/verification/api/server.py")
            api.subprocess = VerifierSubprocess
            report = api.verify(api.VerifyRequest(**json))
            return LocalServiceResponse(report)
        raise RuntimeError("Original R service attempted an unregistered endpoint")


def attach_common(app):
    def materials_list() -> dict:
        return common_call("materials_list", {})

    def materials_read(id: str, offset: int = 0, length: int = 65536) -> dict:
        return common_call("materials_read", {"id": id, "offset": offset, "length": length})

    def workspace_read(path: str, offset: int = 0, length: int = 65536) -> dict:
        return common_call("workspace_read", {"path": path, "offset": offset, "length": length})

    def workspace_write(path: str, text: str) -> dict:
        return common_call("workspace_write", {"path": path, "text": text})

    def compute(code: str) -> dict:
        return common_call("compute", {"code": code})

    def check(candidate: dict) -> dict:
        return common_call("check", {"candidate": candidate})

    def submit(candidate: dict) -> dict:
        return common_call("submit", {"candidate": candidate})

    functions = {function.__name__: function for function in (materials_list, materials_read, workspace_read, workspace_write, compute, check, submit)}
    for schema in json.loads((ROOT / "four-harness-tools.json").read_text()):
        function = functions[schema["name"]]
        tool = Tool.from_function(function, name=schema["name"], description=schema["description"])
        tool.parameters = schema["parameters"]
        app.add_tool(tool)


def load_app(mode):
    if mode not in NATIVE:
        raise RuntimeError("Unknown original R MCP application")
    original = _load_module("four_harness_original_" + mode, ROOT / ("agents/" + mode + "/mcp/server.py"))
    if original.APP is None:
        raise RuntimeError("Pinned FastMCP dependency is missing")
    original.requests = OriginalServiceTransport(mode)
    original.APP.add_middleware(MeteredOriginalTools(mode))
    attach_common(original.APP)
    return original.APP


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: four_harness_mcp.py generation|verification")
    load_app(sys.argv[1]).run(transport="stdio", show_banner=False)

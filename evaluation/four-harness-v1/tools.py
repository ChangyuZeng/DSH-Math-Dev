"""Common offline tools for four-harness-v1 (no model dispatch).

API::

    host = CommonTools(run_id="toy-C-1", run_root=fresh_directory,
                       materials={"statement": {"path": path, "sha256": digest}},
                       limits=ToolLimits(), accounting=event_callback)
    reply = host.call("workspace.write", {"path": "notes.txt", "text": "hello"})
    reply = host.call("compute", {"code": "print(1 + 1)"}, cancel_event=event)

All calls return JSON {ok, result|error, metrics}. The optional synchronous
accounting callback receives tool_started/tool_finished, including request_id,
run_id, argument/result hashes and measured CPU/wall time. A failing begin
callback prevents dispatch; a failing end callback makes the reply fail closed.
No callback is needed to obtain metrics. Callers must enforce cumulative ledger
budgets and cancellation using these hooks; this module enforces per-call limits
and persistent artifact quotas. Use one host instance per fresh run directory.

Compute is standard-library Python, with an isolated interpreter (-I -S).
Filesystem access is read-only: WORKSPACE and MATERIALS contain snapshots, and
artifacts must be written through workspace.write. macOS requires Seatbelt;
Linux requires bubblewrap plus seccomp. Neither backend falls back to Python
restrictions. A real containment probe precedes submitted computation.

ToolLimits are host protection, separate from fair model budgets. Darwin defaults
to resource_mode="macos_monitored": CPU time, output, wall time and process guards
remain, while memory uses a sampled RSS kill watchdog without RLIMIT_AS. It makes
no hard resident-memory or CPU-placement guarantee. Optional "strict" mode
requires RLIMIT_AS and fails closed if the OS rejects it; it never falls back.
Linux defaults to strict mode. CPU placement is unconstrained unless explicitly
limited on Linux. Kernel CPU/RSS observations include their sources; unavailable
observations stay null. Watchdog peaks can miss allocations between samples.

Host credentials, host environment and original materials paths never enter the
sandbox. There are no network/model imports, shells, arbitrary parent evals,
or user-controlled command lines. Host storage and checker code are trusted;
immutable receipts mean immutable through this API, not against the host owner.
"""

import base64
import binascii
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import uuid


HERE = Path(__file__).resolve().parent
AUDIT_PATH = HERE / "audit.py"
CHILD_PATH = HERE / "compute_child.py"
TOOL_NAMES = ("materials.list", "materials.read", "workspace.read", "workspace.write",
              "compute", "check", "submit")


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolLimits:
    """Per-tool host protection; these are not fair campaign resource budgets."""
    resource_mode: str = "macos_monitored" if sys.platform == "darwin" else "strict"
    max_read_bytes: int = 65536
    max_file_bytes: int = 16 * 1024 * 1024
    max_material_bytes: int = 16 * 1024 * 1024
    max_code_bytes: int = 256 * 1024
    max_output_bytes: int = 1024 * 1024
    artifact_bytes: int = 1024 * 1024 * 1024
    max_files: int = 4096
    cpu_seconds: float = 60.0
    timeout_seconds: float = 60.0
    memory_bytes: int = 4 * 1024 * 1024 * 1024
    cpu_cores: int | None = None

    def __post_init__(self):
        if self.resource_mode not in ("macos_monitored", "strict"):
            raise ValueError("resource_mode must be macos_monitored or strict")
        if self.resource_mode == "macos_monitored" and sys.platform != "darwin":
            raise ValueError("macos_monitored requires Darwin")
        for key, value in asdict(self).items():
            if key == "resource_mode" or key == "cpu_cores" and value is None:
                continue
            expected = (int, float) if key in ("cpu_seconds", "timeout_seconds") else (int,)
            if type(value) not in expected or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a finite positive number")
        if self.cpu_cores is not None and sys.platform != "linux":
            raise ValueError("CPU placement limits require Linux; use cpu_cores=None on macOS")
        if self.memory_bytes < 64 * 1024 * 1024:
            raise ValueError("memory_bytes must be at least 64 MiB")


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ToolError("invalid_arguments", "arguments must be finite JSON values") from exc


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parts(path):
    if (type(path) is not str or not path or len(path) > 1024 or "\\" in path
            or "\x00" in path or path.startswith("/")):
        raise ToolError("invalid_path", "path must be a relative POSIX path")
    parts = path.split("/")
    if any(p in ("", ".", "..") or len(p.encode("utf-8")) > 255 for p in parts):
        raise ToolError("invalid_path", "empty, dot and parent path components are forbidden")
    return parts


def _absolute(path):
    value = os.path.abspath(os.fspath(path))
    # macOS exposes its standard temporary directories through these OS aliases.
    # All caller-controlled components are still opened with O_NOFOLLOW.
    if sys.platform == "darwin":
        for alias in ("/var", "/tmp"):
            if value == alias or value.startswith(alias + "/"):
                value = os.path.realpath(alias) + value[len(alias):]
                break
    return Path(value)


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _open_directory(path, create=False):
    path = _absolute(path)
    fd = os.open("/", _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            new = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except OSError as exc:
        os.close(fd)
        raise ToolError("unsafe_path", "directory contains a symlink or is inaccessible") from exc


def _read_file_at(directory_fd, name, maximum):
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ToolError("unsafe_path", "only regular files with one hard link are allowed")
            if before.st_size > maximum:
                raise ToolError("file_limit", "file exceeds the allowed size")
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum:
                    raise ToolError("file_limit", "file exceeds the allowed size")
            after = os.fstat(fd)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or after.st_nlink != 1):
                raise ToolError("file_changed", "file changed while being read")
            return b"".join(chunks)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ToolError("unsafe_path", "file is missing, linked, or inaccessible") from exc


def _read_absolute(path, maximum):
    path = _absolute(path)
    parent = _open_directory(path.parent)
    try:
        return _read_file_at(parent, path.name, maximum)
    finally:
        os.close(parent)


class _Store:
    def __init__(self, path, limits):
        self.path = _absolute(path)
        self.limits = limits
        fd = _open_directory(self.path, create=True)
        self.identity = os.fstat(fd).st_dev, os.fstat(fd).st_ino
        os.close(fd)

    def root(self):
        fd = _open_directory(self.path)
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != self.identity:
            os.close(fd)
            raise ToolError("unsafe_path", "run directory identity changed")
        return fd

    def parent(self, path, create=False):
        parts = _parts(path)
        fd = self.root()
        try:
            for part in parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                new = os.open(part, _DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = new
            return fd, parts[-1]
        except OSError as exc:
            os.close(fd)
            raise ToolError("unsafe_path", "parent path is linked, missing, or inaccessible") from exc

    def read(self, path, maximum=None):
        fd, name = self.parent(path)
        try:
            return _read_file_at(fd, name, maximum or self.limits.max_file_bytes)
        finally:
            os.close(fd)

    def inventory(self):
        entries, total, count = [], 0, 0

        def visit(fd, prefix):
            nonlocal total, count
            with os.scandir(fd) as listing:
                names = sorted(entry.name for entry in listing)
            for name in names:
                count += 1
                if count > self.limits.max_files:
                    raise ToolError("artifact_limit", "too many artifact files or directories")
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                relative = f"{prefix}{name}"
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    try:
                        visit(child, relative + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if info.st_size > self.limits.max_file_bytes:
                        raise ToolError("file_limit", "workspace artifact exceeds the file limit")
                    total += info.st_size
                    if total > self.limits.artifact_bytes:
                        raise ToolError("artifact_limit", "artifact byte quota exceeded")
                    entries.append((relative, info.st_size))
                else:
                    raise ToolError("unsafe_path", "workspace contains links or special files")

        fd = self.root()
        try:
            visit(fd, "")
        except OSError as exc:
            raise ToolError("unsafe_path", "workspace changed during inspection") from exc
        finally:
            os.close(fd)
        return entries, total, count

    def write(self, path, data, exclusive=False):
        fd, name = self.parent(path, create=True)
        temporary = ".write-" + uuid.uuid4().hex
        created = False
        try:
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ToolError("unsafe_path", "destination is linked or not a regular file")
            if exclusive:
                handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o400, dir_fd=fd)
            else:
                handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=fd)
                created = True
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if not exclusive:
                # Both names are under the verified directory descriptor. A
                # concurrently introduced destination symlink is replaced,
                # never followed; original hard-linked files are never edited.
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                created = False
            os.fsync(fd)
        except FileExistsError as exc:
            raise ToolError("immutable", "immutable artifact already exists") from exc
        except OSError as exc:
            raise ToolError("storage_error", "artifact could not be persisted") from exc
        finally:
            if created:
                os.unlink(temporary, dir_fd=fd)
            os.close(fd)


def _window(data, args, limits):
    offset = args.get("offset", 0)
    length = args.get("length", limits.max_read_bytes)
    if type(offset) is not int or offset < 0 or type(length) is not int or not 0 <= length <= limits.max_read_bytes:
        raise ToolError("invalid_arguments", "invalid byte window")
    chunk = data[offset:offset + length]
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    return {"offset": offset, "length": len(chunk), "size": len(data),
            "eof": offset + len(chunk) >= len(data), "sha256": _hash(data),
            "base64": base64.b64encode(chunk).decode("ascii"), "text": text}


def _arguments(args, required=(), optional=()):
    if type(args) is not dict or not set(required) <= set(args) or set(args) - set(required) - set(optional):
        raise ToolError("invalid_arguments", "unknown or missing tool arguments")


def _observed_number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or positive and value == 0:
        return None
    return value


def _wait_usage(observation):
    user = _observed_number(getattr(observation, "ru_utime", None))
    system = _observed_number(getattr(observation, "ru_stime", None))
    cpu = user + system if user is not None and system is not None else None
    peak = _observed_number(getattr(observation, "ru_maxrss", None), positive=True)
    if peak is not None and sys.platform != "darwin":
        peak *= 1024
    return cpu, peak


def _rss(pid):
    """Kernel resident bytes for watchdog; absence is unknown, never zero."""
    try:
        if sys.platform == "linux":
            with open(f"/proc/{pid}/statm", encoding="ascii") as handle:
                return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        if sys.platform == "darwin":
            import ctypes

            class TaskInfo(ctypes.Structure):
                _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                            ("total_user", ctypes.c_uint64), ("total_system", ctypes.c_uint64),
                            ("threads_user", ctypes.c_uint64), ("threads_system", ctypes.c_uint64),
                            ("policy", ctypes.c_int32), ("faults", ctypes.c_int32),
                            ("pageins", ctypes.c_int32), ("cow_faults", ctypes.c_int32),
                            ("messages_sent", ctypes.c_int32), ("messages_received", ctypes.c_int32),
                            ("syscalls_mach", ctypes.c_int32), ("syscalls_unix", ctypes.c_int32),
                            ("csw", ctypes.c_int32), ("threadnum", ctypes.c_int32),
                            ("numrunning", ctypes.c_int32), ("priority", ctypes.c_int32)]

            info = TaskInfo()
            libc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            size = libc.proc_pidinfo(pid, 4, 0, ctypes.byref(info), ctypes.sizeof(info))
            return info.resident_size if size == ctypes.sizeof(info) else None
    except (OSError, ValueError, IndexError):
        pass
    return None


class CommonTools:
    def __init__(self, run_id, run_root, materials=None, limits=None, accounting=None):
        if type(run_id) is not str or not run_id or len(run_id) > 128:
            raise ValueError("run_id must be a nonempty string of at most 128 characters")
        if accounting is not None and not callable(accounting):
            raise ValueError("accounting must be callable")
        self.run_id = run_id
        self._initial_run_id = run_id
        self._call_count = 0
        self.limits = limits or ToolLimits()
        self.accounting = accounting
        self.run_root = _absolute(run_root)
        root = _open_directory(self.run_root, create=True)
        try:
            if os.listdir(root):
                raise ValueError("run_root must be fresh and empty; reopening a run is not permitted")
        finally:
            os.close(root)
        self.workspace = _Store(self.run_root / "workspace", self.limits)
        self.control = _Store(self.run_root / ".host", self.limits)
        self.submissions = _Store(self.control.path / "submissions", self.limits)
        self.control.write("identity.json", _json_bytes({"run_id": run_id}), exclusive=True)
        self._lock = threading.RLock()
        self._materials = {}
        for name, entry in (materials or {}).items():
            if type(name) is not str or not name or len(name) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in name):
                raise ValueError("material IDs must be short ASCII identifiers")
            if type(entry) is not dict or set(entry) != {"path", "sha256"}:
                raise ValueError("materials require path and frozen sha256")
            digest = entry["sha256"]
            if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("material sha256 must be a lowercase SHA-256 digest")
            self._materials[name] = {"path": _absolute(entry["path"]), "sha256": digest}
            self._material(name)
        self._checker_bytes = _read_absolute(AUDIT_PATH, 1024 * 1024)
        self.checker_sha256 = _hash(self._checker_bytes)
        self._child_bytes = _read_absolute(CHILD_PATH, 1024 * 1024)
        self._child_sha256 = _hash(self._child_bytes)
        spec = importlib.util.spec_from_file_location("four_harness_frozen_audit", AUDIT_PATH)
        self._audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._audit)
        self._sandbox = None
        self._probe_result = None
        self._metrics = None
        self._python = Path(sys.executable).resolve()
        # Framework builds expose a launcher as sys.executable. Launch the
        # actual interpreter directly so the sandbox never needs a second exec.
        framework_python = Path(sys.base_prefix).resolve() / "Resources/Python.app/Contents/MacOS/Python"
        if sys.platform == "darwin" and framework_python.is_file():
            self._python = framework_python
        self._runtime = {"python_version": platform.python_version(),
                         "implementation": platform.python_implementation(),
                         "executable_sha256": _hash(_read_absolute(self._python, 64 * 1024 * 1024)),
                         "stdlib_only": True, "isolated_flags": ["-I", "-S", "-B"],
                         "platform": sys.platform, "machine": platform.machine(),
                         "bootstrap_sha256": self._child_sha256}
        if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
            self._sandbox = "seatbelt"
        elif sys.platform == "linux" and shutil.which("bwrap") and platform.machine().lower() in ("x86_64", "amd64", "aarch64", "arm64"):
            self._sandbox = "bubblewrap"

    def _resource_policy(self):
        strict = self.limits.resource_mode == "strict"
        return {"resource_mode": self.limits.resource_mode, "resource_scope": "host_protection",
                "memory_enforcement": "RLIMIT_AS+RSS_watchdog" if strict else "RSS_watchdog",
                "hard_memory_limit": False, "hard_resident_memory_limit": False,
                "address_space_limit_required": strict,
                "hard_cpu_core_limit": sys.platform == "linux" and self.limits.cpu_cores is not None,
                "cpu_placement": "affinity" if self.limits.cpu_cores is not None else "unconstrained",
                "cpu_time_guard": "RLIMIT_CPU", "cpu_time_limit_seconds": math.ceil(self.limits.cpu_seconds),
                "cpu_observation_source": "wait4_rusage", "rss_observation_source": "kernel_rss+wait4_ru_maxrss",
                "rss_sampling_interval_seconds": 0.01,
                "rss_watchdog_is_hard_limit": False}

    def capabilities(self):
        """Metadata only: containment is not qualified until the real probe passes."""
        return {"tools": list(TOOL_NAMES), "runtime": dict(self._runtime),
                "limits": asdict(self.limits), "sandbox_backend": self._sandbox,
                "sandbox_available": self._sandbox is not None,
                "sandbox_verified": self._probe_result is not None and self._probe_result.get("ok", False),
                "probe": self._probe_result, "compute_filesystem": "read_only_snapshots",
                **self._resource_policy(),
                "limitations": ["RSS watchdog can miss short allocations and overshoot between samples; unavailable readings are null",
                                "Resource guards protect the host and do not make equal-compute claims"] +
                    (["macOS has no enforced core placement; strict RLIMIT_AS may be rejected by the OS"]
                     if sys.platform == "darwin" else
                     ["Linux requires working unprivileged bubblewrap namespaces and supported seccomp ABI"])}

    def pristine_for_cold_start(self):
        """Verify an unused run with only constructor state, without accounting."""
        with self._lock:
            if self.run_id != self._initial_run_id or self._call_count or self._probe_result is not None:
                return False
            try:
                identity = _json_bytes({"run_id": self._initial_run_id})
                if self.control.read("identity.json") != identity:
                    return False
                if self.workspace.inventory()[2] or self.submissions.inventory()[2]:
                    return False
                entries, _, count = self.control.inventory()
                return entries == [("identity.json", len(identity))] and count == 2
            except (ToolError, OSError):
                return False

    def material_inventory(self):
        """Read-only frozen IDs/hashes for gateway preflight; rejects file drift."""
        with self._lock:
            return [{"id": key, "size": len(self._material(key)), "sha256": value["sha256"]}
                    for key, value in sorted(self._materials.items())]

    def _material(self, name):
        if name not in self._materials:
            raise ToolError("unknown_material", "unknown material ID")
        entry = self._materials[name]
        data = _read_absolute(entry["path"], self.limits.max_material_bytes)
        if _hash(data) != entry["sha256"]:
            raise ToolError("material_hash_drift", "material no longer matches its frozen hash")
        return data

    def _frozen_checker(self):
        if _hash(_read_absolute(AUDIT_PATH, 1024 * 1024)) != self.checker_sha256:
            raise ToolError("checker_hash_drift", "checker no longer matches its frozen hash")

    def _totals(self):
        entries, workspace_bytes, count = self.workspace.inventory()
        _, submission_bytes, submission_count = self.submissions.inventory()
        if workspace_bytes + submission_bytes > self.limits.artifact_bytes or count + submission_count > self.limits.max_files:
            raise ToolError("artifact_limit", "persistent artifact quota exceeded")
        return entries, workspace_bytes + submission_bytes, count + submission_count

    def call(self, name, arguments=None, *, cancel_event=None):
        started, parent_cpu = time.monotonic(), time.thread_time()
        request_id = uuid.uuid4().hex
        metrics = {"request_id": request_id, "run_id": self.run_id, "tool": name if type(name) is str else None,
                   "started_at": _utc(), "child_cpu_seconds": 0.0, "known_child_cpu_seconds": 0.0,
                   "cpu_accounting_complete": True, "resource_scope": "observed_tool_cost",
                   "resource_mode": self.limits.resource_mode, "child_cpu_observation_source": "wait4_rusage",
                   "peak_memory_bytes": None, "known_peak_memory_bytes": None,
                   "memory_accounting_complete": True, "params_sha256": None}
        with self._lock:
            self._call_count += 1
            self._metrics = metrics
            try:
                args = {} if arguments is None else arguments
                encoding_error = None
                try:
                    metrics["params_sha256"] = _hash(_json_bytes(args))
                except ToolError as exc:
                    encoding_error = exc
                if self.accounting:
                    try:
                        self.accounting(dict(metrics, type="tool_started"))
                    except Exception as exc:
                        raise ToolError("accounting_failed", "tool start could not be accounted") from exc
                if encoding_error:
                    raise encoding_error
                if type(name) is not str:
                    raise ToolError("unknown_tool", "tool name must be a string")
                if cancel_event is not None and cancel_event.is_set():
                    raise ToolError("cancelled", "tool call cancelled before dispatch")
                if name not in TOOL_NAMES:
                    raise ToolError("unknown_tool", "unknown common tool")
                result = self._dispatch(name, args, cancel_event)
                reply = {"ok": True, "result": result}
            except ToolError as exc:
                reply = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
            except Exception:
                # Do not expose host paths, environment, or exception reprs.
                reply = {"ok": False, "error": {"code": "host_error", "message": "tool host rejected or could not complete the call"}}
            finally:
                metrics["wall_seconds"] = time.monotonic() - started
                metrics["parent_cpu_seconds"] = time.thread_time() - parent_cpu
                metrics["cpu_seconds"] = (metrics["parent_cpu_seconds"] + metrics["child_cpu_seconds"]
                                          if metrics["child_cpu_seconds"] is not None else None)
                metrics["known_cpu_seconds"] = metrics["parent_cpu_seconds"] + metrics["known_child_cpu_seconds"]
                metrics["finished_at"] = _utc()
            metrics["result_sha256"] = _hash(_json_bytes(reply))
            reply["metrics"] = dict(metrics)
            if self.accounting:
                try:
                    self.accounting(dict(metrics, type="tool_finished", ok=reply["ok"],
                                         error_code=reply.get("error", {}).get("code")))
                except Exception:
                    reply = {"ok": False, "error": {"code": "accounting_failed", "message": "tool completion could not be accounted"},
                             "metrics": dict(metrics)}
            self._metrics = None
            return reply

    def _dispatch(self, name, args, cancel):
        if name == "materials.list":
            _arguments(args)
            return {"materials": self.material_inventory()}
        if name == "materials.read":
            _arguments(args, ("id",), ("offset", "length"))
            if type(args["id"]) is not str:
                raise ToolError("invalid_arguments", "material ID must be a string")
            return dict(_window(self._material(args["id"]), args, self.limits), id=args["id"])
        if name == "workspace.read":
            _arguments(args, ("path",), ("offset", "length"))
            self._totals()
            return dict(_window(self.workspace.read(args["path"]), args, self.limits), path=args["path"])
        if name == "workspace.write":
            _arguments(args, ("path",), ("text", "base64"))
            parts = _parts(args["path"])
            if ("text" in args) == ("base64" in args):
                raise ToolError("invalid_arguments", "write requires exactly one of text or base64")
            try:
                if "text" in args:
                    if type(args["text"]) is not str:
                        raise ValueError()
                    data = args["text"].encode("utf-8")
                else:
                    if type(args["base64"]) is not str:
                        raise ValueError()
                    data = base64.b64decode(args["base64"], validate=True)
            except (ValueError, UnicodeError, binascii.Error) as exc:
                raise ToolError("invalid_arguments", "invalid text or base64 payload") from exc
            if len(data) > self.limits.max_file_bytes:
                raise ToolError("file_limit", "write exceeds per-file size limit")
            entries, total, count = self._totals()
            sizes = dict(entries)
            old = sizes.get(args["path"], 0)
            if total - old + len(data) > self.limits.artifact_bytes:
                raise ToolError("artifact_limit", "write exceeds persistent artifact quota")
            # Conservative directory reservation avoids inode exhaustion.
            if count + len(parts) > self.limits.max_files and args["path"] not in sizes:
                raise ToolError("artifact_limit", "write exceeds artifact entry limit")
            self.workspace.write(args["path"], data)
            return {"path": args["path"], "size": len(data), "sha256": _hash(data),
                    "artifact_bytes": total - old + len(data)}
        if name == "compute":
            _arguments(args, ("code",))
            if type(args["code"]) is not str or len(args["code"].encode("utf-8")) > self.limits.max_code_bytes:
                raise ToolError("code_limit", "submitted Python exceeds the code limit")
            if self._sandbox is None:
                raise ToolError("unsupported_sandbox", "no supported OS sandbox is available")
            self._totals()
            if (self._probe_result is None or
                    self._probe_result.get("resource_mode", self.limits.resource_mode) != self.limits.resource_mode):
                self._probe(cancel)
            if not self._probe_result["ok"]:
                raise ToolError("unsupported_sandbox", "OS containment probe failed; compute is disabled")
            return self._compute(args["code"], cancel)
        if name in ("check", "submit"):
            _arguments(args, ("candidate",))
            data = _json_bytes(args["candidate"])
            if len(data) > self.limits.max_file_bytes:
                raise ToolError("file_limit", "candidate exceeds per-file limit")
            self._frozen_checker()
            if name == "check":
                started = time.thread_time()
                try:
                    checked = dict(self._audit.validate_witness(json.loads(data)), legal=True)
                except ValueError as exc:
                    checked = {"legal": False, "H": None, "D": self._audit.D, "target_met": False, "error": str(exc)}
                return dict(checked, checker_sha256=self.checker_sha256,
                            candidate_sha256=_hash(data), checker_cpu_seconds=time.thread_time() - started)
            return self._submit(data, cancel)
        raise ToolError("unknown_tool", "unknown common tool")

    def _submit(self, data, cancel):
        if self.limits.max_file_bytes < 4096:
            raise ToolError("file_limit", "per-file limit cannot accommodate a submission receipt reservation")
        digest = _hash(data)
        candidate_name = digest + ".json"
        _, total, count = self._totals()
        submission_entries, _, _ = self.submissions.inventory()
        existing = candidate_name in dict(submission_entries)
        if total + (0 if existing else len(data)) + 4096 > self.limits.artifact_bytes or count + 2 > self.limits.max_files:
            raise ToolError("artifact_limit", "submission exceeds artifact quota including receipt reservation")
        if existing:
            if self.submissions.read(candidate_name) != data:
                raise ToolError("immutable", "stored candidate hash no longer matches")
        else:
            self.submissions.write(candidate_name, data, exclusive=True)
        submitted_at = _utc()
        submitted_monotonic = time.monotonic()
        receipt_id = uuid.uuid4().hex
        # Fresh interpreter and frozen checker copy, independent of any prior
        # check/model result and inaccessible through the workspace interface.
        with tempfile.TemporaryDirectory(prefix="check-", dir=self.control.path) as folder:
            folder = Path(folder)
            checker = folder / "audit.py"
            checker.write_bytes(self._checker_bytes)
            witness = folder / "candidate.json"
            witness.write_bytes(data)
            run = self._run_process([str(self._python), "-I", "-S", "-B", str(checker),
                                     "check-witness", str(witness)], folder, cancel)
        if run["status"] == "completed" and run["returncode"] in (0, 2):
            try:
                result = json.loads(run["stdout"])
                independent = dict(result, legal=True) if run["returncode"] == 0 else {
                    "legal": False, "H": None, "D": self._audit.D, "target_met": False,
                    "error": result["error"]}
            except (ValueError, KeyError, TypeError) as exc:
                raise ToolError("checker_failure", "independent checker emitted invalid output") from exc
        else:
            independent = {"legal": None, "H": None, "target_met": False,
                           "error": "independent checker " + run["status"]}
        receipt = {"receipt_id": receipt_id, "run_id": self.run_id,
                   "candidate_sha256": digest, "checker_sha256": self.checker_sha256,
                   "submitted_at": submitted_at, "submitted_monotonic": submitted_monotonic,
                   "checked_at": _utc(), "independent_check": independent,
                   "checker_cpu_seconds": run["cpu_seconds"], "checker_wall_seconds": run["wall_seconds"],
                   "status": run["status"], "verification_basis": "fresh_process_exact_audit_rerun"}
        encoded = _json_bytes(receipt)
        if len(encoded) > 4096:
            raise ToolError("artifact_limit", "receipt exceeds reserved size")
        self.submissions.write("receipt-" + receipt_id + ".json", encoded, exclusive=True)
        return receipt

    def _runtime_paths(self):
        paths = {Path(sysconfig.get_path("stdlib")).resolve(), self._python}
        # Only the selected interpreter/stdlib and public OS libraries. No
        # home directory, entire /usr, /opt, or host /tmp mounts. Python package
        # directories nested under library roots are masked by the sandbox.
        if sys.platform == "darwin":
            paths.add(Path(sys.base_prefix).resolve())
            paths.update(Path(p) for p in ("/System/Library", "/usr/lib",
                                           "/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld"))
        else:
            paths.update(Path(p).resolve() for p in ("/lib", "/lib64") if Path(p).exists())
            library = Path(sysconfig.get_config_var("LIBDIR") or "/nonexistent") / (sysconfig.get_config_var("LDLIBRARY") or "missing")
            if library.is_file():
                paths.add(library.resolve())
        return sorted(paths, key=str)

    def _sandbox_command(self, folder):
        child = folder / "compute_child.py"
        config = folder / "config.json"
        if self._sandbox == "seatbelt":
            literal = lambda value: json.dumps(str(value))
            read_rules = "\n".join(f"(allow file-read* (subpath {literal(path)}))" if path.is_dir()
                                   else f"(allow file-read* (literal {literal(path)}))"
                                   for path in [*self._runtime_paths(), folder])
            lockdown = ("(version 1)\n(deny default)\n"
                        "(allow process-info* (target self))\n(allow signal (target self))\n"
                        "(allow file-read* (literal \"/dev/null\") (literal \"/dev/urandom\") (literal \"/dev/random\"))\n"
                        "(deny network*)\n(deny process-fork)\n(deny process-exec)\n"
                        + read_rules + "\n(deny file-read* (regex #\"/site-packages(/|$)\"))\n")
            (folder / "lockdown.sb").write_text(lockdown, encoding="utf-8")
            # macOS rejects reapplying sandbox_init inside sandbox-exec. Start
            # only the frozen trusted bootstrap (-I -S, clean environment), and
            # install Seatbelt once before it even reads the submitted code.
            return [str(self._python), "-I", "-S", "-B", str(child), "seatbelt", str(config)]
        command = [shutil.which("bwrap"), "--die-with-parent", "--unshare-all",
                   "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", "/nonexistent",
                   "--setenv", "HOME", "/nonexistent", "--setenv", "LC_ALL", "C",
                   "--ro-bind", str(folder), str(folder)]
        for path in self._runtime_paths():
            command.extend(["--ro-bind", str(path), str(path)])
        package_dirs = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
        for root in (Path(sysconfig.get_path("stdlib")).resolve(), Path("/usr/lib")):
            if root.is_dir():
                for directory in root.glob("python*"):
                    package_dirs.update((directory / "site-packages", directory / "dist-packages"))
                package_dirs.update((root / "site-packages", root / "dist-packages"))
        for directory in sorted(package_dirs, key=str):
            if directory.is_dir():
                command.extend(["--tmpfs", str(directory), "--remount-ro", str(directory)])
        # Preserve loader aliases only where they are OS symlinks.
        for alias in ("/lib", "/lib64"):
            if Path(alias).is_symlink():
                command.extend(["--symlink", str(Path(alias).resolve()), alias])
        command.extend(["--dev", "/dev", "--remount-ro", "/dev", "--remount-ro", "/",
                        "--chdir", str(folder / "workspace"),
                        "--", str(self._python), "-I", "-S", "-B", str(child), "bubblewrap", str(config)])
        return command

    def _compute(self, code, cancel, probe=False):
        if _hash(_read_absolute(CHILD_PATH, 1024 * 1024)) != self._child_sha256:
            raise ToolError("bootstrap_hash_drift", "compute bootstrap changed after freezing")
        with tempfile.TemporaryDirectory(prefix="compute-", dir=self.control.path) as temporary:
            folder = Path(temporary)
            workspace = folder / "workspace"
            workspace.mkdir()
            materials = folder / "materials"
            materials.mkdir()
            if not probe:
                entries, _, _ = self._totals()
                for relative, _ in entries:
                    target = workspace.joinpath(*_parts(relative))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(self.workspace.read(relative))
            material_paths = {}
            for index, name in enumerate(sorted(self._materials)):
                target = materials / str(index)
                target.write_bytes(self._material(name))
                material_paths[name] = str(target)
            (folder / "compute_child.py").write_bytes(self._child_bytes)
            (folder / "code.py").write_text(code, encoding="utf-8")
            config = {"backend": self._sandbox, "resource_mode": self.limits.resource_mode,
                      "code_path": str(folder / "code.py"),
                      "lockdown_profile": str(folder / "lockdown.sb"),
                      "workspace": str(workspace), "materials": material_paths,
                      "memory_bytes": self.limits.memory_bytes, "cpu_seconds": self.limits.cpu_seconds,
                      "cpu_cores": self.limits.cpu_cores}
            (folder / "config.json").write_bytes(_json_bytes(config))
            command = self._sandbox_command(folder)
            result = self._run_process(command, workspace, cancel)
            result["sandbox_backend"] = self._sandbox
            result["runtime"] = dict(self._runtime)
            result["resource_policy"] = self._resource_policy()
            return result

    def _probe(self, cancel):
        # A harmless private sentinel tests real filesystem denial, not only
        # whether sandbox-exec/bwrap is installed. No user code runs on failure.
        sentinel_name = "probe-" + uuid.uuid4().hex
        self.control.write(sentinel_name, b"private-probe", exclusive=True)
        sentinel = self.control.path / sentinel_name
        try:
            # A real loopback listener distinguishes OS permission denial from
            # an absent service, DNS failure, or a disconnected network.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                address = listener.getsockname()
                code = ("import os, socket, subprocess, sys, json, errno\n"
                        "results = {}\n"
                        "def denied(name, action):\n"
                        "    try:\n"
                        "        action()\n"
                        "    except OSError as error:\n"
                        "        results[name] = error.errno in (errno.EPERM, errno.EACCES)\n"
                        "    else:\n"
                        "        results[name] = False\n"
                        "def connect():\n"
                        f"    with socket.create_connection({address!r}, timeout=0.3): pass\n"
                        "def bind():\n"
                        "    with socket.socket() as connection: connection.bind(('127.0.0.1', 0))\n"
                        f"denied('private_read', lambda: open({str(sentinel)!r}, 'rb').read())\n"
                        "denied('workspace_write', lambda: open(os.path.join(WORKSPACE, 'probe'), 'w'))\n"
                        "denied('network', connect)\n"
                        "denied('network_bind', bind)\n"
                        "denied('process', lambda: subprocess.run([sys.executable, '-I', '-S', '-c', 'pass']))\n"
                        "denied('exec', lambda: os.execv(sys.executable, [sys.executable, '-I', '-S', '-c', 'print(123)']))\n"
                        "print(json.dumps(results, sort_keys=True))\n")
                result = self._compute(code, cancel, probe=True)
            try:
                checks = json.loads(result["stdout"])
            except (ValueError, TypeError):
                checks = {}
            ok = result["status"] == "completed" and result["returncode"] == 0 and checks == {
                "private_read": True, "workspace_write": True, "network": True,
                "network_bind": True, "process": True, "exec": True}
            self._probe_result = {"ok": ok, "checks": checks, "status": result["status"],
                                  "resource_mode": self.limits.resource_mode,
                                  "sandbox_backend": self._sandbox,
                                  "sandbox_initialization": ("trusted_bootstrap_before_user_code" if self._sandbox == "seatbelt"
                                                             else "bubblewrap_and_seccomp_before_user_code"),
                                  "network_probe": "permission_errno_on_loopback_connect_and_bind",
                                  "returncode": result["returncode"], "stderr": result["stderr"][:2048]}
        finally:
            # Exact host-created sentinel in the verified control directory.
            fd = self.control.root()
            try:
                os.unlink(sentinel_name, dir_fd=fd)
            finally:
                os.close(fd)

    def _record_child_usage(self, child_cpu, peak):
        if self._metrics is None:
            return
        if child_cpu is None:
            self._metrics["child_cpu_seconds"] = None
            self._metrics["cpu_accounting_complete"] = False
        else:
            self._metrics["known_child_cpu_seconds"] += child_cpu
            if self._metrics["child_cpu_seconds"] is not None:
                self._metrics["child_cpu_seconds"] += child_cpu
        if peak is None:
            self._metrics["peak_memory_bytes"] = None
            self._metrics["memory_accounting_complete"] = False
        else:
            current = self._metrics["known_peak_memory_bytes"]
            known = peak if current is None else max(current, peak)
            self._metrics["known_peak_memory_bytes"] = known
            if self._metrics["memory_accounting_complete"]:
                self._metrics["peak_memory_bytes"] = known

    def _run_process(self, command, cwd, cancel):
        start = time.monotonic()
        environment = {"PATH": "/nonexistent", "HOME": "/nonexistent", "LC_ALL": "C",
                       "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        usage, status, returncode = None, "completed", None
        total_output, peak, killed = 0, None, False
        rss_samples, rss_missing, usage_recorded = 0, 0, False

        def kill():
            nonlocal killed
            if not killed:
                killed = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        try:
            while usage is None or selector.get_map():
                if usage is None:
                    if cancel is not None and cancel.is_set() and status == "completed":
                        status = "cancelled"
                        kill()
                    if time.monotonic() - start >= self.limits.timeout_seconds and status == "completed":
                        status = "timeout"
                        kill()
                    resident = _observed_number(_rss(process.pid), positive=True)
                    if resident is not None:
                        rss_samples += 1
                        peak = resident if peak is None else max(peak, resident)
                        if resident > self.limits.memory_bytes and status == "completed":
                            status = "memory_limit"
                            kill()
                    else:
                        rss_missing += 1
                    waited, wait_status, observation = os.wait4(process.pid, os.WNOHANG)
                    if waited:
                        usage = observation
                        returncode = os.waitstatus_to_exitcode(wait_status)
                        process.returncode = returncode
                        kill()  # terminate any same-group descendants before draining pipes
                for key, _ in selector.select(0.01):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    else:
                        available = max(0, self.limits.max_output_bytes - total_output)
                        streams[key.data].extend(chunk[:available])
                        total_output += len(chunk)
                        if total_output > self.limits.max_output_bytes and status == "completed":
                            status = "output_limit"
                            kill()
            child_cpu, measured_peak = _wait_usage(usage)
            sampled_peak = peak
            if measured_peak is not None:
                peak = measured_peak if peak is None else max(peak, measured_peak)
            self._record_child_usage(child_cpu, peak)
            usage_recorded = True
            if returncode in (-signal.SIGXCPU, -signal.SIGKILL) and status == "completed":
                status = ("cpu_limit" if returncode == -signal.SIGXCPU or
                          child_cpu is not None and child_cpu >= self.limits.cpu_seconds else "killed")
            return {"status": status, "returncode": returncode,
                    "stdout": streams["stdout"].decode("utf-8", errors="replace"),
                    "stderr": streams["stderr"].decode("utf-8", errors="replace"),
                    "output_bytes": total_output, "output_truncated": total_output > self.limits.max_output_bytes,
                    "cpu_seconds": child_cpu, "wall_seconds": time.monotonic() - start,
                    "cpu_accounting_complete": child_cpu is not None,
                    "cpu_observation_source": "wait4_rusage" if child_cpu is not None else None,
                    "peak_memory_bytes": peak,
                    "peak_memory_observation_sources": (["kernel_rss_samples"] if sampled_peak is not None else []) +
                                                       (["wait4_ru_maxrss"] if measured_peak is not None else []),
                    "rss_watchdog": {"samples": rss_samples, "unavailable_samples": rss_missing,
                                     "peak_sampled_bytes": sampled_peak, "sampling_interval_seconds": 0.01,
                                     "hard_limit": False}}
        finally:
            kill()
            if usage is None:
                _, wait_status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(wait_status)
            if not usage_recorded:
                child_cpu, measured_peak = _wait_usage(usage)
                if measured_peak is not None:
                    peak = measured_peak if peak is None else max(peak, measured_peak)
                self._record_child_usage(child_cpu, peak)
            selector.close()
            process.stdout.close()
            process.stderr.close()

"""Host-bound materials, workspace files and no-network sandboxed Python."""

import hashlib
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import threading

from research_store import read_regular, safe_path
from schema import require


def _relative(value):
    require(type(value) is str and value and "\\" not in value and not value.startswith("/")
            and all(part not in ("", ".", "..") for part in value.split("/")), "UNSAFE_PATH", "Expected a canonical relative path")
    require(not any(part.lower().startswith(".env") or part.lower() in ("auth.json", "oauth.json", "credentials.json", "id_rsa")
                    or any(word in part.lower() for word in ("credential", "private_key")) for part in value.split("/")),
            "SENSITIVE_CONTENT", "Sensitive file names are not available to model tools")
    return value


def workspace_size(root):
    root = Path(root)
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        require(not path.is_symlink(), "UNSAFE_PATH", "Workspace links are forbidden")
        if path.is_file():
            total += path.stat().st_size
    return total


def material_read(manifest, name):
    if name == "":
        return {"materials": [{"name": item["name"], "bytes": item["bytes"], "sha256": item["sha256"]} for item in manifest["materials"]]}
    matches = [item for item in manifest["materials"] if item["name"] == name]
    available = ", ".join(item["name"] for item in manifest["materials"])
    require(len(matches) == 1, "NOT_FOUND", f"Unknown material {name!r}. Available materials: {available}. Use material_read with an empty name to list them.")
    descriptor = matches[0]
    path = Path(descriptor["path"]).absolute()
    require(all(not p.is_symlink() for p in [path, *path.parents]), "UNSAFE_PATH", "Material symlink")
    raw = read_regular(path)
    require(len(raw) == descriptor["bytes"] and len(raw) <= 1024 * 1024
            and hashlib.sha256(raw).hexdigest() == descriptor["sha256"], "SOURCE_CHANGED", "Frozen material changed")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("Material is not UTF-8 text") from error
    return {"name": name, "sha256": descriptor["sha256"], "text": text}


def file_read(manifest, path):
    root = Path(manifest["workspace"])
    raw = read_regular(safe_path(root, _relative(path)))
    require(len(raw) <= 1024 * 1024, "UNSUPPORTED", "Workspace read exceeds 1 MiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("Workspace file is not UTF-8 text") from error
    return {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "text": text}


def file_write(manifest, path, text):
    require(type(text) is str and len(text.encode("utf-8")) <= 1024 * 1024, "UNSUPPORTED", "Workspace write is limited to 1 MiB UTF-8 text")
    root = Path(manifest["workspace"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = safe_path(root, _relative(path))
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not target.exists() or target.is_file(), "UNSAFE_PATH", "Workspace target is not a regular file")
    raw = text.encode("utf-8")
    previous = target.stat().st_size if target.exists() else 0
    projected = workspace_size(root) - previous + len(raw)
    require(projected <= manifest["limits"]["artifact_bytes"], "ARTIFACT_LIMIT", "Workspace artifact budget exceeded")
    temporary = target.with_name(".tmp-" + str(os.getpid()))
    require(not temporary.exists(), "WRITE_FAILED", "Temporary workspace file exists")
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "artifact_bytes": workspace_size(root)}


def _sandbox_profile(workspace):
    workspace = workspace.replace("\\", "\\\\").replace('"', '\\"')
    return "\n".join([
        "(version 1)", "(deny default)", f'(allow process-exec (subpath "{Path(sys.executable).resolve().parents[4]}"))', "(allow mach*)", "(allow sysctl*)", "(allow file*)",
        "(deny network*)", '(deny file-read-data (subpath "/Users") (subpath "/private/tmp") (subpath "/private/var/folders") (subpath "/Volumes") (subpath "/private/etc"))', f'(allow file-read-data (subpath "{workspace}"))',
        "(deny file-write*)", f'(allow file-write* (subpath "{workspace}") (subpath "/dev"))',
    ])


def python_run(manifest, code):
    require(type(code) is str and code and len(code.encode("utf-8")) <= 256 * 1024, "UNSUPPORTED", "Python code must be nonempty UTF-8 text up to 256 KiB")
    require(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "UNSUPPORTED", "No verified no-network sandbox is available")
    root = Path(manifest["workspace"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root / ".tmp"
    temporary.mkdir(exist_ok=True, mode=0o700)
    limits = manifest["limits"]
    def constrain():
        def cap(kind, requested):
            soft, hard = resource.getrlimit(kind)
            effective = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
            resource.setrlimit(kind, (effective, hard))
        cap(resource.RLIMIT_CPU, min(limits["tool_timeout_seconds"], limits["cpu_seconds"] or limits["tool_timeout_seconds"]))
        cap(resource.RLIMIT_FSIZE, limits["artifact_bytes"])
        cap(resource.RLIMIT_NPROC, 64)
    environment = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(root), "TMPDIR": str(temporary),
                   "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C"}
    executable = Path(sys.executable).resolve()
    app = executable.parents[1] / "Resources/Python.app/Contents/MacOS/Python"
    command = ["/usr/bin/sandbox-exec", "-p", _sandbox_profile(str(root)), str(app if app.is_file() else executable), "-I", "-B", "-c", code]
    import selectors
    import time
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=constrain)
    status = "completed"
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + limits["tool_timeout_seconds"]
    parent_pid = os.getpid()
    next_resource_check = 0.0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            current = time.monotonic()
            if status == "completed" and current >= deadline:
                status = "timeout"
                process.kill()
            if current >= next_resource_check and process.poll() is None:
                next_resource_check = current + 0.1
                inspected = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True)
                try:
                    rss = int(inspected.stdout.strip()) * 1024
                except ValueError:
                    rss = 0
                if os.getppid() != parent_pid and os.getppid() == 1:
                    status = "parent_lost"
                    process.kill()
                elif rss > limits["memory_bytes"]:
                    status = "memory_limit"
                    process.kill()
                elif workspace_size(root) > limits["artifact_bytes"]:
                    status = "artifact_limit"
                    process.kill()
            for key, _ in selector.select(0.05):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                destination = buffers[key.data]
                remaining = 65536 - len(destination)
                destination.extend(chunk[:remaining])
                if len(chunk) > remaining and process.poll() is None:
                    status = "output_limit"
                    process.kill()
    process.wait()
    process.stdout.close()
    process.stderr.close()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"status": status, "exit_code": process.returncode,
            "stdout": buffers["stdout"].decode("utf-8", errors="backslashreplace"),
            "stderr": buffers["stderr"].decode("utf-8", errors="backslashreplace"), "artifact_bytes": workspace_size(root),
            "cpu_seconds": max(0.0, (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)),
            "memory_enforcement": "host_rss_poll_soft_limit"}

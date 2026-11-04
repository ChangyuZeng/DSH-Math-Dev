"""Native C/X/R preparation and fail-closed, localhost-only qualification.

No model loop lives here. Codex owns C's turns; Xean's pinned Pi workflow owns
X; upstream Rethlas owns generation/strategy/verification. This module has no
live execution API. A configuration is not a qualification result.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

MODEL = {"provider": "openai-codex", "model": "gpt-6-astra", "effort": "max"}
MAX_OUTPUT = 16384
CONTEXT = 65536
TOOLS = ("materials_list", "materials_read", "workspace_read", "workspace_write",
         "compute", "check", "submit")
PINS = {
    "C": {"repository": "https://github.com/openai/codex", "commit": "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856",
          "tag": "rust-v0.166.0", "tag_object": "593389380740f7b4762771573c447ccfdc59742f", "version": "0.166.0"},
    "X": {"repository": "https://github.com/chaoxu/xean", "commit": "26669ebb071f8fc13e8a51f66a474d7ce9af387a", "version": "4.5.0"},
    "R": {"repository": "https://github.com/frenzymath/Rethlas", "commit": "887cc46427636bbdd235160a112f9a30ae81d040"},
}
ROLES = {"C": ("agent", "compaction"), "X": ("coordinator", "explorer", "verifier", "compaction"),
         "R": ("generator", "strategy", "verifier", "repair", "compaction")}
DEFAULT_NODE = Path("/Users/zcy/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node")
DEFAULT_CODEX = Path("/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js")


class NativeBlocked(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise NativeBlocked(code, message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def localhost_url(url, *, arm=None, role=None):
    """Only numeric loopback, with an explicit port and no userinfo/redirects."""
    parsed = urlsplit(url)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port
    except ValueError:
        loopback, port = False, None
    require(parsed.scheme == "http" and loopback and port and not parsed.username and
            not parsed.password and not parsed.query and not parsed.fragment,
            "GATEWAY_REQUIRED", "An explicit numeric-loopback HTTP gateway is mandatory")
    if arm is not None:
        require(arm in ROLES and role in ROLES[arm] and parsed.path == f"/{arm}/{role}/v1",
                "ROLE_ROUTE", "Gateway route must bind the native arm and role")
    return url.rstrip("/")


def _new_file(path, content, *, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
    return path


def isolated_environment(root, *, token, path_entries=()):
    """Construct an allowlist; never inherit HOME, credentials, proxies, or loaders."""
    root = Path(root).resolve()
    require(root.is_dir() and not root.is_symlink(), "ISOLATION", "Isolation root must exist")
    require(isinstance(token, str) and token and "\n" not in token, "GATEWAY_TOKEN", "Missing ephemeral gateway token")
    directories = {"HOME": root / "home", "CODEX_HOME": root / "codex", "XDG_CONFIG_HOME": root / "config",
                   "XDG_CACHE_HOME": root / "cache", "XDG_DATA_HOME": root / "data", "TMPDIR": root / "tmp"}
    for directory in directories.values():
        directory.mkdir(mode=0o700, exist_ok=False)
    return {**{key: str(value) for key, value in directories.items()},
            "PATH": os.pathsep.join([*(str(Path(p).resolve()) for p in path_entries), "/usr/bin", "/bin"]),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb", "NO_COLOR": "1",
            # This pinned reqwest still proxies numeric IPs with NO_PROXY="*".
            # Exact loopback entries bypass macOS system proxy discovery; the OS
            # sandbox independently denies every port except the bound gateway.
            "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
            "FOUR_HARNESS_TOKEN": token, "FOUR_HARNESS_OFFLINE_FIXTURE": "1",
            "CFFIXED_USER_HOME": str(directories["HOME"]),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def codex_config(url, *, arm="C", role="agent", mcp_command=(), tools_url=None, direct_catalog=None):
    """Pin routing/effort and request tool restrictions; qualify the actual wire."""
    url = localhost_url(url, arm=arm, role=role)
    lines = [f'model = {json.dumps(MODEL["model"])}', 'model_provider = "four_harness"',
             'model_reasoning_effort = "max"', f"model_context_window = {CONTEXT}",
             f"model_auto_compact_token_limit = {CONTEXT - MAX_OUTPUT}",
             'approval_policy = "never"', 'sandbox_mode = "read-only"', 'web_search = "disabled"',
             'cli_auth_credentials_store = "ephemeral"', 'mcp_oauth_credentials_store = "file"',
             'check_for_update_on_startup = false', 'project_doc_max_bytes = 0',
             '[analytics]', 'enabled = false', '[feedback]', 'enabled = false',
             '[features]', 'shell_tool = false', 'unified_exec = false', 'apply_patch_freeform = false',
             'unbounded_connection_retries = false', 'respect_system_proxy = false', 'system_proxy_fallback = false',
             'multi_agent = false', 'multi_agent_v2 = false', 'collab = false', 'code_mode = false',
             'apps = false', 'plugins = false', 'web_search = false', 'web_search_request = false',
             'standalone_web_search = false',
             '[model_providers.four_harness]', 'name = "four-harness mandatory gateway"',
             f"base_url = {json.dumps(url)}", 'wire_api = "responses"',
             'env_key = "FOUR_HARNESS_TOKEN"', 'requires_openai_auth = false',
             'request_max_retries = 0', 'stream_max_retries = 0', 'supports_websockets = false']
    if mcp_command:
        require(tools_url is not None, "TOOLS_ROUTE", "MCP requires an explicit gateway tools route")
        localhost_url(tools_url)
        require(urlsplit(tools_url).netloc == urlsplit(url).netloc and
                urlsplit(tools_url).path == f"/{arm}/{role}/tools", "TOOLS_ROUTE", "MCP must share the role gateway")
        require(Path(mcp_command[0]).is_absolute(), "MCP_COMMAND", "MCP executable must be absolute")
        lines += ['[mcp_servers.common]', f"command = {json.dumps(str(mcp_command[0]))}",
                  f"args = {json.dumps([str(a) for a in mcp_command[1:]] + ['--url', tools_url, '--token-env', 'FOUR_HARNESS_TOKEN'])}",
                  'env_vars = ["FOUR_HARNESS_TOKEN"]', 'required = true',
                  f"enabled_tools = {json.dumps(TOOLS)}"]
    config = "\n".join(lines) + "\n"
    if direct_catalog is not None:
        from codex_bridge import direct_config
        config = direct_config(config, catalog=direct_catalog, arm=arm)
    return config


def rethlas_config(gateway_url, role_tokens):
    """Freeze native root and recursive providers without disabling R's strategy.

    The copied MCP shim meters the original tools and binds verifier subprocess
    parents. Native Codex tool execution/spawn still needs separate qualification.
    """
    base = localhost_url(gateway_url)
    require(urlsplit(base).path in {"", "/"}, "ROLE_ROUTE", "R providers require the gateway origin")
    require(set(role_tokens) == set(ROLES["R"]), "ROLE_ROUTE", "Every R descendant role needs a gateway capability")
    lines = ['model = "gpt-6-astra"', 'model_provider = "four_harness_generator"',
             'model_reasoning_effort = "max"', f'model_context_window = {CONTEXT}',
             f'model_auto_compact_token_limit = {CONTEXT - MAX_OUTPUT}',
             'approval_policy = "never"', 'sandbox_mode = "read-only"', 'web_search = "disabled"',
             'cli_auth_credentials_store = "ephemeral"', 'mcp_oauth_credentials_store = "file"',
             'check_for_update_on_startup = false', '[analytics]', 'enabled = false', '[feedback]', 'enabled = false',
             '[features]', 'shell_tool = false', 'unified_exec = false', 'apply_patch_freeform = false',
             'unbounded_connection_retries = false', 'respect_system_proxy = false', 'system_proxy_fallback = false',
             'multi_agent = true', 'apps = false', 'plugins = false']
    for role in ROLES["R"]:
        url = localhost_url(base.rstrip("/") + f"/R/{role}/v1", arm="R", role=role)
        lines += [f'[model_providers.four_harness_{role}]', f'name = "four-harness R {role}"',
                  f'base_url = {json.dumps(url)}', 'wire_api = "responses"',
                  f'env_key = "FOUR_HARNESS_{role.upper()}_TOKEN"', 'requires_openai_auth = false',
                  'request_max_retries = 0', 'stream_max_retries = 0', 'supports_websockets = false']
        if role == "verifier":
            lines += ['env_http_headers = { "X-Four-Parent-Request-Id" = "FOUR_HARNESS_PARENT_REQUEST_ID" }']
    return "\n".join(lines) + "\n"


def source_identity(root, arm):
    root = Path(root).resolve()
    require(arm in PINS, "ARM", "Unknown native arm")
    result = subprocess.run(["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    require(result.returncode == 0 and result.stdout.strip() == PINS[arm]["commit"],
            "SOURCE_PIN", f"{arm} requires the frozen upstream source commit")
    dirty = subprocess.run(["/usr/bin/git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True)
    require(dirty.returncode == 0 and not dirty.stdout.strip(), "SOURCE_DIRTY", "Pinned original source must be clean")
    return {**PINS[arm], "path": str(root), "dirty": False}


def macos_fixture_profile(root, *, readable=(), writable=(), gateway_url, root_writable=True):
    """Deny credential reads, external networking and inherited host IPC.

    Only the supplied runtime/library paths, system runtime files and this fresh
    fixture directory are readable. The sandbox is inherited by every child.
    """
    parsed = urlsplit(localhost_url(gateway_url))
    root = Path(root).resolve()
    quote = lambda value: json.dumps(str(value))
    paths = [root, *[Path(p).resolve() for p in readable], Path("/System"), Path("/usr/lib"),
             Path("/usr/share"), Path("/dev"), Path("/private/var/db/dyld")]
    reads = " ".join(f"(subpath {quote(p)})" for p in paths)
    writes = " ".join(f"(subpath {quote(Path(p).resolve())})" for p in ((*([root] if root_writable else []), *writable)))
    return ("(version 1)\n(deny default)\n(allow process*)\n(allow sysctl-read)\n"
            "(allow file-read-metadata)\n"
            '(allow mach-lookup (global-name "com.apple.cfprefsd.agent") (global-name "com.apple.cfprefsd.daemon"))\n'
            "(deny mach-lookup (global-name \"com.apple.securityd\") "
            "(global-name \"com.apple.security.agent\"))\n"
            # The forced-value query initializes both managed-source generation
            # counters; denying either makes the subsequent native sync fail.
            f'(allow ipc-posix-shm-read-data (ipc-posix-name "apple.cfprefs.{os.getuid()}v1") '
            '(ipc-posix-name "apple.cfprefs.daemonv1"))\n'
            f"(allow file-map-executable {reads})\n"
            f"(allow file-read* {reads} (literal \"/\") (literal \"/private/etc/localtime\") "
            "(literal \"/private/etc/hosts\") (literal \"/private/etc/resolv.conf\"))\n"
            f"(allow file-write* {writes} (literal \"/dev/null\"))\n"
            f'(allow network-outbound (remote tcp "localhost:{parsed.port}"))\n'
            "(allow signal (target same-sandbox))\n")


def process_identity(pid):
    """Read process metadata only; birth identity prevents signaling a reused PID."""
    if sys.platform == "darwin":
        import ctypes

        class BSDInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint32) for name in
                        ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
            _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
            _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid", "nice")]
            _fields_ += [("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]

        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        library.proc_pidinfo.restype = ctypes.c_int
        record = BSDInfo()
        if library.proc_pidinfo(pid, 3, 0, ctypes.byref(record), ctypes.sizeof(record)) != ctypes.sizeof(record):
            return None
        if record.pid != pid or record.uid != os.getuid() or record.status == 5:  # SZOMB
            return None
        return {"pid": pid, "ppid": record.ppid, "pgid": record.pgid,
                "birth": (record.start_seconds, record.start_microseconds)}
    if sys.platform.startswith("linux"):
        directory = Path("/proc") / str(pid)
        try:
            if directory.stat().st_uid != os.getuid():
                return None
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return {"pid": pid, "ppid": int(fields[1]), "pgid": int(fields[2]), "birth": int(fields[19])}
        except (FileNotFoundError, ProcessLookupError):
            return None
    raise NativeBlocked("PROCESS_IDENTITY", "This OS has no qualified process birth identity reader")


class OwnedProcessTree:
    """Retain observed ancestry across setsid; never infer ownership from a name."""
    def __init__(self, pid):
        self.root = pid
        self.lock = threading.RLock()
        self.known = {}
        self.signaled = set()
        self.errors = []
        root = process_identity(pid)
        if root is not None:
            self.known[pid] = root["birth"]

    def sample(self):
        from observation import process_snapshot
        rows = process_snapshot()
        with self.lock:
            live = {pid: record for pid, birth in self.known.items()
                    if (record := process_identity(pid)) is not None and record["birth"] == birth}
            selected = set(live)
            while True:
                additions = set()
                for row in rows:
                    pid = row["pid"]
                    if pid in selected or row["ppid"] not in selected:
                        continue
                    record = process_identity(pid)
                    if record is not None and record["ppid"] in selected:
                        self.known[pid] = record["birth"]
                        additions.add(pid)
                if not additions:
                    break
                selected.update(additions)
        return rows

    def cleanup(self):
        # Refresh ancestry before any parent is signaled. Known children that
        # created a session remain owned after their original parent exits.
        try:
            self.sample()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self.errors.append(type(error).__name__)
        with self.lock:
            identities = sorted(self.known.items(), key=lambda item: item[0] == self.root)
        for pid, birth in identities:
            record = process_identity(pid)
            if record is None or record["birth"] != birth:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                self.signaled.add(pid)
            except ProcessLookupError:
                pass
            except OSError as error:
                self.errors.append({"pid": pid, "error": type(error).__name__, "errno": error.errno})
        deadline = time.monotonic() + 1
        remaining = []
        while time.monotonic() < deadline:
            remaining = [pid for pid, birth in identities
                         if (record := process_identity(pid)) is not None and record["birth"] == birth]
            if not remaining:
                break
            threading.Event().wait(0.02)
        return {"scope": "observed_descendants_with_verified_birth_identity", "signaled_processes": len(self.signaled),
                "known_processes": len(identities), "remaining_known_pids": remaining, "errors": self.errors,
                "whole_process_tree_complete": False, "unobserved_short_lived_parents_possible": True}


def run_process(command, *, env, cwd, timeout=30, cancel_event=None):
    """One attempt; promptly cancel only identity-verified owned descendants."""
    from observation import ProcessObserver
    process = subprocess.Popen([str(x) for x in command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    owned = OwnedProcessTree(process.pid)
    observer = ProcessObserver(process.pid, sampler=owned.sample).start()
    timed_out = False
    cancelled = False
    deadline = time.monotonic() + timeout
    stdout = stderr = ""
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=max(0.001, min(0.2, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                cancelled = cancel_event is not None and cancel_event.is_set()
                timed_out = not cancelled and time.monotonic() >= deadline
                if not cancelled and not timed_out and process.poll() is None:
                    continue
                cleanup = owned.cleanup()
                try:
                    stdout, stderr = process.communicate(timeout=3)
                except subprocess.TimeoutExpired as error:
                    stdout = error.output.decode(errors="replace") if isinstance(error.output, bytes) else error.output or ""
                    stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr or ""
                    owned.errors.append("OUTPUT_PIPE_REMAINS_OPEN")
                    process.stdout.close()
                    process.stderr.close()
                break
    finally:
        cleanup = owned.cleanup()
        resource_observations = observer.stop()
    require(not cleanup["remaining_known_pids"] and not cleanup["errors"], "PROCESS_CLEANUP",
            "Owned native process cleanup was incomplete: " + json.dumps(cleanup, sort_keys=True))
    return {"returncode": process.poll(), "stdout": stdout, "stderr": stderr, "timed_out": timed_out,
            "cancelled": cancelled, "cleanup": cleanup, "resource_observations": resource_observations,
            "cleanup_complete_for_known_processes": not cleanup["remaining_known_pids"] and not cleanup["errors"]}


def isolation_probe(prefix, *, node, env, work):
    """Check a disposable credential canary and an unapproved localhost port."""
    import socket
    with tempfile.TemporaryDirectory(prefix="four-harness-canary-") as outside:
        canary = Path(outside).resolve() / "credential"
        canary.write_text("fixture-only-secret", encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            probe_js = '''const fs = require("node:fs"), net = require("node:net");
let denied = false; try { fs.readFileSync(process.argv[1]); } catch(e) { denied = e.code === "EPERM" || e.code === "EACCES"; }
if (!denied) process.exit(41);
let socket = net.connect(Number(process.argv[2]), "127.0.0.1");
socket.on("connect", () => process.exit(42));
socket.on("error", e => process.exit(e.code === "EPERM" || e.code === "EACCES" ? 0 : 43));
setTimeout(() => process.exit(44), 2000).unref();'''
            probe = run_process([*prefix, node, "-e", probe_js, canary, str(listener.getsockname()[1])],
                                env=env, cwd=work, timeout=5)
            require(probe["returncode"] == 0, "ISOLATION_PROBE", f"Credential/network denial probe failed: {probe}")


def managed_preferences_probe(prefix, *, python, env, work):
    """Reject forced host policy without reading any managed preference value."""
    script = '''import ctypes, json
cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFPreferencesAppValueIsForced.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
cf.CFPreferencesAppValueIsForced.restype = ctypes.c_bool
cf.CFPreferencesAppSynchronize.argtypes = [ctypes.c_void_p]
cf.CFPreferencesAppSynchronize.restype = ctypes.c_bool
cf.CFRelease.argtypes = [ctypes.c_void_p]
app = cf.CFStringCreateWithCString(None, b"com.openai.codex", 0x08000100)
forced = {}
try:
    for name in ("config_toml_base64", "requirements_toml_base64"):
        key = cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
        try:
            forced[name] = bool(cf.CFPreferencesAppValueIsForced(key, app))
        finally:
            cf.CFRelease(key)
    synchronized = bool(cf.CFPreferencesAppSynchronize(app))
finally:
    cf.CFRelease(app)
print(json.dumps({"forced": forced, "synchronized": synchronized, "preference_values_read": False}))
'''
    probe = run_process([*prefix, python, "-I", "-c", script], env=env, cwd=work, timeout=5)
    require(probe["returncode"] == 0, "MANAGED_PREFERENCES_PROBE", "Cannot verify isolated native preferences")
    result = json.loads(probe["stdout"])
    require(result["synchronized"] is True, "MANAGED_PREFERENCES_PROBE", "Native preference synchronization failed")
    require(not any(result["forced"].values()), "HOST_MANAGED_CONFIG", "Forced host Codex policy must not enter an isolated run")
    return result


def codex_fixture(root, *, gateway, token, mcp_command, prompt="Call materials_list once, then say fixture complete.",
                  node=DEFAULT_NODE, codex=DEFAULT_CODEX, timeout=30, direct_source=None):
    """Run the installed native CLI only behind a local fake gateway and OS jail.

    The caller supplies a fake-only gateway, which must reject non-common tools,
    meter all HTTP paths and close dispatch after its two-request fixture budget.
    This function deliberately cannot enable a real transport.
    """
    from gateway import FixtureTransport, Gateway
    require(type(gateway) is Gateway and type(gateway.transport) is FixtureTransport and gateway.is_offline,
            "FAKE_ONLY", "Native fixture accepts only the immutable fake Responses transport")
    gateway_url = gateway.url + "/C/agent/v1"
    localhost_url(gateway_url, arm="C", role="agent")
    require(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
            "OS_ISOLATION", "Native fixture requires the qualified macOS sandbox")
    root = Path(root).resolve()
    require(not root.exists(), "FRESH_RUN", "Native fixture root must be new")
    root.mkdir(parents=True, mode=0o700)
    node, codex = Path(node).resolve(), Path(codex).resolve()
    require(node.is_file() and codex.is_file(), "CODEX_MISSING", "Pinned native Codex/Node runtime missing")
    package = json.loads((codex.parent.parent / "package.json").read_text())
    require(package.get("name") == "@openai/codex" and package.get("version") == PINS["C"]["version"],
            "BINARY_VERSION", "Installed Codex package differs from pinned version")
    env = isolated_environment(root, token=token, path_entries=[node.parent])
    tools_url = gateway_url.removesuffix("/v1") + "/tools"
    catalog = None
    if direct_source is not None:
        from codex_bridge import prepare_catalog
        catalog = prepare_catalog(direct_source, root / "native-catalog.json")
    _new_file(Path(env["CODEX_HOME"]) / "config.toml", codex_config(gateway_url, mcp_command=mcp_command,
        tools_url=tools_url, direct_catalog=catalog["path"] if catalog else None))
    work = root / "workspace"
    work.mkdir(mode=0o700)
    readable = [node.parent.parent, codex.parent.parent, Path(sys.prefix).resolve(),
                "/opt/homebrew/Cellar", "/usr/bin", "/bin"]
    for executable in mcp_command:
        if str(executable).startswith("/"):
            readable.append(Path(executable).resolve().parent)
    profile = _new_file(root / "fixture.sb", macos_fixture_profile(root, readable=readable, gateway_url=gateway_url))
    prefix = ["/usr/bin/sandbox-exec", "-f", profile]
    check = run_process([*prefix, "/usr/bin/true"], env=env, cwd=work, timeout=5)
    require(check["returncode"] == 0, "OS_ISOLATION", f"Sandbox unavailable: exit={check['returncode']} {check['stderr'].strip()}")
    isolation_probe(prefix, node=node, env=env, work=work)
    preferences = managed_preferences_probe(prefix, python=Path(sys.executable).resolve(), env=env, work=work)
    version = run_process([*prefix, node, codex, "--version"], env=env, cwd=work, timeout=10)
    require(version["returncode"] == 0 and version["stdout"].strip() == "codex-cli " + PINS["C"]["version"],
            "BINARY_VERSION", f"Native version check failed: {version['stdout']}{version['stderr']}")
    command = [*prefix, node, codex, "exec", "--skip-git-repo-check", "--ephemeral", "--json", "-C", work, prompt]
    result = run_process(command, env=env, cwd=work, timeout=timeout)
    binding = gateway.bindings[token]
    rejected = [{key: event[key] for key in ("event_id", "role", "code")}
                for event in gateway.ledger.events()
                if event["type"] == "request_rejected" and event["run_id"] == binding["run_id"]]
    blocker = (rejected[-1]["code"] if rejected else
               "MACOS_MANAGED_PREFERENCES" if result["returncode"] and
               "Failed to synchronize managed preferences" in result["stderr"] else
               "NATIVE_PROCESS_FAILED" if result["returncode"] else None)
    startup = '"type":"thread.started"' in result["stdout"] and '"type":"turn.started"' in result["stdout"]
    return {**result, "blocker": blocker, "native_qualified": not bool(blocker),
             "native_startup_succeeded": startup, "gateway_rejections": rejected, "isolation_probe_passed": True,
             "managed_preferences_probe": preferences,
            "source_pin": PINS["C"], "runtime_version": version["stdout"].strip(),
            "entrypoint_sha256": sha256(codex), "node_sha256": sha256(node),
            "config_sha256": sha256(Path(env["CODEX_HOME"]) / "config.toml"),
            "sandbox_sha256": sha256(profile), "catalog": catalog,
            "mode": "offline_native_direct_fixture" if catalog else "offline_native_fixture", "live_ready": False}


def prepare_source(root, destination, arm):
    """Export tracked frozen source and apply exact, reviewable copy-only edits.

    Original git metadata, histories and untracked results never enter the build.
    The recorded unchanged core hashes identify the preserved native mechanism.
    """
    import io
    import tarfile
    identity = source_identity(root, arm)
    source, target = Path(root).resolve(), Path(destination).resolve()
    require(not target.exists() and not target.is_relative_to(source) and not source.is_relative_to(target),
            "COPY_ONLY", "Destination must be a fresh separate build tree")
    patch_name = {"X": "xean", "R": "rethlas"}.get(arm)
    require(patch_name is not None, "SOURCE_BUILD", "This adapter does not rebuild Codex")
    patch_root = Path(__file__).resolve().parent / "patches"
    patch_path = patch_root / (patch_name + ".json")
    plan = json.loads(patch_path.read_text())
    require(plan["commit"] == identity["commit"], "PATCH_PIN", "Patch source commit differs")
    archived = subprocess.run(["/usr/bin/git", "-C", str(source), "archive", "--format=tar", "HEAD"], capture_output=True)
    require(archived.returncode == 0, "SOURCE_EXPORT", "Cannot export pinned source")
    target.mkdir(parents=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
        archive.extractall(target, filter="data")
    changes = []
    for operation in plan["edits"]:
        path = (target / operation["path"]).resolve()
        require(path.is_relative_to(target), "PATCH_PATH", "Patch path escapes isolated copy")
        text = path.read_text()
        before, after = operation["before"], operation["after"]
        require(text.count(before) == operation.get("count", 1), "PATCH_DRIFT", f"Patch anchor drift: {operation['path']}")
        original_hash = sha256(path)
        path.write_text(text.replace(before, after), encoding="utf-8")
        changes.append({"path": operation["path"], "before_sha256": original_hash, "after_sha256": sha256(path)})
    for origin, relative in plan.get("copies", {}).items():
        path = (target / relative).resolve()
        require(path.is_relative_to(target) and not path.exists(), "PATCH_PATH", "Adapter destination must be new inside copy")
        _new_file(path, (patch_root / origin).read_text())
        changes.append({"path": relative, "before_sha256": None, "after_sha256": sha256(path)})
    if arm in {"X", "R"}:
        from gateway import responses_tools
        _new_file(target / ("src/four-harness-tools.json" if arm == "X" else "four-harness-tools.json"), json.dumps(responses_tools(), sort_keys=True))
    preserved = {}
    for relative in plan["preserved"]:
        require(sha256(source / relative) == sha256(target / relative), "CORE_CHANGED", f"Native mechanism changed: {relative}")
        preserved[relative] = sha256(target / relative)
    files = {str(path.relative_to(target)): sha256(path) for path in target.rglob("*") if path.is_file()}
    receipt = {"arm": arm, "source": identity, "archive_sha256": hashlib.sha256(archived.stdout).hexdigest(),
               "patch_sha256": sha256(patch_path), "changes": changes, "preserved_core": preserved, "files": files,
               "mode": "copy_only", "live_ready": False}
    _new_file(target / "four-harness-build.json", json.dumps(receipt, indent=2))
    return receipt


def copied_fixture(root, *, gateway, arm, source_copy, executable, entry, role_tokens, timeout=30, extra_env=None, native_codex=False):
    """Launch an actual copied entry point with a fake-only transport and OS jail."""
    from gateway import FixtureTransport, Gateway
    require(type(gateway) is Gateway and type(gateway.transport) is FixtureTransport and gateway.is_offline,
            "FAKE_ONLY", "Only immutable fake Responses transport is permitted")
    root, source_copy = Path(root).resolve(), Path(source_copy).resolve()
    require(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "OS_ISOLATION", "Native fixtures require the qualified macOS sandbox")
    require(arm in {"X", "R"} and not (source_copy / ".git").exists(), "COPY_ONLY", "Use an exported native build, never an upstream worktree")
    require(not root.exists(), "FRESH_RUN", "Native fixture root must be new")
    receipt = json.loads((source_copy / "four-harness-build.json").read_text())
    require(receipt["source"]["commit"] == PINS[arm]["commit"], "BUILD_PIN", "Copied native source differs")
    expected = receipt.get("files")
    require(isinstance(expected, dict) and expected, "BUILD_MANIFEST", "Complete copied source hashes are required")
    require(entry and entry[0] in expected, "ENTRYPOINT", "Native entry must be in the pinned build manifest")
    require(type(native_codex) is bool and (not native_codex or arm == "R" and entry[0] == "four_harness_codex_fixture.py"),
            "ENTRYPOINT", "Actual Codex startup is restricted to the audited R offline fixture")
    for relative, digest in expected.items():
        require(sha256(source_copy / relative) == digest, "BUILD_DRIFT", f"Copied native source modified: {relative}")
    root.mkdir(parents=True, mode=0o700)
    executable = Path(executable).resolve()
    node = Path(DEFAULT_NODE).resolve()
    env = isolated_environment(root, token=next(iter(role_tokens.values())), path_entries=[node.parent, executable.parent])
    routes = {}
    for role, token in role_tokens.items():
        url = gateway.url + f"/{arm}/{role}/v1"
        localhost_url(url, arm=arm, role=role)
        routes[role] = {"url": url.removesuffix("/v1"), "token": token}
    env["FOUR_HARNESS_ROUTES"] = json.dumps(routes, separators=(",", ":"))
    from urllib.request import Request, build_opener, ProxyHandler
    caps = set()
    for route in routes.values():
        request = Request(route["url"] + "/manifest", data=b"{}", headers={"Authorization": "Bearer " + route["token"], "Content-Type": "application/json"})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            manifest = json.load(response)
        require(manifest.get("offline") is True and manifest.get("model_policy") == MODEL and manifest.get("context_window") == CONTEXT,
                "GATEWAY_POLICY", "Native fixture must use the frozen offline model/context contract")
        caps.add(manifest["budget"]["max_output_tokens"])
    require(len(caps) == 1 and 0 < next(iter(caps)) <= MAX_OUTPUT, "OUTPUT_CAP", "Native roles must share a frozen bounded output cap")
    env["FOUR_HARNESS_MAX_OUTPUT"] = str(next(iter(caps)))
    require(not extra_env or set(extra_env) <= {"CODEX_BIN", "FOUR_HARNESS_STATE", "FOUR_HARNESS_ROLE", "FOUR_HARNESS_PYTHON_SITE"},
            "ENVIRONMENT", "Unreviewed native fixture environment override")
    env.update(extra_env or {})
    writable = []
    if arm == "R":
        import shlex
        require(set(role_tokens) == set(ROLES["R"]), "ROLE_ROUTE", "Every R role must have a gateway capability")
        config = rethlas_config(gateway.url, role_tokens)
        if native_codex:
            # A copied export has no .git. This marker bounds project discovery
            # to the frozen export and explicitly trusts only that fresh copy.
            config = 'project_root_markers = ["four-harness-build.json"]\n' + config
            for project in (source_copy, source_copy / "agents/generation", source_copy / "agents/verification"):
                config += f'\n[projects.{json.dumps(str(project))}]\ntrust_level = "trusted"\n'
        _new_file(Path(env["CODEX_HOME"]) / "config.toml", config)
        env.update({f"FOUR_HARNESS_{role.upper()}_TOKEN": token for role, token in role_tokens.items()})
        if native_codex:
            codex = DEFAULT_CODEX.resolve()
            package = json.loads((codex.parent.parent / "package.json").read_text())
            require(package.get("name") == "@openai/codex" and package.get("version") == PINS["C"]["version"],
                    "BINARY_VERSION", "Installed R Codex differs from the frozen binary version")
            launch = shlex.join([str(node), str(codex)])
            wrapper_text = ('#!/bin/sh\n'
                f'if [ "$1" = "--version" ]; then exec {launch} --version; fi\n'
                'if [ "$1" != "exec" ]; then exit 64; fi\n'
                'case "${FOUR_HARNESS_ROLE:-}" in generator|verifier) ;; *) exit 65;; esac\n'
                'shift\n'
                f'exec {launch} exec --skip-git-repo-check --config '
                '\'model_reasoning_effort="max"\' --config "model_provider=\\\"four_harness_${FOUR_HARNESS_ROLE}\\\"" "$@"\n')
        else:
            child = "four_harness_mcp_fixture_child.py" if entry[0] == "four_harness_mcp_fixture.py" else "four_harness_fixture_child.py"
            wrapper_text = "#!/bin/sh\nexec " + shlex.join([str(executable), "-I", str(source_copy / child)]) + ' "$@"\n'
        wrapper = _new_file(root / "bin/codex", wrapper_text, mode=0o700)
        env["PATH"] = str(wrapper.parent) + os.pathsep + env["PATH"]
        env["CODEX_BIN"] = str(wrapper)
        env["FOUR_HARNESS_STATE"] = str(root / "child-state.json")
        for relative in ("agents/generation/results", "agents/generation/logs", "agents/verification/results",
                         "agents/generation/memory", "agents/verification/memory"):
            directory = source_copy / relative
            require(not directory.exists(), "FRESH_RUN", "R fixture outputs must not reuse earlier results")
            directory.mkdir(parents=True)
            writable.append(directory)
    work = root / "workspace"
    work.mkdir(mode=0o700)
    readable = [source_copy, executable.parent, node.parent.parent, Path(sys.prefix).resolve(), "/opt/homebrew/Cellar", "/usr/bin", "/bin"]
    if native_codex:
        readable.append(DEFAULT_CODEX.resolve().parent.parent)
    if extra_env and extra_env.get("FOUR_HARNESS_PYTHON_SITE"):
        readable.append(extra_env["FOUR_HARNESS_PYTHON_SITE"])
    profile = _new_file(root / "fixture.sb", macos_fixture_profile(root, readable=readable, writable=writable,
                        gateway_url=next(iter(routes.values()))["url"] + "/v1"))
    prefix = ["/usr/bin/sandbox-exec", "-f", profile]
    isolation_probe(prefix, node=node, env=env, work=work)
    preferences = managed_preferences_probe(prefix, python=executable, env=env, work=work) if native_codex else None
    result = run_process([*prefix, executable, *[str(source_copy / item) if index == 0 else str(item) for index, item in enumerate(entry)]],
                         env=env, cwd=work, timeout=timeout)
    return {**result, "source_pin": PINS[arm], "sandbox_sha256": sha256(profile),
            "isolation_probe_passed": True, "managed_preferences_probe": preferences,
            "entrypoint_sha256": sha256(source_copy / entry[0]), "build_receipt_sha256": sha256(source_copy / "four-harness-build.json"),
            "mode": "offline_native_fixture", "live_ready": False}


def _fixture_response(index, output):
    return {"id": f"resp_fixture_{index}", "object": "response", "created_at": 1,
            "status": "completed", "model": MODEL["model"], "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}}


def _fixture_ledger(root, arm, count, max_output_tokens):
    from ledger import Ledger, FORMAL_BUDGET
    require(type(max_output_tokens) is int and 20 <= max_output_tokens <= MAX_OUTPUT,
            "OUTPUT_CAP", "Fixture cap must fit its twenty output tokens and frozen maximum")
    root = Path(root).resolve()
    require(not root.exists(), "FRESH_RUN", "Fixture evidence root must be new")
    root.mkdir(parents=True, mode=0o700)
    ledger = Ledger(root / "host/ledger.sqlite")
    run_id, campaign = arm + "-fixture", "offline-" + arm.lower()
    ledger.create_campaign(campaign, max_requests=count, max_tokens=120000, scope="offline")
    ledger.create_run(run_id, campaign, arm, budget={**FORMAL_BUDGET, "model_requests": count,
                                                   "total_tokens": 120000, "max_output_tokens": max_output_tokens})
    return root, ledger, run_id


def xean_fixture(root, *, source_copy, bun, max_output_tokens=MAX_OUTPUT):
    """Auditable actual Explorer role/Pi fixture; not X's complete host workflow."""
    from gateway import FixtureTransport, Gateway, capability
    from tools import CommonTools
    root, ledger, run_id = _fixture_ledger(root, "X", 2, max_output_tokens)
    common = CommonTools(run_id=run_id, run_root=root / "tools", materials={}, accounting=lambda event: ledger.tool_event(run_id, event))
    tokens = {role: capability() for role in ("explorer", "coordinator", "verifier")}
    intrinsic = {"submit_notes", "submit_coordination", "submit_verdict", "submit_statement", "submit_proof", "read_notes"}
    transport = FixtureTransport([
        _fixture_response(1, [{"type": "function_call", "id": "fc_x_1", "call_id": "call_x_1", "name": "materials_list", "arguments": "{}", "status": "completed"}]),
        _fixture_response(2, [{"type": "function_call", "id": "fc_x_2", "call_id": "call_x_2", "name": "submit_notes",
            "arguments": json.dumps({"notes": [{"text": "Fixture arithmetic: 2 + 3 = 5.", "support": [], "summary": None, "detailedSummary": None}]}), "status": "completed"}])])
    with Gateway(ledger, transport=transport,
                 bindings={token: {"run_id": run_id, "arm": "X", "role": role} for role, token in tokens.items()},
                 tools={run_id: common}, allowed_tools={run_id: set(TOOLS) | intrinsic},
                 intrinsic_tools={(run_id, role): intrinsic for role in tokens}, inject_output_cap=True) as gateway:
        result = copied_fixture(root / "native", gateway=gateway, arm="X", source_copy=source_copy, executable=bun,
                                entry=["four-harness-fixture.ts"], role_tokens=tokens, timeout=40)
        snapshot = ledger.snapshot(run_id)
    return {"native": result, "snapshot": snapshot, "final_snapshot": ledger.snapshot(run_id),
            "events": ledger.events(), "requests": transport.requests,
            "qualification_scope": "actual X Explorer createRoleRuntime + Pi GenerationTask, common material tool and original submit_notes",
            "qualification_blockers": ["FULL_X_HOST_COORDINATOR_VERIFIER_CHAIN_NOT_EXERCISED", "DESCENDANT_PARENT_LINKAGE_NOT_QUALIFIED",
                                       ],
            "native_qualified": False, "live_ready": False}


def rethlas_fixture(root, *, source_root, python_executable, python_site, max_output_tokens=MAX_OUTPUT):
    """Original R exec/resume shell + imported verifier endpoint; fake child only."""
    from gateway import FixtureTransport, Gateway, capability
    root, ledger, run_id = _fixture_ledger(root, "R", 3, max_output_tokens)
    build = root / "source-copy"
    prepare_source(source_root, build, "R")
    tokens = {role: capability() for role in ROLES["R"]}
    transport = FixtureTransport([_fixture_response(index, []) for index in range(1, 4)])
    with Gateway(ledger, transport=transport,
                 bindings={token: {"run_id": run_id, "arm": "R", "role": role} for role, token in tokens.items()}) as gateway:
        result = copied_fixture(root / "native", gateway=gateway, arm="R", source_copy=build, executable=python_executable,
                                entry=["four_harness_verifier_fixture.py"], role_tokens=tokens, timeout=35,
                                extra_env={"FOUR_HARNESS_PYTHON_SITE": str(Path(python_site).resolve())})
        snapshot = ledger.snapshot(run_id)
    return {"native": result, "snapshot": snapshot, "final_snapshot": ledger.snapshot(run_id),
            "events": ledger.events(), "requests": transport.requests,
            "qualification_scope": "actual R generation shell exec/resume and imported verify endpoint/subprocess with one-shot fake child",
            "qualification_blockers": ["NATIVE_CODEX_MODEL_TOOL_ROUNDTRIP_NOT_QUALIFIED", "NATIVE_MCP_ACCOUNTING_NOT_EXERCISED_BY_THIS_FIXTURE",
                                       "RECURSIVE_STRATEGY_REPAIR_COMPACTION_INTERCEPTION_NOT_QUALIFIED",
                                       "FULL_VERIFICATION_CHAIN_NOT_EXERCISED", ],
            "native_qualified": False, "live_ready": False}


def rethlas_codex_fixture(root, *, source_root, python_executable, python_site, max_output_tokens=MAX_OUTPUT):
    """Probe original R generation/verifier commands with genuine pinned Codex.

    These are independent offline startup probes. They cannot qualify a complete
    R generation/verification chain while the native tool surface is rejected.
    """
    from gateway import FixtureTransport, Gateway, capability
    from tools import CommonTools
    dependencies = rethlas_mcp_dependencies(python_site)
    root, ledger, run_id = _fixture_ledger(root, "R", 2, max_output_tokens)
    build = root / "source-copy"
    prepare_source(source_root, build, "R")
    tokens = {role: capability() for role in ROLES["R"]}
    transport = FixtureTransport([_fixture_response(1, []), _fixture_response(2, [])])
    common = CommonTools(run_id=run_id, run_root=root / "tools", materials={},
                         accounting=lambda event: ledger.tool_event(run_id, event))
    with Gateway(ledger, transport=transport,
                 bindings={token: {"run_id": run_id, "arm": "R", "role": role, "require_native_metadata": True}
                           for role, token in tokens.items()},
                 tools={run_id: common}, allowed_tools={run_id: set(TOOLS)}, inject_output_cap=True) as gateway:
        result = copied_fixture(root / "native", gateway=gateway, arm="R", source_copy=build,
                                executable=python_executable, entry=["four_harness_codex_fixture.py"],
                                role_tokens=tokens, timeout=65, native_codex=True,
                                extra_env={"FOUR_HARNESS_PYTHON_SITE": str(Path(python_site).resolve())})
        snapshot = ledger.snapshot(run_id)
    details = json.loads(result["stdout"]) if result["returncode"] == 0 else {}
    rejected = [{key: event[key] for key in ("event_id", "role", "code")} for event in ledger.events()
                if event["type"] == "request_rejected"]
    blockers = sorted({event["code"] for event in rejected}) or ["NATIVE_PROCESS_FAILED"]
    return {"native": result, "details": details, "snapshot": snapshot, "final_snapshot": ledger.snapshot(run_id),
            "dependencies": dependencies, "events": ledger.events(), "requests": transport.requests,
            "gateway_rejections": rejected,
            "qualification_scope": "actual original R generation shell and separate verifier API invoke installed Codex; startup/rejection probes only",
            "qualification_blockers": blockers + ["ACTUAL_RECURSIVE_SPAWN_NOT_EXERCISED", "FULL_VERIFICATION_SKILL_CHAIN_NOT_EXERCISED"],
            "native_qualified": False, "live_ready": False}


def rethlas_mcp_dependencies(python_site):
    """Verify the complete isolated Python dependency resolution before startup."""
    from importlib.metadata import distributions
    pin_file = Path(__file__).resolve().parent / "patches/rethlas-mcp-requirements.txt"
    normalize = lambda name: name.lower().replace("_", "-").replace(".", "-")
    expected = {}
    for line in pin_file.read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            expected[normalize(name)] = version
    site = Path(python_site).resolve()
    require(site.is_dir(), "DEPENDENCIES", "Isolated R MCP dependencies are absent")
    files = {}
    for item in sorted(site.rglob("*")):
        relative = item.relative_to(site)
        require(item.resolve().is_relative_to(site), "DEPENDENCIES", "R MCP dependency symlink escapes its isolated tree")
        if item.is_dir() or "__pycache__" in relative.parts or item.suffix == ".pyc":
            continue
        require(item.is_file(), "DEPENDENCIES", "R MCP dependency tree contains a nonregular file")
        files[relative.as_posix()] = sha256(item)
    actual = {normalize(item.metadata["Name"]): item.version for item in distributions(path=[str(site)])}
    require(actual == expected, "DEPENDENCIES", "R MCP isolated dependency versions differ from the frozen snapshot")
    tree_hash = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"versions": actual, "requirements_sha256": sha256(pin_file),
            "file_tree_sha256": tree_hash, "file_count": len(files)}


def rethlas_mcp_fixture(root, *, source_root, python_executable, python_site, max_output_tokens=MAX_OUTPUT, scenario="positive"):
    """Actual original MCP memory/verification path; immutable fake model replies.

    Native metadata fixtures cover descendant accounting, not the Rust spawn
    implementation. The actual Codex tool surface still fails gateway admission.
    """
    from gateway import FixtureTransport, Gateway, capability
    from tools import CommonTools
    require(scenario in {"positive", "unknown-parent"}, "FIXTURE_SCENARIO", "Unknown R MCP fixture scenario")
    dependencies = rethlas_mcp_dependencies(python_site)
    root, ledger, run_id = _fixture_ledger(root, "R", 3 if scenario == "positive" else 1, max_output_tokens)
    build = root / "source-copy"
    prepare_source(source_root, build, "R")
    reference = _new_file(root / "host/reference.txt", "Frozen offline fixture arithmetic reference: 2 + 3 = 5.\n")
    common = CommonTools(run_id=run_id, run_root=root / "tools",
                         materials={"fixture": {"path": reference, "sha256": sha256(reference)}},
                         accounting=lambda event: ledger.tool_event(run_id, event))
    tokens = {role: capability() for role in ROLES["R"]}
    generation = {"rethlas_generation_" + name for name in
                  ("memory_init", "memory_append", "memory_search", "branch_update", "search_arxiv_theorems", "verify_proof_service")}
    verification = {"rethlas_verification_" + name for name in
                    ("memory_init", "memory_append", "memory_query", "search_arxiv_theorems", "validate_verification_output", "write_verification_output")}
    report = {"verdict": "correct", "verification_report": {"summary": "Offline arithmetic transport fixture only.",
              "critical_errors": [], "gaps": [], "notes": "Fixture only", "checked_items": []}, "repair_hints": ""}
    transport = FixtureTransport([_fixture_response(1, []), _fixture_response(2, []),
        _fixture_response(3, [{"type": "message", "id": "fixture_report", "role": "assistant", "status": "completed",
                              "content": [{"type": "output_text", "text": json.dumps(report), "annotations": []}]}])])
    with Gateway(ledger, transport=transport,
                 bindings={token: {"run_id": run_id, "arm": "R", "role": role, "require_native_metadata": True}
                           for role, token in tokens.items()},
                 tools={run_id: common}, allowed_tools={run_id: set(TOOLS)},
                 intrinsic_tools={(run_id, role): verification if role == "verifier" else generation for role in tokens}) as gateway:
        result = copied_fixture(root / "native", gateway=gateway, arm="R", source_copy=build, executable=python_executable,
                                entry=["four_harness_mcp_fixture.py", scenario], role_tokens=tokens, timeout=75,
                                extra_env={"FOUR_HARNESS_PYTHON_SITE": str(Path(python_site).resolve())})
        snapshot = ledger.snapshot(run_id)
    capabilities = json.loads(result["stdout"]) if result["returncode"] == 0 else {}
    return {"native": result, "snapshot": snapshot, "final_snapshot": ledger.snapshot(run_id),
            "events": ledger.events(), "requests": transport.requests,
            "dependencies": dependencies, "common_compute": capabilities.get("common_compute"), "scenario": scenario,
            "qualification_scope": "actual original R MCP memory/BM25/branch, common tools, frozen search, verify API/subprocess and original report validation/writing; native metadata protocol fixture",
            "qualification_blockers": ["NATIVE_CODEX_MODEL_TOOL_ROUNDTRIP_NOT_QUALIFIED", "ACTUAL_RECURSIVE_SPAWN_NOT_EXERCISED",
                                       "REPAIR_COMPACTION_NOT_EXERCISED", "FULL_VERIFICATION_SKILL_CHAIN_NOT_EXERCISED",
                                       ] +
                                      (["COMMON_COMPUTE_OS_CONTAINMENT_UNAVAILABLE"] if capabilities.get("common_compute", {}).get("available") is False else []),
            "native_qualified": False, "live_ready": False}


def xean_settings():
    """Every Pi role is a named gateway provider; no Codex web-search branch."""
    roles = {name: {"provider": f"four-harness-{role}", "model": MODEL["model"], "reasoning": MODEL["effort"]}
             for name, role in (("explorer", "explorer"), ("coordinator", "coordinator"),
                                ("correctness", "verifier"), ("source", "verifier"),
                                ("requirements", "verifier"), ("reconstruction", "verifier"))}
    return {**roles, "maxExplorerTurns": 10, "window": 100000}

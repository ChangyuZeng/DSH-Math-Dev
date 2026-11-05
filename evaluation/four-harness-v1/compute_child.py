"""Trusted bootstrap for CommonTools; never an unsandboxed Python executor.

The host launches this frozen file in an isolated interpreter. It installs
Seatbelt once on macOS, or starts inside bubblewrap and installs Linux seccomp.
User code is read only after OS restrictions and host-protection guards are installed.
macos_monitored deliberately omits RLIMIT_AS; the parent measures RSS and kills
over-limit processes without claiming a hard memory ceiling. Strict mode requires
RLIMIT_AS. The Linux seccomp filter also prohibits process/thread creation, exec,
namespace changes and network syscalls. This is OS enforcement, not an import blacklist.
"""

import ctypes
import errno
import json
import math
import os
from pathlib import Path
import platform
import resource
import sys


def _linux_filter():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = 0xC000003E
        denied = [41, 42, 43, 49, 50, 53, 56, 57, 58, 59, 101, 109, 112,
                  155, 161, 165, 166, 169, 175, 176, 203, 246, 272, 288, 298,
                  302, 308, 310, 311, 313, 321, 322, 323, 425, 426, 427, 435, 438]
    elif machine in ("aarch64", "arm64"):
        arch = 0xC00000B7
        denied = [39, 40, 41, 51, 97, 104, 117, 122, 142, 146, 198, 199,
                  200, 201, 202, 203, 220, 221, 242, 261, 268, 270, 271,
                  272, 273, 280, 281, 282, 425, 426, 427, 435, 438]
    else:
        raise RuntimeError("unsupported Linux seccomp architecture")

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    # Verify the syscall ABI; x32 uses a second syscall-number space.
    instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
                    (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000),
                    (0x06, 0, 0, 0x80000000)]
    for syscall in denied:
        instructions.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, 0x00050000 | errno.EPERM)])
    instructions.append((0x06, 0, 0, 0x7FFF0000))
    filters = (Filter * len(instructions))(*(Filter(*item) for item in instructions))
    program = Program(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise OSError(ctypes.get_errno(), "cannot install seccomp")


def _mac_lockdown(config):
    # Only the frozen bootstrap has run so far (-I -S, no inherited settings).
    # Install the deny-default profile once before reading submitted Python;
    # even interpreter re-exec is then prohibited. Failure has no fallback.
    library = ctypes.CDLL("/usr/lib/libsandbox.dylib")
    library.sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64,
                                    ctypes.POINTER(ctypes.c_char_p)]
    library.sandbox_init.restype = ctypes.c_int
    error = ctypes.c_char_p()
    profile = Path(config["lockdown_profile"]).read_bytes()
    if library.sandbox_init(profile, 0, ctypes.byref(error)):
        message = error.value.decode("utf-8", errors="replace") if error.value else "unknown error"
        if error.value:
            library.sandbox_free_error.argtypes = [ctypes.c_char_p]
            library.sandbox_free_error(error)
        raise RuntimeError("cannot install macOS process sandbox: " + message)


def _limit(kind, value):
    _, current_hard = resource.getrlimit(kind)
    ceiling = value if current_hard == resource.RLIM_INFINITY else min(value, current_hard)
    resource.setrlimit(kind, (ceiling, ceiling))


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("seatbelt", "bubblewrap"):
        raise RuntimeError("this bootstrap requires the common-tool OS sandbox")
    config = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if config["backend"] != sys.argv[1]:
        raise RuntimeError("sandbox backend mismatch")
    mode = config["resource_mode"]
    if mode not in ("macos_monitored", "strict") or mode == "macos_monitored" and sys.platform != "darwin":
        raise RuntimeError("unsupported resource mode")
    _limit(resource.RLIMIT_CORE, 0)
    _limit(resource.RLIMIT_FSIZE, 0)  # all files are read-only; stdout uses pipes
    _limit(resource.RLIMIT_NOFILE, 64)
    if config["cpu_seconds"] is not None:
        _limit(resource.RLIMIT_CPU, max(1, math.ceil(config["cpu_seconds"])))
    if mode == "strict":
        _limit(resource.RLIMIT_AS, config["memory_bytes"])
    if sys.platform == "linux" and sys.argv[1] == "bubblewrap":
        # Optional placement protects the host; it is not a fair model budget.
        if config["cpu_cores"] is not None:
            available = sorted(os.sched_getaffinity(0))
            os.sched_setaffinity(0, available[:config["cpu_cores"]])
        _linux_filter()
    elif sys.platform == "darwin" and sys.argv[1] == "seatbelt":
        _mac_lockdown(config)
    else:
        raise RuntimeError("unsupported sandbox/platform combination")
    code = Path(config["code_path"]).read_text(encoding="utf-8")
    namespace = {"__name__": "__main__", "__file__": config["code_path"],
                 "WORKSPACE": config["workspace"], "MATERIALS": config["materials"]}
    # This is the sole execution site, reached only inside the OS sandbox.
    exec(compile(code, "<submitted-python>", "exec"), namespace, namespace)


if __name__ == "__main__":
    main()

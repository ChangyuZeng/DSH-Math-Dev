"""Observe native writable state and stop dispatch on artifact boundary violations.

This is an observed watchdog with explicit sampling gaps, not a filesystem quota.
Read-only harness/runtime trees must live outside the writable roots.
"""
import os
from pathlib import Path
import stat
import threading
import time
from ledger import GateError, require


class StateGuard:
    def __init__(self, roots, *, max_bytes, on_stop, interval=0.05):
        require(type(max_bytes) is int and max_bytes > 0, 'STATE_LIMIT', 'Invalid state byte limit')
        self.roots = tuple(Path(p).resolve(strict=True) for p in roots)
        require(bool(self.roots) and all(p.is_dir() for p in self.roots), 'STATE_ROOT', 'Existing writable roots required')
        require(all(a != b and a not in b.parents and b not in a.parents
                    for i, a in enumerate(self.roots) for b in self.roots[i+1:]),
                'STATE_ROOT', 'Writable roots must not overlap')
        self.identities = [(p.stat().st_dev, p.stat().st_ino) for p in self.roots]
        self.max_bytes, self.on_stop, self.interval = max_bytes, on_stop, interval
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.violation = None
        self.samples = 0
        self.peak = 0
        self.last = None
        self.unknown_samples = 0
        self.scan_retries = 0
        self.last_observation_error = None

    def _measure(self):
        total = 0
        for root, identity in zip(self.roots, self.identities):
            info = root.lstat()
            require(not stat.S_ISLNK(info.st_mode) and (info.st_dev, info.st_ino) == identity,
                    'STATE_ROOT_CHANGED', 'Writable root identity changed')
            def scan_error(error):
                raise error
            for directory, names, files in os.walk(root, followlinks=False, onerror=scan_error):
                for name in names + files:
                    st = (Path(directory) / name).lstat()
                    require(not stat.S_ISLNK(st.st_mode), 'STATE_LINK', 'Symlinks are not admitted in writable state')
                    require(stat.S_ISDIR(st.st_mode) or (stat.S_ISREG(st.st_mode) and st.st_nlink == 1),
                            'STATE_FILE', 'Nonregular or hardlinked writable state')
                    if stat.S_ISREG(st.st_mode):
                        total += st.st_size
            current=root.lstat()
            require((current.st_dev,current.st_ino)==identity and not stat.S_ISLNK(current.st_mode),
                    'STATE_ROOT_CHANGED','Writable root identity changed during observation')
        return total

    def check(self):
        with self.lock:
            if self.violation:
                raise GateError(self.violation, 'Native state guard has stopped this run')
            try:
                for attempt in range(3):
                    try:
                        total=self._measure()
                        break
                    except FileNotFoundError as error:
                        # Atomic cache/WAL replacement can invalidate a walk.
                        # Discard this entire sample; never call it zero bytes.
                        self.unknown_samples += 1
                        self.last_observation_error={'type':type(error).__name__,'errno':error.errno}
                        if attempt == 2:
                            raise
                        self.scan_retries += 1
                self.samples += 1
                self.last = total
                self.peak = max(self.peak, total)
                require(total <= self.max_bytes, 'ARTIFACT_BUDGET', 'Native writable artifacts exceeded run budget')
                return total
            except Exception as error:
                if isinstance(error,OSError) and not isinstance(error,FileNotFoundError):
                    self.unknown_samples += 1
                    self.last_observation_error={'type':type(error).__name__,'errno':error.errno}
                self.violation = getattr(error, 'code', 'STATE_OBSERVATION_UNKNOWN')
                self.on_stop(self.violation)
                raise GateError(self.violation, 'Native state boundary violated; dispatch stopped') from error

    def start(self):
        require(self.thread is None, 'STATE_STARTED', 'State guard already started')
        self.check()
        def observe():
            while not self.done.wait(self.interval):
                try:
                    self.check()
                except GateError:
                    return
        self.thread = threading.Thread(target=observe, name='four-state-guard', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.done.set()
        if self.thread:
            self.thread.join(timeout=2)
        try:
            self.check()
        except GateError:
            pass
        return self.report()

    def report(self):
        return {'sample_count': self.samples, 'last_bytes': self.last, 'peak_sampled_bytes': self.peak,
                'max_bytes': self.max_bytes, 'violation': self.violation,
                'unknown_sample_count':self.unknown_samples,'scan_retry_count':self.scan_retries,
                'scan_retry_limit':2,'last_observation_error':self.last_observation_error,
                'mode': 'observed_watchdog_and_predispatch_check', 'poll_seconds': self.interval,
                'transient_peak_complete': False, 'filesystem_hard_quota': False}

"""Host-side process observations, never a CPU/memory fairness or enforcement claim.

ps reports per-process (not children-inclusive) CPU. Samples can miss short-lived
children and peaks; CPU has ps display precision and is not an exact total. No
command lines, environment variables, paths or file contents are collected.
"""
from __future__ import annotations

import math
import subprocess
import threading
import time


def cpu_time_seconds(value: str) -> float:
    days = 0
    if '-' in value:
        day, value = value.split('-', 1)
        days = int(day)
    parts = value.split(':')
    if len(parts) not in (2, 3):
        raise ValueError('Unsupported ps CPU time')
    numbers = [float(part) for part in parts]
    if days < 0 or any(not math.isfinite(number) or number < 0 for number in numbers):
        raise ValueError('Invalid ps CPU time')
    result = days * 86400.0
    for index, number in enumerate(reversed(numbers)):
        result += number * (60 ** index)
    return result


def process_snapshot():
    completed = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,time=,rss='],
                               capture_output=True, text=True, timeout=1,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    if completed.returncode:
        raise RuntimeError('Process metrics unavailable')
    rows = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 5:
            continue
        try:
            pid, parent, group = map(int, fields[:3])
            cpu, rss = cpu_time_seconds(fields[3]), int(fields[4]) * 1024
            if pid <= 0 or parent < 0 or group < 0 or rss < 0:
                continue
            rows.append({'pid': pid, 'ppid': parent, 'pgid': group, 'cpu_seconds': cpu, 'rss_bytes': rss})
        except ValueError:
            continue
    return rows


class ProcessObserver:
    def __init__(self, root_pid: int, *, interval=0.1, sampler=process_snapshot):
        if type(root_pid) is not int or root_pid <= 0 or not 0.02 <= interval <= 1:
            raise ValueError('Invalid process observer configuration')
        self.pid, self.interval, self.sampler = root_pid, interval, sampler
        self.end = threading.Event()
        self.thread = None
        self.started = None
        self.samples = self.failures = 0
        self.cpu = {}
        self.peak = None
        self.regression = False

    def consume(self, rows):
        # Own group survives parent exit; descendants may establish another group.
        selected = {row['pid'] for row in rows if row['pid'] == self.pid or row['pgid'] == self.pid}
        while True:
            expanded = selected | {row['pid'] for row in rows if row['ppid'] in selected}
            if expanded == selected:
                break
            selected = expanded
        active = [row for row in rows if row['pid'] in selected]
        if not active:
            return
        self.samples += 1
        self.peak = max(self.peak or 0, sum(row['rss_bytes'] for row in active))
        for row in active:
            previous = self.cpu.get(row['pid'])
            if previous is not None and row['cpu_seconds'] < previous:
                self.regression = True  # PID reuse or platform counter ambiguity.
            self.cpu[row['pid']] = max(previous or 0.0, row['cpu_seconds'])

    def _run(self):
        while not self.end.is_set():
            try:
                self.consume(self.sampler())
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                self.failures += 1
            self.end.wait(self.interval)

    def start(self):
        if self.thread is not None:
            raise RuntimeError('Process observer cannot restart')
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True, name='four-harness-resource-observer')
        self.thread.start()
        return self

    def stop(self):
        self.end.set()
        if self.thread:
            self.thread.join(timeout=1.5)
        return self.report()

    def report(self):
        finished = self.thread is None or not self.thread.is_alive()
        return {'scope': 'sampled_process_group_and_descendants', 'source': 'ps pid/ppid/pgid/time/rss',
                'sample_interval_seconds': self.interval, 'samples': self.samples, 'failed_samples': self.failures,
                'sampled_processes': len(self.cpu),
                'sampled_cpu_seconds': sum(self.cpu.values()) if self.cpu and not self.regression else None,
                'cpu_precision': 'ps formatted cumulative CPU; rounded and incomplete',
                'peak_sampled_rss_bytes': self.peak, 'pid_or_counter_ambiguity': self.regression,
                'observer_stopped': finished, 'whole_process_tree_complete': False,
                'missed_short_lived_processes_possible': True, 'missed_memory_peaks_possible': True,
                'equal_cpu_or_memory_budget': False, 'aggregate_with_tool_cpu': False,
                'unknown_means_zero': False}

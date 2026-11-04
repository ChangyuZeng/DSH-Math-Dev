"""Validate truthful sampling semantics; never advertise whole-tree totals."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'))
from observation import ProcessObserver, cpu_time_seconds, process_snapshot


def row(pid, parent, group, cpu, rss):
    return {'pid': pid, 'ppid': parent, 'pgid': group, 'cpu_seconds': cpu, 'rss_bytes': rss}


class ObservationTests(unittest.TestCase):
    def test_unknown_samples_stay_null_and_counter_ambiguity_is_not_total(self):
        observer = ProcessObserver(10)
        empty = observer.report()
        self.assertIsNone(empty['sampled_cpu_seconds'])
        self.assertIsNone(empty['peak_sampled_rss_bytes'])
        observer.consume([row(10, 1, 10, 3, 100), row(11, 10, 10, 2, 200), row(12, 11, 12, 1, 50), row(99, 1, 99, 999, 999)])
        observer.consume([row(11, 1, 10, 4, 300)])
        result = observer.report()
        self.assertEqual(result['sampled_cpu_seconds'], 8)
        self.assertEqual(result['peak_sampled_rss_bytes'], 350)
        self.assertFalse(result['whole_process_tree_complete'])
        self.assertFalse(result['aggregate_with_tool_cpu'])
        self.assertTrue(result['missed_short_lived_processes_possible'])
        observer.consume([row(11, 1, 10, 1, 100)])
        self.assertIsNone(observer.report()['sampled_cpu_seconds'])
        self.assertTrue(observer.report()['pid_or_counter_ambiguity'])

    def test_cpu_formats_and_live_sampler_expose_no_command_lines(self):
        self.assertEqual(cpu_time_seconds('02:03.50'), 123.5)
        self.assertEqual(cpu_time_seconds('1-02:03:04.50'), 93784.5)
        with self.assertRaises(ValueError):
            cpu_time_seconds('nan:00')
        rows = process_snapshot()
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(set(value) == {'pid', 'ppid', 'pgid', 'cpu_seconds', 'rss_bytes'} for value in rows))


if __name__ == '__main__':
    unittest.main()

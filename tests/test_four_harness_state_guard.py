import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'
sys.path.insert(0, str(HERE))
from state_guard import StateGuard
from ledger import GateError


class StateGuards(unittest.TestCase):
    def test_native_state_and_common_workspace_share_one_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            a, b = root/'native', root/'common'
            a.mkdir(); b.mkdir()
            reasons = []
            guard = StateGuard([a, b], max_bytes=20, on_stop=reasons.append)
            (a/'state.sqlite').write_bytes(b'x'*12)
            (b/'file').write_bytes(b'y'*8)
            self.assertEqual(guard.check(), 20)
            (a/'state.sqlite-wal').write_bytes(b'!')
            with self.assertRaises(GateError): guard.check()
            self.assertEqual(reasons, ['ARTIFACT_BUDGET'])
            self.assertFalse(guard.report()['filesystem_hard_quota'])
            with self.assertRaises(GateError): guard.check()
            self.assertEqual(len(reasons), 1)

    def test_link_root_swap_and_overlapping_roots_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            a = root/'native'; a.mkdir()
            b = a/'nested'; b.mkdir()
            with self.assertRaises(GateError): StateGuard([a,b], max_bytes=100,on_stop=lambda _:None)
            guard = StateGuard([a], max_bytes=100,on_stop=lambda _:None)
            (a/'outside').symlink_to(root/'secret')
            with self.assertRaises(GateError): guard.check()

    def test_unreadable_subtree_stops_instead_of_reporting_zero_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            reasons=[]
            guard=StateGuard([root],max_bytes=100,on_stop=reasons.append)
            with patch('state_guard.os.scandir',side_effect=PermissionError('fixture')):
                with self.assertRaises(GateError):guard.check()
            self.assertEqual(reasons,['STATE_OBSERVATION_UNKNOWN'])
            self.assertEqual(guard.report()['violation'],'STATE_OBSERVATION_UNKNOWN')
            self.assertIsNone(guard.report()['last_bytes'])

    def test_atomic_file_removal_discards_partial_sample_and_rescans_whole_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve(); (root/'kept').write_bytes(b'12345')
            reasons=[]; guard=StateGuard([root],max_bytes=5,on_stop=reasons.append)
            import os
            original=os.walk; calls=[]
            def walk(*args,**kwargs):
                calls.append(1)
                return iter([(str(root),[],['vanished'])]) if len(calls)==1 else original(*args,**kwargs)
            with patch('state_guard.os.walk',side_effect=walk):
                self.assertEqual(guard.check(),5)
            self.assertEqual(reasons,[])
            self.assertEqual(guard.report()['unknown_sample_count'],1)
            self.assertEqual(guard.report()['scan_retry_count'],1)
            self.assertEqual(guard.report()['sample_count'],1)
            self.assertEqual(guard.report()['last_bytes'],5)
            self.assertFalse(guard.report()['transient_peak_complete'])

    def test_repeated_missing_files_exhaust_retry_without_a_zero_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve(); reasons=[]
            guard=StateGuard([root],max_bytes=100,on_stop=reasons.append)
            with patch('state_guard.os.walk',side_effect=lambda *a,**k:iter([(str(root),[],['vanished'])])):
                with self.assertRaises(GateError):guard.check()
            self.assertEqual(reasons,['STATE_OBSERVATION_UNKNOWN'])
            self.assertEqual(guard.report()['unknown_sample_count'],3)
            self.assertEqual(guard.report()['scan_retry_count'],2)
            self.assertIsNone(guard.report()['last_bytes'])

    def test_watchdog_start_stop_retains_observation_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            guard = StateGuard([root],max_bytes=100,on_stop=lambda _:None).start()
            result = guard.stop()
            self.assertGreaterEqual(result['sample_count'],2)
            self.assertIsNone(result['violation'])
            self.assertFalse(result['transient_peak_complete'])

if __name__ == '__main__': unittest.main()

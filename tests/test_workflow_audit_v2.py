"""Independent-audit regressions. Synthetic receipts only; no upstream calls."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'evaluation' / 'workflow-v2'))
from campaign import usage_projection
from driver import immediate_failure


class AuditAccountingTests(unittest.TestCase):
    def test_sync_transport_failure_journals_without_reentering_ledger_transaction(self):
        from workflow_runtime import WorkflowRuntime, Ledger, OPTIMIZATION_BUDGET
        from transport import HostTransport
        from ledger import GateError
        from test_four_harness_transport import payload
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())
            runtime = WorkflowRuntime(folder, 'project', 'run')
            runtime.directory = Path(folder)
            runtime.ledger = Ledger(Path(folder) / 'ledger.sqlite')
            runtime.ledger.create_campaign('fixture', max_requests=120, max_tokens=None, scope='optimization')
            runtime.ledger.create_run('run', 'fixture', 'M', budget=OPTIMIZATION_BUDGET)
            request = {**payload(), 'max_output_tokens': 128000}
            request_id = runtime.ledger.reserve_request('run', 'worker', request)
            runtime.ledger.start_request(request_id)
            def unavailable():
                raise GateError('AUTH_UNAVAILABLE', 'Synthetic credential failure')
            transport = HostTransport(profile='workflow', max_output_tokens=128000,
                auth_loader=unavailable, evidence_sink=runtime._transport_evidence)
            context = {'request_id': request_id, 'logical_request_id': request_id,
                'attempt': 1, 'retry_of': None, 'cancel_event': threading.Event()}
            with patch.object(runtime.ledger, 'record', side_effect=AssertionError('Nested write forbidden')):
                with self.assertRaises(GateError) as failure:
                    runtime.ledger.dispatch_request(request_id, lambda: transport.start(request, context))
            self.assertFalse(failure.exception.evidence.get('evidence_sink_failed', False))
            files = list((Path(folder) / 'transport').glob('*.json'))
            self.assertEqual(len(files), 1)
            saved = json.loads(files[0].read_bytes())
            self.assertEqual(saved['request_id'], request_id)
            self.assertEqual(saved['send_state'], 'not_started')
            self.assertEqual(runtime.ledger.snapshot('run')['total_tokens'], None)

    def test_partial_receipt_is_not_complete_cost(self):
        receipt = {'request_id': 'partial', 'status': 'unknown', 'usage': {
            'input_tokens': 10, 'output_tokens': 0, 'total_tokens': 10,
            'cached_input_tokens': None, 'reasoning_tokens': None, 'complete': False}}
        value = usage_projection([receipt])
        self.assertIsNone(value['total_tokens'])
        self.assertFalse(value['core_complete'])
        self.assertEqual(value['known_total_tokens'], 10)
        self.assertEqual(value['unknown_requests'], 1)

    def test_complete_terminal_cost_survives_an_unknown_solver_outcome(self):
        receipt = {'request_id': 'terminal', 'status': 'unknown', 'usage': {
            'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12,
            'cached_input_tokens': None, 'reasoning_tokens': None, 'complete': True}}
        value = usage_projection([receipt])
        self.assertEqual(value['total_tokens'], 12)
        self.assertTrue(value['core_complete'])

    def test_actual_drift_is_not_hidden_by_an_unknown_usage_anomaly(self):
        for facts in ({'actual_model': 'other-model'}, {'actual_effort': 'low'}):
            self.assertEqual(immediate_failure('USAGE_UNKNOWN', [
                {'type': 'request_unknown', 'anomaly': 'usage_unknown', **facts}]), 'model_drift')

    def test_transport_drift_flags_pause_on_first_failure(self):
        for flag, expected in [('error_model_drift', 'model_drift'),
                               ('error_effort_drift', 'model_drift'),
                               ('error_schema_invalid', 'protocol_drift')]:
            with self.subTest(flag=flag):
                events = [{'type': 'transport_evidence', 'evidence': {flag: True, 'http_status': 500}}]
                self.assertEqual(immediate_failure('UPSTREAM_HTTP', events), expected)


if __name__ == '__main__':
    unittest.main()

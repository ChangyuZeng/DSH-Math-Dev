"""Host-only prepare endpoint auth, parent binding, and immutable snapshot integration."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_four_harness_gateway as gateway_fixture
import tools


class CandidateFileGateway(unittest.TestCase):
    setUp = gateway_fixture.GatewayTests.setUp
    start = gateway_fixture.GatewayTests.start
    post = gateway_fixture.GatewayTests.post

    def setup_host(self, **kwargs):
        self.host = tools.CommonTools('M1', Path(self.tmp.name) / 'M1', {},
                                      accounting=lambda event: self.ledger.tool_event('M1', event))
        data = b'{"pairs":[]}'
        self.host.workspace.write('candidate.json', data)
        self.args = {'candidate_file': {'path': 'candidate.json', 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}}
        gw = self.start(tools={'M1': self.host}, **kwargs)
        status, _ = self.post(gw)
        self.assertEqual(status, 200)
        self.parent = self.transport.requests[0]['request_id']
        self.headers = {'X-Four-Parent-Request-Id': self.parent, 'X-Four-Native-Call-Id': 'file-call'}
        return gw

    def test_prepare_requires_authenticated_host_parent_and_native_call(self):
        gw = self.setup_host()
        payload = {'name': 'check', 'arguments': self.args}
        path = '/M/worker/prepare-candidate'
        self.assertEqual(self.post(gw, payload, path=path, token='invalid')[0], 409)
        self.assertEqual(self.post(gw, payload, path=path)[1]['error']['code'], 'CANDIDATE_PREPARE_BINDING')
        self.assertEqual(self.post(gw, payload, path=path, headers={**self.headers, 'X-Four-Parent-Request-Id': 'forged'})[0], 409)
        self.assertEqual(self.post(gw, {'name': 'compute', 'arguments': {}}, path=path, headers=self.headers)[0], 409)
        self.assertEqual(list(self.host.control.path.glob('prepare-receipt-*')), [])

    def test_gateway_prepare_dispatch_keeps_snapshot_and_rejects_token_reuse(self):
        gw = self.setup_host()
        status, prepared = self.post(gw, {'name': 'check', 'arguments': self.args}, path='/M/worker/prepare-candidate', headers=self.headers)
        self.assertEqual(status, 200, prepared)
        self.host.workspace.write('candidate.json', b'not json')
        payload = {'name': 'check', 'arguments': self.args, 'prepared_candidate': prepared['prepare_id']}
        status, receipt = self.post(gw, payload, path='/M/worker/tools', headers=self.headers)
        self.assertEqual(status, 200, receipt)
        self.assertTrue(receipt['ok'], receipt)
        self.assertEqual(receipt['result']['candidate_sha256'], prepared['candidate_sha256'])
        self.assertFalse(receipt['result']['legal'])
        self.assertEqual(self.post(gw, payload, path='/M/worker/tools', headers=self.headers)[1]['error']['code'], 'candidate_prepare_binding')

    def test_invalid_json_returns_failure_receipt_without_a_prepare_token(self):
        gw = self.setup_host()
        data = b'{"a":1,"a":2}'
        self.host.workspace.write('candidate.json', data)
        args = {'candidate_file': {'path': 'candidate.json', 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}}
        status, receipt = self.post(gw, {'name': 'check', 'arguments': args}, path='/M/worker/prepare-candidate', headers=self.headers)
        self.assertEqual(status, 200, receipt)
        self.assertFalse(receipt['ok'])
        self.assertEqual(receipt['error']['code'], 'invalid_candidate_json')
        self.assertEqual(receipt['metrics']['phase'], 'candidate_preparation')
        self.assertNotIn('prepare_id', receipt)
        self.assertEqual(len(list(self.host.control.path.glob('prepare-receipt-*'))), 1)

    def test_prepare_audit_pairs_are_distinct_from_actual_tool_requests(self):
        events = []
        gw = self.setup_host(audit_sink=lambda kind, identity, value: events.append((kind, identity, value)))
        events.clear()
        payload = {'name': 'check', 'arguments': self.args}
        _, prepared = self.post(gw, payload, path='/M/worker/prepare-candidate', headers=self.headers)
        self.assertEqual([row[0] for row in events], ['candidate_prepare_request', 'candidate_prepare_response'])
        self.assertEqual(events[0][1], events[1][1])
        self.assertFalse(any(row['type'] == 'tool_started' for row in self.ledger.events()))
        self.post(gw, {**payload, 'prepared_candidate': prepared['prepare_id']}, path='/M/worker/tools', headers=self.headers)
        self.assertEqual([row[0] for row in events[-2:]], ['tool_request', 'tool_response'])
        self.assertEqual(events[-2][1], events[-1][1])
        self.assertEqual(sum(row['type'] == 'tool_started' for row in self.ledger.events()), 1)
        events.clear()
        # Explicit hash mismatch is a known failure, with its own matched prepare audit.
        args = {'candidate_file': {**self.args['candidate_file'], 'sha256': '0' * 64}}
        self.post(gw, {'name': 'check', 'arguments': args}, path='/M/worker/prepare-candidate', headers=self.headers)
        self.assertEqual([row[0] for row in events], ['candidate_prepare_request', 'candidate_prepare_response'])
        self.assertEqual(events[0][1], events[1][1])
        self.assertFalse(events[1][2]['ok'])
        self.assertEqual(sum(row['type'] == 'tool_started' for row in self.ledger.events()), 1)
        self.assertEqual(self.ledger.snapshot('M1')['requests'], 1)

    def test_manifest_preserves_inline_and_offers_exclusive_file_descriptor(self):
        gw = self.setup_host()
        status, value = self.post(gw, {}, path='/M/worker/manifest')
        self.assertEqual(status, 200)
        for item in value['tools']:
            if item['name'] in ('check', 'submit'):
                schema = item['parameters']
                self.assertEqual(schema['oneOf'], [{'required': ['candidate']}, {'required': ['candidate_file']}])
                self.assertFalse(schema['additionalProperties'])
                self.assertEqual(schema['properties']['candidate_file']['required'], ['path', 'sha256', 'size'])


if __name__ == '__main__':
    unittest.main()

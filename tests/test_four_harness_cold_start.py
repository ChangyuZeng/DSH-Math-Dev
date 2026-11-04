"""Cold information is a byte allowlist; no model calls or target search."""
import os
import io
import json
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from unittest.mock import patch
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'
sys.path.insert(0, str(SOURCE))
from cold_start import (ColdStartError, canonical_inputs, input_manifest, material_specs,
                        prepare, write_materials)
from tools import CommonTools, ToolLimits
from gateway import Gateway, FixtureTransport
from ledger import Ledger, GateError, SMOKE_BUDGET


class ColdStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_four_empty_states_share_only_task_and_submission(self):
        result = prepare(self.root / 'campaign')
        self.assertEqual(result['model_requests'], 0)
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['os_isolation_qualified'])
        self.assertEqual({a['materials_sha256'] for a in result['arms'].values()}, {input_manifest()['materials_sha256']})
        for arm in 'CXRM':
            for state in ('home', 'workspace', 'cache', 'state', 'tmp'):
                self.assertEqual(list((self.root / 'campaign' / arm / state).iterdir()), [])
        with self.assertRaises(FileExistsError):
            prepare(self.root / 'campaign')
        text = '\n'.join(data.decode() for data in canonical_inputs().values())
        for excluded in ('1179648', '9/8', 'thread', 'baseline', 'route', '65536'):
            self.assertNotIn(excluded, text)
        self.assertNotEqual(input_manifest()['materials_sha256'], input_manifest('smoke')['materials_sha256'])
        smoke = b'\n'.join(canonical_inputs('smoke').values())
        self.assertNotIn(b'1179649', smoke)
        self.assertNotIn(b'1048576', smoke)

    def test_extra_material_or_changed_bytes_are_rejected(self):
        directory = self.root / 'materials'
        write_materials(directory)
        old = directory / 'old-answer.txt'
        old.write_text('fixture prior run')
        with self.assertRaises(ColdStartError):
            material_specs(directory)
        # Fresh identity, not resetting the contaminated material directory.
        clean = self.root / 'second-materials'
        write_materials(clean)
        path = clean / 'task.txt'
        path.chmod(0o600)
        path.write_text(path.read_text() + 'Use a previously solved construction.')
        with self.assertRaises(ColdStartError):
            material_specs(clean)

    def test_symlink_and_hardlink_materials_are_rejected(self):
        actual = self.root / 'outside'
        actual.mkdir()
        (self.root / 'link').symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ColdStartError):
            prepare(self.root / 'link' / 'new')
        directory = self.root / 'materials'
        write_materials(directory)
        os.link(directory / 'task.txt', self.root / 'extra-link')
        with self.assertRaises(ColdStartError):
            material_specs(directory)

    def test_smoke_gateway_rejects_target_or_external_material_before_dispatch(self):
        for scope, accepted in [('formal', False), ('smoke', True)]:
            case = self.root / scope
            case.mkdir()
            specs = write_materials(case / 'materials', scope)
            ledger = Ledger(case / 'ledger.sqlite')
            ledger.create_campaign('smoke', max_requests=2, max_tokens=32768, scope='smoke')
            ledger.create_run('C1', 'smoke', 'C', budget=SMOKE_BUDGET)
            host = CommonTools('C1', case / 'tools', specs,
                               limits=ToolLimits(timeout_seconds=10, artifact_bytes=16 * 1024 * 1024))
            args = {'transport': FixtureTransport([]), 'bindings': {'secret': {'run_id': 'C1', 'arm': 'C', 'role': 'agent'}},
                    'tools': {'C1': host}}
            if accepted:
                with Gateway(ledger, **args) as gateway:
                    gateway.check_cold_materials('C1')
            else:
                with self.assertRaises(GateError) as caught:
                    Gateway(ledger, **args)
                self.assertEqual(caught.exception.code, 'COLD_INPUT')
            self.assertEqual(ledger.snapshot('C1')['requests'], 0)

    def smoke_host(self, suffix, *, host_id='C1', limits=None):
        root = self.root / suffix
        root.mkdir()
        ledger = Ledger(root / 'ledger.sqlite')
        ledger.create_campaign('smoke', max_requests=2, max_tokens=32768, scope='smoke')
        ledger.create_run('C1', 'smoke', 'C', budget=SMOKE_BUDGET)
        host = CommonTools(host_id, root / 'tools', write_materials(root / 'materials', 'smoke'),
                           limits=limits or ToolLimits(timeout_seconds=10, artifact_bytes=16 * 1024 * 1024))
        kwargs = {'transport': FixtureTransport([]), 'bindings': {'secret': {'run_id': 'C1', 'arm': 'C', 'role': 'agent'}},
                  'tools': {'C1': host}}
        return ledger, host, kwargs

    def test_cold_gateway_binds_pristine_run_and_owns_accounting(self):
        for suffix, expected in [('wrong-run', 'COLD_RUN_BINDING'), ('used', 'COLD_STATE'), ('limits', 'TOOL_BUDGET')]:
            ledger, host, kwargs = self.smoke_host(suffix, host_id='old' if suffix == 'wrong-run' else 'C1',
                                                  limits=ToolLimits() if suffix == 'limits' else None)
            if suffix == 'used':
                self.assertTrue(host.call('workspace.write', {'path': 'answer.txt', 'text': 'prior-canary'})['ok'])
            with self.assertRaises(GateError) as caught:
                Gateway(ledger, **kwargs)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(ledger.snapshot('C1')['resource_observations']['tool_count'], 0)
        ledger, host, kwargs = self.smoke_host('bound')
        old_callback_events = []
        host.accounting = old_callback_events.append
        with Gateway(ledger, **kwargs) as gateway:
            self.assertTrue(host.call('materials.list', {})['ok'])
            self.assertEqual(old_callback_events, [])
            self.assertEqual(ledger.snapshot('C1')['resource_observations']['tool_count'], 1)
            host.accounting = None
            with self.assertRaises(GateError) as caught:
                gateway.check_cold_materials('C1')
            self.assertEqual(caught.exception.code, 'COLD_RUN_BINDING')

    def test_smoke_never_advertises_or_dispatches_target_checker(self):
        from mcp import serve
        ledger, host, kwargs = self.smoke_host('smoke-tools')
        kwargs['allowed_tools'] = {'C1': {'check', 'submit', 'mcp__common__check'}}
        with Gateway(ledger, **kwargs) as gateway:
            base = gateway.url + '/C/agent'
            def post(suffix, body):
                request = Request(base + suffix, json.dumps(body).encode(),
                                  {'Authorization': 'Bearer secret', 'Content-Type': 'application/json'})
                try:
                    response = build_opener(ProxyHandler({})).open(request, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response)
            status, manifest = post('/manifest', {})
            self.assertEqual(status, 200)
            self.assertEqual(manifest['scope'], 'smoke')
            expected = {'materials_list', 'materials_read', 'workspace_read', 'workspace_write', 'compute'}
            self.assertEqual({tool['name'] for tool in manifest['tools']}, expected)
            stream = io.StringIO()
            serve(base + '/tools', 'secret', io.StringIO('{"id":1,"method":"tools/list"}\n'), stream)
            self.assertEqual({tool['name'] for tool in json.loads(stream.getvalue())['result']['tools']}, expected)
            with patch.object(host, 'call', side_effect=AssertionError('Target tool reached dispatch')):
                for name in ('check', 'submit'):
                    status, result = post('/tools', {'name': name, 'arguments': {'candidate': {'pairs': []}}})
                    self.assertEqual((status, result['error']['code']), (409, 'TOOL_NOT_ALLOWED'))
                for name in ('check', 'submit', 'mcp__common__check'):
                    status, result = post('/responses', {'model': 'gpt-6-astra', 'reasoning': {'effort': 'max'},
                        'max_output_tokens': 2048, 'input': '2+3', 'tools': [{'type': 'function', 'name': name}]})
                    self.assertEqual((status, result['error']['code']), (409, 'TOOL_NOT_ALLOWED'))
            self.assertEqual(ledger.snapshot('C1')['requests'], 0)
            self.assertEqual(ledger.snapshot('C1')['resource_observations']['tool_count'], 0)
            self.assertEqual(kwargs['transport'].requests, [])

    def test_common_tools_cannot_read_prior_runs_or_operator_baseline(self):
        specs = write_materials(self.root / 'materials')
        old = self.root / 'previous-run'
        old.mkdir()
        (old / 'answer.txt').write_text('prior-result-canary')
        operator = self.root / 'operator-baseline.txt'
        operator.write_text('operator-only-canary')
        host = CommonTools('new-run', self.root / 'tools', specs)
        listed = host.call('materials.list', {})
        self.assertTrue(listed['ok'])
        self.assertEqual({entry['id'] for entry in listed['result']['materials']}, {'task.txt', 'submission.txt'})
        self.assertFalse(host.call('materials.read', {'id': 'operator-baseline', 'offset': 0, 'length': 100})['ok'])
        self.assertFalse(host.call('workspace.read', {'path': '../previous-run/answer.txt'})['ok'])
        self.assertTrue(host.call('workspace.write', {'path': 'current.txt', 'text': 'same-run-memory'})['ok'])
        self.assertTrue(host.call('workspace.read', {'path': 'current.txt'})['ok'])
        other = CommonTools('other-run', self.root / 'other-tools', specs)
        self.assertFalse(other.call('workspace.read', {'path': 'current.txt'})['ok'])


if __name__ == '__main__':
    unittest.main()

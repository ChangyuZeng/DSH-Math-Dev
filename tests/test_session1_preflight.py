"""Credential failures must stop terminal preparation before durable run creation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('session1_prepare', SOURCE / 'evaluation/session1/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
from ledger import GateError


class CredentialAdmission(unittest.TestCase):
    def test_expired_real_auth_creates_no_project_or_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve()
            with patch('transport.load_host_credential', side_effect=GateError('AUTH_EXPIRED', 'expired fixture')):
                with self.assertRaises(GateError) as caught:
                    prepare.prepare(output, 'real', 'S1')
            self.assertEqual(caught.exception.code, 'AUTH_EXPIRED')
            self.assertFalse((output / 'store').exists())
            self.assertEqual(__import__('json').loads((output / 'credential-preflight.json').read_text())['ready'], False)

    def test_fixture_never_reads_host_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('transport.load_host_credential', side_effect=AssertionError('fixture read credentials')):
                packet = prepare.prepare(Path(directory).resolve(), 'fixture', 'S1')
            self.assertEqual(packet['spec']['execution']['checker']['id'], 'workflow_certificate_v2_S1')


if __name__ == '__main__':
    unittest.main()

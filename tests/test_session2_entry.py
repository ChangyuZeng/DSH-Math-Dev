"""Include Session2 terminal contract checks in the standard project test suite."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'evaluation/session2/test_session2.py'
_spec = importlib.util.spec_from_file_location('session2_entry_checks', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
Session2Tests = _module.Session2Tests

"""Shared research readers must work with a genuinely read-only filesystem policy."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import backend

ROOT = Path(__file__).resolve().parents[1]


class ReadOnlyLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-math-readonly-lock-")
        self.root = Path(self.temp.name).resolve() / "data"
        self.projects = backend.Projects(self.root)
        self.project = self.projects.create("readonly-fixture", {
            "title": "Read-only lock acceptance", "statement": "Synthetic fixture; no mathematical claim.",
            "quantifiers": "One fixed fixture", "domain": "Synthetic fixture records", "conventions": [],
            "target_type": "engineering_fixture", "completion_criteria": ["Read without filesystem writes"],
            "sources": ["Local synthetic engineering fixture"], "knowledge_status": "synthetic_fixture",
            "parent": None, "relation": "",
        })
        self.addCleanup(self.temp.cleanup)

    def hashes(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def test_shared_reader_opens_existing_lock_read_only_and_preserves_bytes(self):
        before = self.hashes()
        original = os.open
        seen = []

        def checked_open(path, flags, *args, **kwargs):
            if Path(path).name == ".writer.lock":
                seen.append(flags)
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                self.assertFalse(flags & (os.O_CREAT | os.O_TRUNC))
            return original(path, flags, *args, **kwargs)

        with patch("os.open", checked_open):
            view = self.projects.overview(self.project["id"])
        self.assertEqual(view["project"]["schema_version"], 4)
        self.assertTrue(view["snapshot"]["usable"])
        self.assertTrue(seen)
        self.assertEqual(before, self.hashes())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Requires POSIX FIFO support")
    def test_fifo_lock_is_rejected_without_blocking_before_inode_check(self):
        campaign = Path(self.temp.name).resolve() / "fifo-fixture"
        campaign.mkdir()
        os.mkfifo(campaign / ".writer.lock", 0o600)
        result = subprocess.run([
            sys.executable, "-B", "-c",
            "from backend import ResearchStore; import sys; ResearchStore(sys.argv[1], writable=False)",
            str(campaign),
        ], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe lock inode", result.stderr)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("sandbox-exec"), "Requires macOS Seatbelt")
    def test_actual_seatbelt_readonly_policy_allows_overview_without_any_writes(self):
        before = self.hashes()
        result = subprocess.run([
            "/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny file-write*)",
            sys.executable, "-B", str(ROOT / "backend.py"), str(self.root),
        ], input=json.dumps({"action": "overview", "args": {"project_id": self.project["id"]}}),
            text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertEqual(before, self.hashes())


if __name__ == "__main__":
    unittest.main()

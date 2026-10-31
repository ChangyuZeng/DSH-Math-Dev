import hashlib
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import backend
from checkers.registry import get


class Contracts(unittest.TestCase):
    def test_cards_checkers_and_scientific_boundaries(self):
        expected = {"OP-000006": "ring15_d20_v1", "OP-000007": "q7_e305_v1", "OP-000008": "grid75_p149_v1"}
        for op_id, checker_id in expected.items():
            contract = json.loads((HERE / "contracts" / (op_id + ".json")).read_bytes())
            self.assertFalse(contract["execution_authorized"])
            self.assertIsNone(contract["model"])
            self.assertIsNone(contract["effort"])
            self.assertEqual(contract["curation_state"], "needs_review")
            self.assertEqual(contract["proposed_limits_not_authorized"]["wall_seconds"], 2700)
            self.assertEqual(contract["proposed_limits_not_authorized"]["requests"], 24)
            self.assertEqual(contract["proposed_limits_not_authorized"]["tools"], 80)
            card = HERE / contract["problem_card"]["path"]
            self.assertEqual(hashlib.sha256(card.read_bytes()).hexdigest(), contract["problem_card"]["sha256"])
            checker = HERE / contract["checker"]["path"]
            self.assertEqual(hashlib.sha256(checker.read_bytes()).hexdigest(), contract["checker"]["sha256"])
            spec = get(checker_id)
            for key, value in spec["module"].PROBLEM_FIELDS.items():
                self.assertEqual(contract["problem"][key], value)
            self.assertIn(contract["formal_scope"], spec["scopes"])
            self.assertIsNone(contract["review"]["R1"])
            self.assertIsNone(contract["review"]["R2"])
        q7 = json.loads((HERE / "contracts/OP-000007.json").read_bytes())
        self.assertTrue(q7["local_goal_gate"]["required_before_model_run"])
        self.assertFalse(q7["local_goal_gate"]["approved"])
        self.assertEqual(q7["local_obligations"], [])


if __name__ == "__main__":
    unittest.main()

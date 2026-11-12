"""Independent small-domain oracles for the new Session2 finite contracts."""
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('session2_certificates', ROOT / 'evaluation/optimization-v1/certificates.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
sys.path.insert(0, str(ROOT / 'runtime/p2c'))
from checkers import workflow_certificate_v2 as fixed


class Session2Certificates(unittest.TestCase):
    def test_difference_set_exhaustive_oracle(self):
        # Count representations per residue independently, without a shared histogram.
        for modulus in (3, 7, 9):
            for size in (2, 3):
                task = {'kind': 'cyclic_difference_set', 'parameters': {'v': modulus, 'k': size, 'lambda': 1}}
                for points in itertools.combinations(range(modulus), size):
                    expected = all(sum((a-b) % modulus == r for a, b in itertools.permutations(points, 2)) == 1
                                   for r in range(1, modulus))
                    self.assertEqual(c.check(task, {'values': list(points)})['target_met'], expected)

    def test_tetrahedron_exhaustive_oracle(self):
        triples = list(itertools.combinations(range(5), 3))
        for mask in range(1 << len(triples)):
            values = [(mask >> i) & 1 for i in range(len(triples))]
            # Each 4-set has exactly four 3-subsets; check via set containment.
            edges = [set(t) for t, bit in zip(triples, values) if bit]
            valid = all(sum(edge <= set(q) for edge in edges) < 4 for q in itertools.combinations(range(5), 4))
            for bound in (0, 5, 10):
                task = {'kind': 'turan_k4_3', 'parameters': {'n': 5, 'min_edges': bound}}
                self.assertEqual(c.check(task, {'values': values})['target_met'], valid and sum(values) >= bound)

    def test_fixed_contracts_and_malformed(self):
        for task_id, count in (('D8', 33), ('D9', 4060)):
            contract = fixed.contract(task_id)
            self.assertEqual(contract['task']['split'], 'session2_dev')
            scope = fixed.scope_for(task_id)
            for raw in (b'{', b'{"values":[],"values":[]}', b'{"values":[NaN]}'):
                self.assertEqual(fixed.evaluate(raw, scope)['error_code'], 'invalid_input')
            for values in ([0]*(count-1), [True]+[0]*(count-1), [0.0]+[0]*(count-1), [2**8192]+[0]*(count-1)):
                result = fixed.evaluate(json.dumps({'values': values}).encode(), scope)
                self.assertEqual(result['verdict'], 'fail')
                self.assertFalse(result['checks']['legal'])
        self.assertEqual(fixed.contract('D8')['task']['parameters'], {'v': 1057, 'k': 33, 'lambda': 1})
        self.assertEqual(fixed.contract('D9')['task']['parameters'], {'n': 30, 'min_edges': 2350})


if __name__ == '__main__':
    unittest.main()

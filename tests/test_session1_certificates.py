"""Deterministic Session1 acceptance; construction fixtures stay in tests only."""
import ast
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'evaluation/optimization-v1/certificates.py'
spec = importlib.util.spec_from_file_location('session1_public_certificates', PUBLIC)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
sys.path.insert(0, str(ROOT / 'runtime/p2c'))
from checkers import workflow_certificate_v2 as fixed

# Pre-change task-card digests, including the original reserve R1 CRT card.
LEGACY_HASHES = {
    'D1': '1b56dfe619149c70b59069663839f9a4a9bad8f7221f3e0bb7998056023207a8',
    'D2': '085379a56bb2c0fccc3656190ae513a673b368c637c459854a8036051bba2913',
    'D3': '781787faf1d16a6c365afc354d72db6f355f49da7b0ea0922516dcc00d6b8f42',
    'D4': '11d220cb0b2f11233c83d67c05d304b317faf479c33190fb7b3f316055e623d7',
    'D5': '3f4c5693cc5d47503126f507df7f42ecb16fba0b463b2265bd472fe4405fd119',
    'D6': 'eefd3ae767a120d47cf7c8229152bdb079faf53aff91dbd87ce253fca84a7156',
    'H1': '4430b4b6335b0e81a302bb04b5089b48a81bb678b8a4192fbd68d7bf1eee909d',
    'H2': '9a90cb3b0489bfffebd6b1e729697bb2850b11e5b5c687008b126fd71ca71111',
    'H3': 'dbf8560d5befd18806438ce690e9a1ceea58f370923c2a9d086c24972af6ed64',
    'R1': 'e2c66037625a6d9cdbaf6c307ef344f643474d6cd9c6132a86bf385875f01963',
    'R2': '3b342e8d4cb67bd5422696f74c87f0c0944bc212f4ebd2eb488d857872d1e10a',
    'R3': 'de73c268c18c2b959a8b6e7762c417372b693dbfd6b6c09543da7360b5f31d14',
}


def paley17():
    # Hidden acceptance fixture: +/-1, +/-2, +/-4, +/-8 modulo 17.
    residues = {1, 2, 4, 8, 9, 13, 15, 16}
    return [int((j-i) % 17 in residues) for i in range(17) for j in range(i+1, 17)]


def toy(n, size):
    return {'kind': 'ramsey_coloring', 'parameters': {'n': n, 'clique_size': size}}


class Session1Certificates(unittest.TestCase):
    def test_cards_and_legacy_hash_bytes_are_stable(self):
        cards = c.cards()
        self.assertEqual(len(cards), 16)
        legacy = cards[:12]
        self.assertEqual({t['id']: t['sha256'] for t in legacy}, LEGACY_HASHES)
        for card in cards:
            bare = dict(card)
            expected = bare.pop('sha256')
            self.assertEqual(hashlib.sha256(c.canonical(bare).encode()).hexdigest(), expected)
        additions = {t['id']: t for t in cards[12:14]}
        self.assertEqual(set(additions), {'D7', 'S1'})
        self.assertEqual(additions['D7']['parameters'], {'n': 42, 'clique_size': 5})
        self.assertEqual(additions['S1']['parameters'], {'n': 17, 'clique_size': 4})
        self.assertEqual(additions['S1']['split'], 'session1_acceptance')
        self.assertIn('no extremal or optimality claim', additions['D7']['statement'])
        self.assertEqual(hashlib.sha256(PUBLIC.read_bytes()).hexdigest(), fixed.CERTIFICATE_SOURCE_SHA256)

    def test_positive_r1_fixture_and_color_swap(self):
        card = fixed.contract('S1')['task']
        self.assertEqual(card['kind'], 'ramsey_coloring')
        values = paley17()
        self.assertEqual(len(values), 136)
        for colors in (values, [1-x for x in values]):
            result = c.check(card, {'values': colors})
            self.assertEqual((result['H'], result['D'], result['target_met']), (1, 1, True))

    def test_negative_both_colors_and_mutated_fixture(self):
        for task_id, edges in (('S1', 136), ('D7', 861)):
            card = fixed.contract(task_id)['task']
            for color in (0, 1):
                self.assertFalse(c.check(card, {'values': [color]*edges})['target_met'])
        values = paley17()
        # Force a K4 at the highest vertex indices, retaining the edge encoding.
        for color in (0, 1):
            mutated = [color if i >= 13 else values[index]
                       for index, (i, j) in enumerate(itertools.combinations(range(17), 2))]
            self.assertFalse(c.check(fixed.contract('S1')['task'], {'values': mutated})['target_met'])

    def test_boundary_clique_size_and_small_exhaustive_oracle(self):
        # Independent tuple enumeration verifies all 1024 colorings on five vertices.
        n = 5
        edges = list(itertools.combinations(range(n), 2))
        for bits in range(1 << len(edges)):
            values = [(bits >> index) & 1 for index in range(len(edges))]
            colors = dict(zip(edges, values))
            for size in (3, 4, 5):
                expected = not any(len({colors[e] for e in itertools.combinations(vertices, 2)}) == 1
                                   for vertices in itertools.combinations(range(n), size))
                self.assertEqual(c.check(toy(n, size), {'values': values})['target_met'], expected)
        # No k-clique when n<k; exactly k vertices of either color must fail.
        for color in (0, 1):
            self.assertTrue(c.check(toy(3, 4), {'values': [color]*3})['target_met'])
            self.assertFalse(c.check(toy(4, 4), {'values': [color]*6})['target_met'])

    def test_malformed_domains_and_edge_count_boundaries(self):
        for task_id, count in (('S1', 136), ('D7', 861)):
            card = fixed.contract(task_id)['task']
            malformed = [None, [], {}, {'values': ()}, {'values': [], 'extra': 1},
                         {'values': [0]*(count-1)}, {'values': [0]*(count+1)}]
            malformed += [{'values': [value] + [0]*(count-1)}
                          for value in (True, False, 0.0, '0', None, -1, 2, 1 << 8192)]
            for candidate in malformed:
                with self.subTest(task=task_id, candidate_type=type(candidate).__name__):
                    with self.assertRaises(ValueError):
                        c.check(card, candidate)

    def fresh_registered(self, task_id, raw):
        # -I and an unrelated cwd prevent accidental reliance on test imports.
        program = '''import json,sys
sys.path.insert(0, sys.argv[1])
from checkers.registry import get
from checkers import workflow_certificate_v2 as fixed
task_id = sys.argv[2]
spec = get(fixed.PREFIX + task_id)
assert spec is not None
assert spec['workflow_contract']['task']['kind'] == 'ramsey_coloring'
assert len(spec['scopes']) == 1
scope = next(iter(spec['scopes']))
result = spec['module'].evaluate(sys.stdin.buffer.read(), scope)
print(json.dumps(result, sort_keys=True))
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', program,
                                     str(ROOT / 'runtime/p2c'), task_id], input=raw,
                                    cwd=directory, env={}, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout)

    def test_registered_checker_in_fresh_isolated_process(self):
        cases = [('S1', json.dumps({'values': paley17()}).encode(), 'pass', True),
                 ('S1', json.dumps({'values': [0]*136}).encode(), 'fail', True),
                 ('D7', json.dumps({'values': [1]*861}).encode(), 'fail', True),
                 ('S1', json.dumps({'values': paley17()[:-1]}).encode(), 'fail', False),
                 ('D7', json.dumps({'values': [2]+[0]*860}).encode(), 'fail', False)]
        for task_id, raw, verdict, legal in cases:
            with self.subTest(task=task_id, verdict=verdict, legal=legal):
                result = self.fresh_registered(task_id, raw)
                self.assertEqual(result['verdict'], verdict)
                self.assertEqual(result['checks']['legal'], legal)
                self.assertEqual(result['values']['task_sha256'], fixed.contract(task_id)['task']['sha256'])
        for raw in (b'{', b'{"values":[],"values":[]}', b'{"values":[NaN]}'):
            result = self.fresh_registered('S1', raw)
            self.assertEqual((result['verdict'], result['error_code']), ('error', 'invalid_input'))

    def test_runtime_and_registry_allow_exact_task_ids_only(self):
        from checkers.registry import get
        tree = ast.parse((ROOT / 'workflow_runtime.py').read_text())
        patterns = [node.args[0].value for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'fullmatch' and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and str(node.args[0].value).startswith('workflow_certificate_v2_')]
        self.assertEqual(len(patterns), 1)
        for task_id in fixed.TASK_IDS:
            self.assertIsNotNone(re.fullmatch(patterns[0], fixed.PREFIX + task_id))
            self.assertIsNotNone(get(fixed.PREFIX + task_id))
        for task_id in ('D0', 'D10', 'D17', 'H0', 'H4', 'H6', 'R0', 'R1', 'R2', 'R6', 'R11', 'S0', 'S2', 'X1', 'D7x'):
            checker_id = fixed.PREFIX + task_id
            self.assertIsNone(re.fullmatch(patterns[0], checker_id))
            self.assertIsNone(get(checker_id))
            with self.assertRaises(ValueError):
                fixed.contract(task_id)
            self.assertEqual(fixed.evaluate(b'{}', fixed.scope_for(task_id))['error_code'], 'unsupported')


if __name__ == '__main__':
    unittest.main()

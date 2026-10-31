"""Fixed checker allowlist. No caller-supplied modules or specifications."""

from checkers import primitive_pythagorean_v1 as primitive
from checkers import grid75_p149_v1 as grid
from checkers import q7_e305_v1 as q7
from checkers import ring15_d20_v1 as ring


def _scopes(module, obligations):
    return {scope: {**spec, "obligation_fields": obligations(scope)} for scope, spec in module.SCOPES.items()}


SPECS = {
    primitive.CHECKER_ID: {"module": primitive, "path": "primitive_pythagorean_v1.py",
                           "scopes": {primitive.SCOPE: {"id": "formal", "checks": ["pythagorean", "primitive"],
                                                        "values": ["a", "b", "c", "lhs", "rhs", "gcd"], "timeout": 5,
                                                        "obligation_fields": primitive.OBLIGATION_FIELDS}}},
    ring.CHECKER_ID: {"module": ring, "path": "ring15_d20_v1.py",
                      "scopes": _scopes(ring, lambda _scope: ring.OBLIGATION_FIELDS)},
    q7.CHECKER_ID: {"module": q7, "path": "q7_e305_v1.py", "scopes": _scopes(q7, q7.obligation_fields)},
    grid.CHECKER_ID: {"module": grid, "path": "grid75_p149_v1.py", "scopes": _scopes(grid, grid.obligation_fields)},
}


def get(checker_id, scope=None):
    if checker_id not in SPECS:
        return None
    spec = SPECS[checker_id]
    if scope is None:
        return spec
    return spec["scopes"].get(scope)

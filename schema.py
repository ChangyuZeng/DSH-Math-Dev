import importlib.util
from pathlib import Path

_BASE = Path(__file__).resolve().parent / "p2c_schema.py"
if not _BASE.exists():
    _BASE = Path(__file__).resolve().parent / "runtime/p2c/schema.py"
_spec = importlib.util.spec_from_file_location("p2c_schema", _BASE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
for _name in dir(_base):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_base, _name)
SCHEMA_VERSION = 4


def validate_data(kind, data):
    if kind != "Problem":
        return _base.validate_data(kind, data)
    fields(data, "title statement quantifiers domain conventions target_type parent relation completion_criteria sources knowledge_status")
    for name in ("title", "statement", "quantifiers", "domain"):
        string(data[name])
    strings(data["conventions"])
    strings(data["completion_criteria"], True)
    strings(data["sources"], True)
    choice(data["target_type"], "construct prove_or_disprove exploratory engineering_fixture")
    choice(data["knowledge_status"], "unverified_open known_result synthetic_fixture")
    string(data["relation"], empty=data["parent"] is None)
    if data["parent"] is not None:
        ref(data["parent"])
    require(data["target_type"] != "engineering_fixture" or data["knowledge_status"] != "unverified_open",
            "INVALID_SCHEMA", "An open research target is not an engineering fixture")

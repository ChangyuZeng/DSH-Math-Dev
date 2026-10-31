import json
from pathlib import Path
import sys

from research_store import ResearchStore

if len(sys.argv) != 3 or sys.argv[2] != "replay":
    raise SystemExit("Usage: python3 -B runtime/cli.py research replay")
with ResearchStore(Path(sys.argv[1])) as store:
    value = store.snapshot()
    if not value["usable"]:
        raise SystemExit("Unusable research evidence")
    print(json.dumps(value, ensure_ascii=False))

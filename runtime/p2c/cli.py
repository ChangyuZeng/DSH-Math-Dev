#!/usr/bin/env python3
"""Offline host entry. No model or DSH runtime is started by any command."""

import argparse
import json
from pathlib import Path
import sys

from research_store import ResearchStore
from schema import StoreError, decode, fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    submit = commands.add_parser("commit")
    submit.add_argument("request_file")
    archive = commands.add_parser("archive")
    archive.add_argument("staging_relative")
    commands.add_parser("replay")
    get = commands.add_parser("get")
    get.add_argument("id")
    get.add_argument("--rev", type=int)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("operation_id")
    commands.add_parser("recover-tail")
    for name in ("review-preview", "review", "control", "read", "check"):
        command = commands.add_parser(name)
        command.add_argument("request_file")
    export = commands.add_parser("export")
    export.add_argument("destination")
    export.add_argument("--expected-seq", type=int)
    args = parser.parse_args()
    writable = args.command in ("init", "commit", "archive", "recover-tail", "review", "control", "check")
    try:
        with ResearchStore(args.campaign, writable=writable, create=args.command == "init") as store:
            if args.command == "export":
                from exporting import export_bundle
                result = export_bundle(store, args.destination, args.expected_seq)
            elif args.command in ("init", "replay"):
                result = store.snapshot()
            elif args.command == "commit":
                request = decode(Path(args.request_file).read_bytes())
                fields(request, "operation_id changes")
                result = store.commit(**request)
            elif args.command in ("review-preview", "review", "control", "read", "check"):
                request = decode(Path(args.request_file).read_bytes())
                if args.command == "check":
                    from checking import run_check
                    fields(request, "operation_id checker_id target witness")
                    result = run_check(store, **request)
                elif args.command == "review-preview":
                    fields(request, "target candidate")
                    result = store.review_preview(**request)
                elif args.command == "review":
                    fields(request, "operation_id review_id target candidate verdict scope findings basis_hash")
                    result = store.review(**request)
                elif args.command == "control":
                    fields(request, "operation_id control")
                    result = store.control(**request)
                else:
                    fields(request, "kind state text object_id rev")
                    result = store.research_read(**request)
            elif args.command == "archive":
                result = store.archive(args.staging_relative)
            elif args.command == "get":
                result = store.get(args.id, args.rev)
            elif args.command == "receipt":
                result = store.receipt(args.operation_id)
            else:
                result = store.recover_tail()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("usable") is False else 0
    except (StoreError, OSError, ValueError) as error:
        print(json.dumps({"error": getattr(error, "code", type(error).__name__),
                          "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""One-shot fake model child for the original verification subprocess path.

This is explicitly not Codex. Real FastMCP and the original R verifier tools
validate and write the response; no agent/model iteration is implemented.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from four_harness_mcp import COMMON, NATIVE, encoded, post
from four_harness_mcp_fixture import transport


async def main():
    if os.environ.get("FOUR_HARNESS_OFFLINE_FIXTURE") != "1" or os.environ.get("FOUR_HARNESS_ROLE") != "verifier":
        raise RuntimeError("Fake child requires its isolated offline verifier environment")
    args = sys.argv[1:]
    if args[:1] != ["exec"] or args[args.index("-m") + 1] != "gpt-6-astra":
        raise RuntimeError("Original subprocess did not pin the model")
    if "model_reasoning_effort=max" not in args or 'sandbox_mode="read-only"' not in args:
        raise RuntimeError("Original subprocess did not pin max effort and sandbox")
    if any("dangerously" in value for value in args):
        raise RuntimeError("Unsafe upstream launch flag")
    run_id = re.match(r"Run_id: ([A-Za-z0-9_-]+)\. ", args[-1]).group(1)
    parent = os.environ["FOUR_HARNESS_PARENT_REQUEST_ID"]
    native_parent = json.loads(os.environ["FOUR_HARNESS_PARENT_NATIVE_METADATA"])
    metadata = {"thread_id": "verifier-" + run_id, "turn_id": "verifier-turn-" + run_id,
                "request_kind": "turn", "agent_name": "/root/verifier",
                "parent_thread_id": native_parent["thread_id"], "parent_turn_id": native_parent["turn_id"]}
    response = post("verifier", "v1/responses", {
        "model": "gpt-6-astra", "reasoning": {"effort": "max"},
        "max_output_tokens": int(os.environ["FOUR_HARNESS_MAX_OUTPUT"]),
        "input": [{"role": "user", "content": args[-1]}],
        "client_metadata": {"x-codex-turn-metadata": encoded(metadata).decode()},
    }, metadata=metadata, parent=parent)
    report = json.loads(response["output"][0]["content"][0]["text"])
    async with transport("verification") as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == COMMON | NATIVE["verification"], names

        async def call(name, arguments, call_id, error=False):
            result = await client.call_tool(name, arguments, meta={"callId": call_id, "x-codex-turn-metadata": metadata})
            assert result.isError is error, result.model_dump_json()
            if error:
                return result
            return result.structuredContent or json.loads(result.content[0].text)

        await call("memory_init", {"run_id": run_id, "meta": {"fixture_only": True}}, "verify_init")
        await call("memory_append", {"run_id": run_id, "channel": "statement_checks", "record": {"text": "fixture statement check"}}, "verify_statement")
        await call("memory_append", {"run_id": run_id, "channel": "reference_checks", "record": {"text": "frozen fixture material"}}, "verify_reference")
        query = await call("memory_query", {"run_id": run_id, "channel": "statement_checks", "contains": "fixture"}, "verify_query")
        assert query["count"] == 1, query
        valid = await call("validate_verification_output", {"payload": report}, "verify_validate")
        assert valid["valid"] is True, valid
        invalid = {**report, "repair_hints": "inconsistent with a correct verdict"}
        await call("write_verification_output", {"run_id": run_id, "payload": invalid}, "verify_reject", error=True)
        assert not (ROOT / "agents/verification/results" / run_id / "verification.json").exists()
        written = await call("write_verification_output", {"run_id": run_id, "payload": report}, "verify_write")
        assert written["status"] == "ok", written
    print(json.dumps({"mode": "fake_model_original_verification_mcp", "run_id": run_id, "parent_request_id": parent,
                      "native_codex_qualified": False, "live_ready": False}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

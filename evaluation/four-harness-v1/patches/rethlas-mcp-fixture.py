"""Offline protocol fixture driving the real original MCP apps, not an agent loop."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import cache
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from four_harness_mcp import COMMON, NATIVE, encoded, post
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT_META = {"thread_id": "r-root-thread", "turn_id": "r-root-turn", "request_kind": "turn", "agent_name": "/root"}
CHILD_META = {"thread_id": "r-subgoal-thread", "turn_id": "r-subgoal-turn", "request_kind": "turn", "agent_name": "/root/subgoal-prover",
              "parent_thread_id": "r-root-thread", "parent_turn_id": "r-root-turn", "subagent_kind": "thread_spawn"}


def model_once(role, metadata, prompt):
    return post(role, "v1/responses", {
        "model": "gpt-6-astra", "reasoning": {"effort": "max"},
        "max_output_tokens": int(os.environ["FOUR_HARNESS_MAX_OUTPUT"]),
        "input": [{"role": "user", "content": prompt}],
        "client_metadata": {"x-codex-turn-metadata": encoded(metadata).decode()},
    }, metadata=metadata)


@cache
def tool_timeout_seconds(mode):
    role = {"generation": "generator", "verification": "verifier"}[mode]
    manifest = post(role, "manifest", {})
    seconds = manifest["budget"]["tool_timeout_seconds"]
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise RuntimeError("Invalid frozen MCP tool deadline")
    return seconds


@asynccontextmanager
async def transport(mode):
    # Use the pinned MCP SDK client; only the actual upstream server imports
    # FastMCP. This is a transport fixture, with no model or agent loop.
    parameters = StdioServerParameters(command=sys.executable, args=["-I", str(ROOT / "four_harness_mcp.py"), mode],
                                       env=dict(os.environ), cwd=str(ROOT / "agents" / mode))
    # The authenticated manifest supplies the same tool deadline used by the
    # common gateway (formal/offline 60s, smoke 10s). Import cost stays inside it.
    deadline = timedelta(seconds=tool_timeout_seconds(mode))
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams, read_timeout_seconds=deadline) as client:
            await client.initialize()
            yield client


async def tool(client, name, arguments, call_id, metadata=ROOT_META):
    result = await client.call_tool(name, arguments, meta={"callId": call_id, "x-codex-turn-metadata": metadata})
    if result.isError:
        raise RuntimeError("Original MCP fixture tool failed: " + result.model_dump_json())
    value = result.structuredContent or json.loads(result.content[0].text)
    return value["result"] if name in COMMON and value.get("ok") is True else value


async def main():
    if os.environ.get("FOUR_HARNESS_OFFLINE_FIXTURE") != "1":
        raise RuntimeError("No real model endpoint permitted")
    model_once("generator", ROOT_META, "Offline MCP accounting fixture; do not solve a research problem.")
    async with transport("generation") as client:
        names = {item.name for item in (await client.list_tools()).tools}
        assert names == COMMON | NATIVE["generation"], names
        if sys.argv[1:] == ["unknown-parent"]:
            unknown = {**CHILD_META, "thread_id": "unknown-child", "turn_id": "unknown-turn"}
            rejected = await client.call_tool("memory_init", {"problem_id": "never-created"},
                meta={"callId": "unknown_memory", "x-codex-turn-metadata": unknown})
            assert rejected.isError and "NATIVE_PARENT_UNKNOWN" in rejected.model_dump_json(), rejected.model_dump_json()
            assert not (ROOT / "agents/generation/memory/never-created").exists()
            print(json.dumps({"mode": "offline_unknown_parent_rejection", "rejected_before_original_tool": True,
                              "native_codex_qualified": False, "native_recursive_spawn_qualified": False, "live_ready": False}))
            return
        await tool(client, "memory_init", {"problem_id": "fixture", "meta": {"fixture_only": True}}, "gen_init")
        await tool(client, "memory_append", {"problem_id": "fixture", "channel": "subgoals", "record": {"text": "Fixture subgoal arithmetic 2+3=5"}}, "gen_append")
        await tool(client, "branch_update", {"problem_id": "fixture", "branch_id": "b1", "state": {"status": "pending", "subgoal": "arithmetic"}}, "gen_branch")
        search = await tool(client, "memory_search", {"problem_id": "fixture", "query": "arithmetic"}, "gen_search")
        assert search["count"] >= 2, search
        references = await tool(client, "search_arxiv_theorems", {"query": "fixture arithmetic", "num_results": 2}, "gen_references")
        assert references["count"] == 1 and references["results"][0]["theorem_id"].startswith("frozen-sha256:"), references
        await tool(client, "workspace_write", {"path": "fixture.txt", "text": "offline fixture"}, "gen_write")
        read = await tool(client, "workspace_read", {"path": "fixture.txt"}, "gen_read")
        assert read["text"] == "offline fixture", read
        computed = await client.call_tool("compute", {"code": "print(2 + 3)"},
            meta={"callId": "gen_compute", "x-codex-turn-metadata": ROOT_META})
        if computed.isError:
            assert "unsupported_sandbox" in computed.model_dump_json(), computed.model_dump_json()
            compute_capability = {"available": False, "blocker": "COMMON_COMPUTE_OS_CONTAINMENT_UNAVAILABLE"}
        else:
            assert "5" in computed.model_dump_json(), computed.model_dump_json()
            compute_capability = {"available": True}
        # This is a raw native-protocol descendant fixture. The upstream Rust
        # spawn is still gated by the unqualified native Codex tool surface.
        model_once("strategy", CHILD_META, "Offline child metadata fixture for the original subgoal-prover profile.")
        child = await tool(client, "memory_append", {"problem_id": "fixture", "channel": "proof_steps", "record": {"fixture_only": True, "text": "child tool receipt"}}, "child_append", CHILD_META)
        assert child["status"] == "ok"
        verified = await tool(client, "verify_proof_service", {"statement": "Offline arithmetic fixture", "proof": "Fixture text only"}, "gen_verify")
        assert verified["verdict"] == "correct", verified
    print(json.dumps({"mode": "offline_original_mcp_fixture", "generation_tools": sorted(names),
                      "bm25_results": search["count"], "frozen_reference_count": references["count"],
                      "original_verification_report": verified, "common_compute": compute_capability,
                      "tool_timeout_seconds": tool_timeout_seconds("generation"),
                      "native_recursive_spawn_qualified": False,
                      "native_codex_qualified": False, "live_ready": False}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

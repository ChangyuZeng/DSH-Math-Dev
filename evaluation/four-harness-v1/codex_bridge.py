"""Audited direct-tool adaptation around the original Codex execution loop.

The pinned full catalog copy changes two tool fields and removes only the exact
Apps/Plugins documentation for disabled capabilities. The remaining instructions,
model identity, effort, context metadata and native R coordination are preserved.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def prepare_catalog(source_root, destination, *, arm=None, empty_skills=False):
    """Copy the pinned full catalog with audited unavailable-capability removals."""
    from native import MODEL, _new_file, require, sha256, source_identity

    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    identity = source_identity(source_root, "C")
    source = source_root / "codex-rs/models-manager/models.json"
    require(not destination.exists() and not destination.is_relative_to(source_root),
            "CATALOG_COPY", "The adapted catalog must be a new file outside the upstream tree")
    original = json.loads(source.read_text(encoding="utf-8"))
    adapted = copy.deepcopy(original)
    matches = [model for model in adapted["models"] if model.get("slug") == MODEL["model"]]
    require(len(matches) == 1, "CATALOG_MODEL", "Pinned catalog must contain exactly one Astra model")
    model = matches[0]
    replacements = {"tool_mode": ("code_mode_only", "direct"), "apply_patch_tool_type": ("freeform", None)}
    for field, (before, after) in replacements.items():
        require(model.get(field) == before, "CATALOG_DRIFT", "Pinned native tool metadata changed")
        model[field] = after
    before_model = next(model for model in original["models"] if model["slug"] == MODEL["model"])
    instruction = model["model_messages"]["instructions_template"]
    anchor = "# Apps (Connectors)\n"
    require(instruction.count(anchor) == 1, "CATALOG_DRIFT", "Unavailable capability prompt anchor changed")
    prefix, suffix = instruction.split(anchor, 1)
    removed = anchor + suffix
    require(hashlib.sha256(removed.encode()).hexdigest() ==
            "4cb30a641de178608c00d489c9f6fbfb7ff13386f9e9043c388d43a5e1a9ea1e",
            "CATALOG_DRIFT", "Pinned Apps/Plugins instructions changed")
    removed_skills = ""
    if empty_skills:
        require(arm == "C" and prefix.count("# Using skills\n") == 1,
                "SKILL_CATALOG", "Only C with an empty skill catalog may omit skill documentation")
        prefix, skills_suffix = prefix.split("# Using skills\n", 1)
        removed_skills = "# Using skills\n" + skills_suffix
        require(hashlib.sha256(removed_skills.encode()).hexdigest() ==
                "ce66a2bc125a8ce168137e6847a54f7a8a10f52fd33e96c2c2cb1de278c5130b",
                "CATALOG_DRIFT", "Pinned empty-skill documentation changed")
    model["model_messages"]["instructions_template"] = prefix
    import difflib
    instruction_diff = "".join(difflib.unified_diff(instruction.splitlines(True), prefix.splitlines(True),
        fromfile="pinned/native-instructions", tofile="adapted/native-instructions"))
    diff_path = destination.with_suffix(".prompt.diff")
    _new_file(diff_path, instruction_diff)
    _new_file(destination, canonical(adapted) + "\n")
    preserved = {key: value for key, value in before_model.items() if key not in replacements and key != "model_messages"}
    return {"path": str(destination), "source": identity, "source_sha256": sha256(source),
            "adapted_sha256": sha256(destination), "model": dict(MODEL),
            "changes": [{"field": field, "before": before, "after": after}
                        for field, (before, after) in replacements.items()],
            "preserved_model_fields_sha256": digest(preserved),
            "prompt_adaptation": {"field": "model_messages.instructions_template", "removed": "Apps and Plugins capability documentation only",
                "removed_bytes": len(removed.encode()), "removed_sha256": hashlib.sha256(removed.encode()).hexdigest(),
                "preserved_prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest(),
                "original_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
                "diff_path": str(diff_path), "diff_sha256": sha256(diff_path),
                "empty_skill_condition": {"required": empty_skills, "arm": arm,
                    "fresh_home_required": empty_skills, "native_catalog_must_be_checked_before_dispatch": empty_skills,
                    "removed_bytes": len(removed_skills.encode()),
                    "removed_sha256": hashlib.sha256(removed_skills.encode()).hexdigest() if removed_skills else None}},
            "native_loop_modified": False, "tool_adapted_semantics": True}


def direct_config(config, *, catalog, arm):
    """Supported native configuration, with inherited OS isolation still mandatory."""
    from native import require
    require(arm in {"C", "R"} and Path(catalog).is_file(), "CATALOG_COPY", "Adapted catalog is missing")
    require(config.count("[features]\n") == 1, "CONFIG_DRIFT", "Expected one native feature table")
    config = 'model_catalog_json = ' + json.dumps(str(Path(catalog).resolve())) + '\n' + config
    config = config.replace("[features]\n", "[features]\nview_image = false\nsleep_tool = false\ngoals = false\n")
    for name in ("common", "reasoning_agent", "verification_agent"):
        config = config.replace(f"[mcp_servers.{name}]\n", f'[mcp_servers.{name}]\ndefault_tools_approval_mode = "approve"\nomit_tools_from = ["deferred", "code_mode"]\n')
    config += '\n[tools.experimental_request_user_input]\nenabled = false\n[tools.update_plan]\nenabled = true\n'
    config += '\n[agents]\n' + ('enabled = false\n' if arm == "C" else
        'enabled = true\ndefault_subagent_model = "gpt-6-astra"\ndefault_subagent_reasoning_effort = "max"\n')
    # Keep native skill rendering enabled so absence on the actual wire is an
    # observable empty catalog, not a hidden catalog. R's original skills remain.
    config += '\n[skills]\ninclude_instructions = true\n'
    for name in ("imagegen", "openai-docs", "skill-creator", "skill-installer"):
        config += '\n[[skills.config]]\nname = ' + json.dumps(name) + '\nenabled = false\n'
    return config


# Parameters captured from the pinned 0.166.0 native direct-tool wire, and checked
# against the corresponding upstream tool specs. Descriptions participate in the
# hash so a changed binary/schema is not silently accepted. Execution stays native.
INTRINSIC_SCHEMAS = {
    "functions.list_mcp_resources": "2e13a90ce5126025c0f59b1b2ccd17135f584b8fc6b40de68c05e363de7e0655",
    "functions.list_mcp_resource_templates": "7366bd91463e8b5d749de5e1d4f2c6a28508eb445ba6b55e80e14c910a2b2704",
    "functions.read_mcp_resource": "c4557459d69e290cec0ceab72c6c59da2dd0f1f05ad77f80cf0bfcf164f714a2",
    "functions.update_plan": "7a0d4f64367ba0df6b25b415fb385bbeed450beea90e4fe86b91aea9c2d45158",
    "clock.curr_time": "99334726611ccf58a148b0814696bfa6fe08c1b2d027e946beccf5a74331c9aa",
    "collaboration.followup_task": "fbcba3d23a28365528e1a8778aa24b53150d6d6672a71ecfb26418c57106ecd8",
    "collaboration.interrupt_agent": "82903eab0a1920ff3de82fd8db2bc45a571b3a418ad0b9107a49048c925e5821",
    "collaboration.list_agents": "c6d58559188e5c9405112cb2e50431a3032048a634f9cc2883844874953e2584",
    "collaboration.send_message": "806c0094585e40f1ab8b35915c2400401da32dd871924eeb24254f40923ac6d8",
    # Pinned R project config adds the original subgoal-prover agent_type option.
    "collaboration.spawn_agent": "7d26f1efdcf00aeef6e7ad81ae0ffebe8b71fb26124e8b7590b14b039b2be91f",
    "collaboration.wait_agent": "3e9f34360d221ec070a2867d14314e1e7230b91443e70deedc15a5c5a4a53279",
}
OMITTED_OPTIONAL_TOOLS = {"functions.list_mcp_resources", "functions.list_mcp_resource_templates",
                          "functions.read_mcp_resource", "clock.curr_time"}
R_TOOLS = {
    "mcp__reasoning_agent": {"memory_init", "memory_append", "memory_search", "branch_update", "search_arxiv_theorems", "verify_proof_service", "native_skill_read"},
    "mcp__verification_agent": {"memory_init", "memory_append", "memory_query", "search_arxiv_theorems", "validate_verification_output", "write_verification_output", "native_skill_read"},
}


def native_schema(schema):
    """Pinned Codex's MCP schema projection; gateway execution keeps every limit.

    Codex's JsonSchema type drops these numeric/maximum annotations while exposing
    MCP functions. This comparison accepts only that observed projection, never a
    different type, property, required key, or additionalProperties policy.
    """
    if isinstance(schema, list):
        return [native_schema(value) for value in schema]
    if isinstance(schema, dict):
        return {key: native_schema(value) for key, value in schema.items()
                if key not in {"minimum", "maximum", "maxItems", "title", "$schema"}}
    return schema


def validate_arguments(value, schema):
    """Validate the constrained native schema before an intrinsic is released."""
    from ledger import require
    kind = schema.get("type")
    matches = {"object": type(value) is dict, "array": type(value) is list,
               "string": isinstance(value, str), "integer": type(value) is int,
               "number": type(value) in {int, float}, "boolean": type(value) is bool,
               "null": value is None}
    require(kind in matches and matches[kind], "TOOL_SCHEMA", "Native tool argument type differs")
    if "enum" in schema:
        require(value in schema["enum"], "TOOL_SCHEMA", "Native tool enum differs")
    if kind == "object":
        props = schema.get("properties", {})
        require(set(schema.get("required", [])) <= set(value), "TOOL_SCHEMA", "Required native tool argument missing")
        if schema.get("additionalProperties") is False:
            require(set(value) <= set(props), "TOOL_SCHEMA", "Unknown native tool argument")
        for key in value.keys() & props.keys():
            validate_arguments(value[key], props[key])
    if kind == "array":
        require(len(value) >= schema.get("minItems", 0) and len(value) <= schema.get("maxItems", len(value)),
                "TOOL_SCHEMA", "Native array argument exceeds bounds")
        for item in value:
            validate_arguments(item, schema["items"])
    if kind in {"integer", "number"}:
        import math
        require(math.isfinite(value) and value >= schema.get("minimum", value) and value <= schema.get("maximum", value),
                "TOOL_SCHEMA", "Native number argument exceeds bounds")


def common_output_receipt(output):
    """Decode only native/MCP text envelopes, without changing the tool result."""
    from ledger import require
    import re
    # Pinned codex-rs/core/src/tools/context.rs:234-245 prepends this timing
    # item. It is presentation metadata; the complete receipt is still hashed.
    timing = r"Wall time: [0-9]+\.[0-9]{4} seconds\nOutput:"
    value = output
    for _ in range(6):
        if isinstance(value, str):
            header = re.match(timing + r"\n", value)
            if header:
                value = value[header.end():]
            try:
                value = json.loads(value)
            except ValueError:
                break
        elif isinstance(value, dict) and type(value.get("ok")) is bool and isinstance(value.get("metrics"), dict):
            return value
        elif (isinstance(value, list) and len(value) == 2 and isinstance(value[0], dict) and
              set(value[0]) == {"type", "text"} and value[0].get("type") == "input_text" and
              isinstance(value[0].get("text"), str) and re.fullmatch(timing, value[0]["text"])):
            value = value[1]
        elif isinstance(value, list) and len(value) == 1:
            value = value[0]
        elif isinstance(value, dict) and value.get("type") in {"text", "input_text"} and isinstance(value.get("text"), str):
            value = value["text"]
        elif isinstance(value, dict) and isinstance(value.get("content"), list):
            value = value["content"]
        else:
            break
    require(False, "NATIVE_TOOL_RECEIPT", "Native common output is not its complete gateway receipt")


class NativeDirectBridge:
    """Validate native namespaces and account native state tools before delivery.

    One instance belongs to one run/role. Common and original R MCP functions are
    metered at their real server execution boundary. Native in-process functions
    are reserved before the model response reaches Codex and finished only when
    Codex sends the corresponding function_call_output on its next physical call.
    Missing completion remains an unknown reservation; CPU is never invented.
    """
    def __init__(self, arm, role, common_tools, *, tool_timeout_seconds=10, empty_skills=False):
        from ledger import require
        from native import ROLES
        require(arm in {"C", "R"} and role in ROLES[arm], "ROLE_ROUTE", "Unknown native bridge role")
        self.arm, self.role = arm, role
        require(type(empty_skills) is bool and (not empty_skills or arm == "C"),
                "SKILL_CATALOG", "Only C may use an audited empty skill catalog")
        self.empty_skills = empty_skills
        self.common = {tool["name"]: copy.deepcopy(tool) for tool in common_tools}
        require(len(self.common) == len(common_tools) and bool(self.common), "TOOL_SCHEMA", "Unique frozen common tools required")
        self.tool_timeout_seconds = tool_timeout_seconds
        self.intrinsic_names = {name for name in INTRINSIC_SCHEMAS if
            name not in OMITTED_OPTIONAL_TOOLS and (arm == "R" or not name.startswith("collaboration."))}
        self.mcp_namespaces = {"mcp__common"} if arm == "C" else ({"mcp__verification_agent"} if role == "verifier" else {"mcp__reasoning_agent"})
        self.declarations = {}
        self.calls = {}
        self.outputs = {}
        self.lock = threading.RLock()
        self.scope = None
        self.adaptations = {}

    def _filter_optional(self, payload):
        from ledger import require
        before, before_bytes, removed = digest(payload), len(canonical(payload).encode()), []
        containers = [payload] + [item for item in payload.get("input", []) if isinstance(item, dict) and item.get("type") == "additional_tools"]
        for container in containers:
            spaces = container.get("tools")
            if not isinstance(spaces, list):
                continue
            retained = []
            for space in spaces:
                if not isinstance(space, dict) or space.get("type") != "namespace" or not isinstance(space.get("tools"), list):
                    retained.append(space)
                    continue
                functions = []
                for tool in space["tools"]:
                    full = str(space.get("name")) + "." + str(tool.get("name")) if isinstance(tool, dict) else ""
                    if full in OMITTED_OPTIONAL_TOOLS:
                        require(tool.get("type") == "function" and digest(tool.get("parameters")) == INTRINSIC_SCHEMAS[full],
                                "TOOL_SCHEMA", "Omitted optional native function changed")
                        removed.append(full)
                    else:
                        functions.append(tool)
                if functions:
                    retained.append({**space, "tools": functions})
            if "tools" in container:
                container["tools"] = retained
        self.adaptations[id(payload)] = {"before_payload_sha256": before, "after_payload_sha256": digest(payload),
            "before_utf8_bytes": before_bytes, "after_utf8_bytes": len(canonical(payload).encode()),
            "removed_names": sorted(removed), "reason": "unavailable_mcp_resources_and_optional_clock", "native_loop_modified": False}

    def _qualified(self, item):
        from ledger import require
        name, namespace = item.get("name"), item.get("namespace")
        require(isinstance(name, str) and name, "TOOL_NOT_ALLOWED", "Native function name required")
        if namespace is not None:
            require(isinstance(namespace, str) and namespace and "." not in name,
                    "TOOL_NOT_ALLOWED", "Ambiguous native function namespace")
            return namespace + "." + name
        if name in self.declarations:
            return name
        candidates = [full for full in self.declarations
                      if full.rsplit(".", 1)[1] == name or full.replace(".", "__", 1) == name]
        require(len(candidates) == 1, "TOOL_NOT_ALLOWED", "Unknown or ambiguous native function")
        return candidates[0]

    def validate_request(self, payload, *, allowed_names, scope):
        from ledger import require
        require(scope in {"offline", "smoke", "formal"}, "TOOL_NOT_ALLOWED", "Unknown native request scope")
        with self.lock:
            require(self.scope in {None, scope}, "TOOL_NOT_ALLOWED", "Native scope changed within a run")
            self.scope = scope
            if self.empty_skills:
                # The pinned renderer emits this block for every visible native
                # skill, including an explicit warning for a hidden/omitted list.
                # C alone disables the four unrelated bundled skills and runs in
                # an empty fresh home/workspace with include_instructions=true.
                rendered = canonical(payload.get("input", []))
                require(all(marker not in rendered for marker in (
                    "<skills_instructions>", "<available_skills>", "### Available skills",
                    "skills are available but omitted", "skills are not listed automatically")),
                    "SKILL_CATALOG", "C's native skill catalog is no longer empty")
            self._filter_optional(payload)
            permitted_common = set(self.common) & set(allowed_names)
            if scope == "smoke":
                require(not ({"check", "submit"} & set(self.common)), "TOOL_NOT_ALLOWED", "Smoke cannot register target tools")
            sources = list(payload.get("tools", []))
            request_input = payload.get("input", [])
            require(type(request_input) is list and type(payload.get("tools", [])) is list,
                    "TOOL_NOT_ALLOWED", "Native request must use explicit input and tool arrays")
            for item in request_input:
                require(type(item) is dict, "TOOL_NOT_ALLOWED", "Invalid native input item")
                if item.get("type") == "additional_tools":
                    require(set(item) <= {"type", "id", "role", "tools"} and item.get("role") == "developer" and type(item.get("tools")) is list,
                            "TOOL_NOT_ALLOWED", "Invalid native additional_tools declaration")
                    sources.extend(item["tools"])
                require(item.get("type") not in {"custom_tool_call", "custom_tool_call_output", "tool_search_call", "tool_search_output"},
                        "TOOL_NOT_ALLOWED", "Executable or deferred native tools are not admitted")
            for space in sources:
                require(type(space) is dict and space.get("type") == "namespace" and type(space.get("tools")) is list,
                        "TOOL_NOT_ALLOWED", "Only audited direct function namespaces are admitted")
                namespace = space.get("name")
                require(namespace in self.mcp_namespaces | {"functions", "clock"} | ({"collaboration"} if self.arm == "R" else set()),
                        "TOOL_NOT_ALLOWED", "Unapproved native namespace")
                for tool in space["tools"]:
                    require(type(tool) is dict and tool.get("type") == "function" and isinstance(tool.get("name"), str),
                            "TOOL_NOT_ALLOWED", "Only native function tools are admitted")
                    name, params = tool["name"], tool.get("parameters")
                    full = namespace + "." + name
                    require(type(params) is dict and params.get("type") == "object", "TOOL_SCHEMA", "Native function parameters required")
                    if namespace in self.mcp_namespaces and name in permitted_common:
                        require(params == native_schema(self.common[name]["parameters"]), "TOOL_SCHEMA", "Common native schema drift")
                        category = "common"
                    elif namespace in self.mcp_namespaces and self.arm == "R" and name in R_TOOLS[namespace]:
                        category = "rethlas_mcp"
                    else:
                        require(full in self.intrinsic_names and digest(params) == INTRINSIC_SCHEMAS[full],
                                "TOOL_NOT_ALLOWED", "Unmetered native function or intrinsic schema drift")
                        category = "intrinsic"
                        if full == "collaboration.spawn_agent":
                            before_tool = digest(tool)
                            description = tool.get("description", "")
                            require(description.count("Spawns an agent") == 1, "TOOL_SCHEMA", "Native spawn model menu changed")
                            menu, instructions = description.split("Spawns an agent", 1)
                            require(hashlib.sha256(menu.encode()).hexdigest() ==
                                    "de55dfcdae14acf7d37298aea67347c92781da2aa17361b8a35170bf22b185b0",
                                    "TOOL_SCHEMA", "Native available-model menu changed")
                            tool["description"] = ("Available model overrides: gpt-6-astra with reasoning effort max. "
                                "Omit overrides to inherit the same parent settings.\nSpawns an agent" + instructions)
                            params = copy.deepcopy(params)
                            params["properties"]["model"]["enum"] = ["gpt-6-astra"]
                            params["properties"]["reasoning_effort"]["enum"] = ["max"]
                            tool["parameters"] = params
                            self.adaptations[id(payload)].setdefault("model_policy_adaptations", []).append({
                                "name": full, "before_tool_sha256": before_tool, "after_tool_sha256": digest(tool),
                                "removed_menu_sha256": hashlib.sha256(menu.encode()).hexdigest(),
                                "retained_coordination_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
                                "retained_agent_types_sha256": digest(params["properties"]["agent_type"]),
                                "native_roles_removed": []})
                    declaration = {"parameters": params, "category": category}
                    require(full not in self.declarations or self.declarations[full] == declaration,
                            "TOOL_SCHEMA", "Native function changed within the run")
                    self.declarations[full] = declaration
            require(bool(self.declarations), "TOOL_NOT_ALLOWED", "Native request has no audited declarations")
            self.adaptations[id(payload)].update(after_payload_sha256=digest(payload),
                                                 after_utf8_bytes=len(canonical(payload).encode()))

    def validate_response(self, response, *, request_id, ledger, run_id):
        from ledger import require
        staged = []
        with self.lock:
            for item in response.get("output", []):
                require(type(item) is dict, "RESPONSE_SCHEMA", "Invalid native response item")
                kind = item.get("type")
                require(kind in {"message", "reasoning", "function_call"}, "TOOL_NOT_ALLOWED", "Unknown native response capability")
                if kind != "function_call":
                    continue
                full = self._qualified(item)
                require(full in self.declarations, "TOOL_NOT_ALLOWED", "Response requested an unadvertised native tool")
                call_id = item.get("call_id")
                require(isinstance(call_id, str) and 0 < len(call_id) <= 200 and call_id not in self.calls and
                        all(call_id != pending[0] for pending in staged), "TOOL_SCHEMA", "Duplicate native call identity")
                require(isinstance(item.get("arguments"), str), "TOOL_SCHEMA", "Native arguments must be explicit JSON")
                try:
                    arguments = json.loads(item["arguments"])
                except (ValueError, TypeError) as error:
                    raise ValueError("Native function arguments are not JSON") from error
                declaration = self.declarations[full]
                if declaration["category"] == "intrinsic":
                    validate_arguments(arguments, declaration["parameters"])
                    if full.startswith("functions.") and "mcp_resource" in full:
                        require(arguments.get("server") in {None, *(name.removeprefix("mcp__") for name in self.mcp_namespaces)},
                                "TOOL_NOT_ALLOWED", "Native resource access is restricted to the frozen MCP servers")
                    if full == "collaboration.spawn_agent":
                        require(arguments.get("model", "gpt-6-astra") == "gpt-6-astra" and arguments.get("reasoning_effort", "max") == "max",
                                "MODEL_DRIFT", "Native descendant model or effort changed")
                    if full == "collaboration.wait_agent":
                        require(10000 <= arguments.get("timeout_ms", 30000) <= self.tool_timeout_seconds * 1000,
                                "TOOL_BUDGET", "Native wait exceeds the frozen tool deadline")
                staged.append((call_id, full, arguments, declaration["category"]))
            # Validate every output first, then reserve all native executions before
            # Gateway can release any of this response to the unmodified CLI.
            for call_id, full, arguments, category in staged:
                identity = "native-" + digest([run_id, self.arm, self.role, call_id])
                if category == "intrinsic":
                    ledger.tool_event(run_id, {"type": "tool_started", "request_id": identity,
                        "name": full, "native_role": self.role, "call_id": call_id,
                        "parent_request_id": request_id, "args_sha256": digest(arguments),
                        "admission_boundary": "before_native_response_delivery"})
                self.calls[call_id] = {"id": identity, "name": full, "category": category,
                                       "parent_request_id": request_id, "args_sha256": digest(arguments), "started": time.monotonic()}

    def observe_inputs(self, payload, *, ledger, run_id):
        from ledger import require
        with self.lock:
            adaptation = self.adaptations.pop(id(payload), None)
            require(adaptation is not None, "TOOL_SCHEMA", "Native request was not validated before observation")
            ledger.record(run_id, "native_tool_capability_filter", **adaptation)
            if self.empty_skills:
                ledger.record(run_id, "native_empty_skill_catalog", arm=self.arm,
                              checked_before_dispatch=True, include_instructions=True,
                              evidence="pinned_native_skill_renderer_emitted_no_catalog")
            for item in payload.get("input", []):
                if item.get("type") != "function_call_output":
                    continue
                call_id = item.get("call_id")
                require(call_id in self.calls, "TOOL_SCHEMA", "Native output has no admitted function call")
                result_hash = digest(item.get("output"))
                if call_id in self.outputs:
                    require(self.outputs[call_id] == result_hash, "TOOL_SCHEMA", "Repeated native output changed")
                    continue
                call = self.calls[call_id]
                if call["category"] == "intrinsic":
                    ledger.tool_event(run_id, {"type": "tool_finished", "request_id": call["id"],
                        "name": call["name"], "native_role": self.role, "call_id": call_id,
                        "parent_request_id": call["parent_request_id"], "result_sha256": result_hash,
                        "status": "native_output_observed", "cpu_seconds": None,
                        "cpu_observation_complete": False,
                        "wall_seconds": max(0.0, time.monotonic() - call["started"]),
                        "wall_scope": "response_delivery_to_next_request_includes_transport"})
                else:
                    events = [event for event in ledger.events() if event.get("run_id") == run_id]
                    if call["category"] == "common":
                        contexts = [event for event in events if event["type"] == "native_tool_context" and
                            event.get("native_call_id") == call_id and event.get("tool_name") == call["name"].rsplit(".", 1)[1] and
                            event.get("parent_request_id") == call["parent_request_id"] and event.get("arguments_sha256") == call["args_sha256"]]
                        require(len(contexts) == 1, "NATIVE_TOOL_RECEIPT", "Native MCP output lacks its exact execution receipt")
                        tool_id = contexts[0]["tool_request_id"]
                    else:
                        starts = [event["tool"] for event in events if event["type"] == "tool_started" and
                            event["tool"].get("call_id") == call_id and event["tool"].get("parent_request_id") == call["parent_request_id"] and
                            event["tool"].get("args_sha256") == call["args_sha256"]]
                        require(len(starts) == 1, "NATIVE_TOOL_RECEIPT", "Original R tool output lacks its native admission")
                        tool_id = starts[0]["request_id"]
                    finishes = [event["tool"] for event in events if event["type"] == "tool_finished" and event["tool"].get("request_id") == tool_id]
                    require(len(finishes) == 1 and isinstance(finishes[0].get("result_sha256"), str),
                            "NATIVE_TOOL_RECEIPT", "Native MCP output has no finished execution receipt")
                    verified, projection_id, model_body_hash = False, None, None
                    if call["category"] == "common":
                        receipt = common_output_receipt(item.get("output"))
                        metrics = receipt["metrics"]
                        # The host retains the full compute observation. A shared
                        # smoke projection may omit only host diagnostics; bind
                        # the actual sent result to that exact host ledger event.
                        model_body_hash = digest({key: value for key, value in receipt.items() if key != "metrics"})
                        host_hash = finishes[0]["result_sha256"]
                        if model_body_hash != host_hash:
                            projections = [event for event in events if event["type"] == "common_reply_projection" and
                                           event.get("tool_request_id") == tool_id]
                            require(len(projections) == 1 and projections[0].get("full_result_sha256") == host_hash and
                                    projections[0].get("sent_result_sha256") == model_body_hash and
                                    projections[0].get("sent_sha256") == digest(receipt),
                                    "NATIVE_TOOL_RECEIPT", "Native common projection lacks its exact full-host receipt binding")
                            projection_id = projections[0]["event_id"]
                        host_metrics = {key: value for key, value in finishes[0].items()
                                        if key not in {"type", "ok", "error_code", "metrics", "late_receipt"}}
                        host_metrics["model_request_id"] = call["parent_request_id"]
                        projected_metrics = {"request_id": tool_id, "model_request_id": call["parent_request_id"],
                                             "result_sha256": model_body_hash}
                        require(model_body_hash == metrics.get("result_sha256") and
                                host_metrics.get("request_id") == tool_id and host_metrics.get("run_id") == run_id and
                                host_metrics.get("params_sha256") == call["args_sha256"] and
                                metrics in (host_metrics, projected_metrics),
                                "NATIVE_TOOL_RECEIPT", "Native continuation changed the executed common tool result")
                        verified = True
                    ledger.record(run_id, "native_function_output", call_id=call_id, name=call["name"],
                                  parent_request_id=call["parent_request_id"], result_sha256=result_hash,
                                  tool_request_id=tool_id, tool_result_sha256=finishes[0]["result_sha256"],
                                  model_tool_result_sha256=model_body_hash, receipt_projection_event_id=projection_id,
                                  output_receipt_verified=verified, accounting_boundary="mcp_server")
                self.outputs[call_id] = result_hash


def native_launcher_source(node, codex, receipt_directory):
    """One native invocation with a return-code receipt; no agent or retry loop."""
    return '''import hashlib, json, os, subprocess, sys, time, uuid
from pathlib import Path
COMMAND = __COMMAND__
RECEIPTS = Path(__RECEIPTS__)
arguments = sys.argv[1:]
if arguments == ["--version"]:
    raise SystemExit(subprocess.run(COMMAND + arguments).returncode)
role = os.environ.get("FOUR_HARNESS_ROLE")
if not arguments or arguments[0] != "exec" or role not in {"generator", "verifier"}:
    raise SystemExit(64)
command = COMMAND + ["exec", "--skip-git-repo-check", "--config", 'model_reasoning_effort="max"',
                     "--config", 'model_provider="four_harness_' + role + '"'] + arguments[1:]
started = time.monotonic()
result = subprocess.run(command)
receipt = {"kind": "installed_codex_process", "role": role, "returncode": result.returncode,
           "command_sha256": hashlib.sha256(json.dumps(command, ensure_ascii=False, sort_keys=True,
               separators=(",", ":")).encode()).hexdigest(), "wall_seconds": time.monotonic() - started,
           "retry_count": 0, "native_loop_replaced": False}
path = RECEIPTS / (uuid.uuid4().hex + ".json")
with path.open("x", encoding="utf-8") as stream:
    json.dump(receipt, stream, sort_keys=True, separators=(",", ":"))
raise SystemExit(result.returncode)
'''.replace("__COMMAND__", repr([str(node), str(codex)])).replace("__RECEIPTS__", repr(str(receipt_directory)))


def collect_native_evidence(plan, result, *, ledger, run_id, evidence_path=None):
    """Bind original native termination to physical requests and executed tool output."""
    from native import _new_file, require, sha256
    contract = plan["native_termination_contract"]
    snapshot = ledger.snapshot(run_id)
    events = [event for event in ledger.events() if event.get("run_id") == run_id]
    proof = [event for event in events if event["type"] == "native_function_output" and event.get("output_receipt_verified") is True]
    evidence = {"arm": contract["arm"], "native_returncode": result["returncode"],
        "termination_contract": contract, "physical_request_ids": [row["id"] for row in snapshot["request_states"]],
        "native_tool_output_proof": [{key: event[key] for key in ("call_id", "name", "parent_request_id", "tool_request_id",
                                     "tool_result_sha256", "model_tool_result_sha256", "receipt_projection_event_id")}
                                     for event in proof],
        "accounting_complete": snapshot["accounting_complete"], "full_verification_chain_qualified": False,
        "bounded_stop_qualified": False}
    clean = (not result.get("timed_out") and not result.get("cancelled") and
             result.get("cleanup_complete_for_known_processes") is True and snapshot["accounting_complete"] and
             snapshot["requests"] == 2 and all(row["state"] == "finished" for row in snapshot["request_states"]) and bool(proof))
    if contract["arm"] == "C":
        evidence["native_completed"] = bool(clean and result["returncode"] == 0)
    else:
        script, launcher, log = (Path(contract[key]) for key in ("script_path", "launcher_path", "iteration_log"))
        require(not script.is_symlink() and not launcher.is_symlink() and sha256(script) == contract["script_sha256"] and
                sha256(launcher) == contract["launcher_sha256"], "NATIVE_SOURCE_DRIFT", "Original R launch contract changed")
        records = []
        for path in sorted(Path(contract["invocation_receipts"]).glob("*.json")):
            require(not path.is_symlink() and path.stat().st_size <= 4096, "NATIVE_RECEIPT", "Invalid native invocation receipt")
            records.append({**json.loads(path.read_text()), "receipt_sha256": sha256(path)})
        exact_stop = contract["terminal_marker"] in result.get("stderr", "").splitlines()
        iteration = any(line.startswith(contract["iteration_marker"]) for line in result.get("stdout", "").splitlines())
        native_success = (len(records) == 1 and records[0].get("kind") == "installed_codex_process" and
                          records[0].get("role") == "generator" and records[0].get("returncode") == 0 and
                          records[0].get("retry_count") == 0)
        log_hash = sha256(log) if log.is_file() and not log.is_symlink() else None
        marker = script.parent.parent / "results/four-harness-smoke/blueprint_verified.md"
        evidence.update(native_invocations=records, iteration_log_sha256=log_hash,
                        original_terminal_marker_observed=exact_stop, original_iteration_success_observed=iteration,
                        verified_blueprint_present=marker.exists(), native_completed=False,
                        bounded_stop_qualified=bool(clean and result["returncode"] == 1 and exact_stop and iteration and
                                                   native_success and log_hash and not marker.exists()))
    if evidence_path is not None:
        path = _new_file(evidence_path, canonical(evidence) + "\n")
        evidence["evidence_file"] = str(path)
        evidence["evidence_sha256"] = sha256(path)
    return evidence


def prepare_codex_smoke(root, *, arm, source_root, node, codex, python, gateway_url,
                        role_tokens, execution, codex_source_root=None, python_site=None, offline=False):
    """Prepare, but never run, the genuine native command for the frozen smoke.

    Offline preflight uses this identical command and policy with only the host's
    immutable Responses transport replaced. No proof marker or substitute loop is
    generated. A caller must use identity-checked cancellation while running it.
    """
    import os
    import re
    import shlex
    import sys
    from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
    from cold_start import SMOKE_TASK
    from native import (CONTEXT, MODEL, PINS, ROLES, _new_file, codex_config,
                        isolated_environment, isolation_probe, localhost_url, macos_fixture_profile,
                        managed_preferences_probe, prepare_source, require, rethlas_config,
                        rethlas_mcp_dependencies, run_process, sha256)

    root, source_root = Path(root).resolve(), Path(source_root).resolve()
    node, codex, python = Path(node).resolve(), Path(codex).resolve(), Path(python).resolve()
    require(arm in {"C", "R"} and set(role_tokens) == set(ROLES[arm]), "ROLE_ROUTE", "All frozen native roles are required")
    require(type(offline) is bool and ((offline and execution is None) or
            (not offline and isinstance(execution, dict) and execution.get("arm") == arm)),
            "EXECUTION", "An explicit matching native smoke execution is required; offline uses no live execution")
    require(sys.platform == "darwin" and not root.exists() and not root.is_relative_to(source_root),
            "ISOLATION", "Native smoke needs a fresh isolated macOS root outside original source")
    require(all(path.is_file() for path in (node, codex, python)), "RUNTIME", "Pinned native runtimes are required")
    base = localhost_url(gateway_url)
    require(base.count("/") == 2, "ROLE_ROUTE", "Gateway origin must not have a path")
    package = json.loads((codex.parent.parent / "package.json").read_text())
    require(package.get("name") == "@openai/codex" and package.get("version") == PINS["C"]["version"],
            "BINARY_VERSION", "Installed Codex differs from the pinned version")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise RuntimeError("Native gateway manifest redirects are forbidden")

    from ledger import SMOKE_BUDGET
    expected_budget = dict(SMOKE_BUDGET)
    opener, manifests, routes = build_opener(ProxyHandler({}), NoRedirect()), {}, {}
    for role, token in role_tokens.items():
        url = base + f"/{arm}/{role}"
        localhost_url(url + "/v1", arm=arm, role=role)
        request = Request(url + "/manifest", b"{}", {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with opener.open(request, timeout=5) as response:
            manifest = json.load(response)
        require(manifest.get("offline") is offline and manifest.get("scope") == "smoke" and
                manifest.get("model_policy") == MODEL and manifest.get("budget") == expected_budget and
                manifest.get("context_window") == CONTEXT and manifest.get("canonical_cold_input_checked") is True and
                manifest.get("execution") == execution, "GATEWAY_POLICY", "Native smoke manifest differs from the frozen execution")
        require({tool["name"] for tool in manifest["tools"]} ==
                {"materials_list", "materials_read", "workspace_read", "workspace_write", "compute"},
                "TOOL_NOT_ALLOWED", "Smoke must expose exactly the five common tools")
        manifests[role], routes[role] = manifest, {"url": url, "token": token}
    if not offline:
        require(execution.get("mode") == "authorized_smoke", "EXECUTION", "Live native mode requires the authorized smoke execution")
    root.mkdir(parents=True, mode=0o700)
    env = isolated_environment(root, token=role_tokens["agent" if arm == "C" else "generator"], path_entries=[node.parent, python.parent])
    env.update(FOUR_HARNESS_OFFLINE_FIXTURE="1" if offline else "0", FOUR_HARNESS_EXECUTION=canonical(execution),
               FOUR_HARNESS_ROUTES=canonical(routes), FOUR_HARNESS_MAX_OUTPUT="2048")
    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    writable = [workspace, *[Path(env[key]) for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR")]]
    catalog_source = source_root if arm == "C" else Path(codex_source_root or Path(__file__).resolve().parents[2] / "dist/four-harness-sources/codex-0.166.0")
    catalog = prepare_catalog(catalog_source, root / "native-catalog.json", arm=arm, empty_skills=arm == "C")
    readable = [node.parent, codex.parent.parent, python.parent, Path(sys.base_prefix)]
    # The canonical task is the only operator seed. Both unchanged canonical
    # files remain available through common materials; no duplicated file labels.
    prompt = SMOKE_TASK
    adapters = {role: NativeDirectBridge(arm, role, manifests[role]["tools"], tool_timeout_seconds=10,
                                        empty_skills=arm == "C") for role in role_tokens}
    preservation = None
    termination = {"arm": arm, "expected_returncode": 0, "reason": "native_codex_finished"}
    if arm == "C":
        shim = _new_file(root / "common-mcp.py", (Path(__file__).parent / "mcp.py").read_text())
        config = codex_config(base + "/C/agent/v1", mcp_command=[str(python), "-I", str(shim)],
                              tools_url=base + "/C/agent/tools", direct_catalog=catalog["path"])
        command = [str(node), str(codex), "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", prompt]
        cwd = workspace
    else:
        require(python_site is not None, "DEPENDENCIES", "R requires its isolated frozen MCP dependencies")
        dependency_receipt = rethlas_mcp_dependencies(python_site)
        site = Path(python_site).resolve()
        source_copy = root / "source"
        preservation = prepare_source(source_root, source_copy, "R")
        skills, scan_hashes = {}, {}
        leak = re.compile(r"ring.?15|1179648|1,179,648|baseline.?cert", re.IGNORECASE)
        for mode in ("generation", "verification"):
            skills[mode] = {}
            role_root = source_copy / "agents" / mode
            sources = [role_root / "AGENTS.md", *sorted((role_root / ".agents/skills").glob("*/SKILL.md"))]
            for path in sources:
                require(not path.is_symlink() and path.resolve().is_relative_to(role_root), "SKILL_PATH", "Original native skill path escaped")
                text = path.read_text()
                require(leak.search(text) is None, "SOURCE_ANSWER_LEAK", "Native prompt or skill contains excluded target information")
                relative = str(path.relative_to(source_copy))
                scan_hashes[relative] = sha256(path)
                if path.name == "SKILL.md":
                    skills[mode][path.parent.name] = {"path": relative, "sha256": sha256(path)}
            require(skills[mode], "SKILL_SOURCE", "Original R role has no native skills")
            project_config = role_root / ".codex/config.toml"
            text = project_config.read_text()
            for name in ("reasoning_agent", "verification_agent"):
                text = text.replace(f"[mcp_servers.{name}]\n", f'[mcp_servers.{name}]\ndefault_tools_approval_mode = "approve"\nomit_tools_from = ["deferred", "code_mode"]\n')
            project_config.write_text(text)
            for name in ("memory", "results", "logs"):
                directory = role_root / name
                directory.mkdir(exist_ok=True)
                writable.append(directory)
        _new_file(source_copy / "four-harness-skills.json", canonical(skills))
        _new_file(source_copy / "agents/generation/data/four-harness-smoke.md", prompt)
        config = 'project_root_markers = ["four-harness-build.json"]\n' + direct_config(rethlas_config(base, role_tokens), catalog=catalog["path"], arm="R")
        config = 'developer_instructions = ' + json.dumps("Use common materials_list/materials_read for the frozen task and submission files; native_skill_read reads original local R strategy SKILL.md files by skill directory name. Original memory, branch, verification and recursive strategy remain in effect.") + "\n" + config
        for project in (source_copy, source_copy / "agents/generation", source_copy / "agents/verification"):
            config += f'\n[projects.{json.dumps(str(project))}]\ntrust_level = "trusted"\n'
        receipts = workspace / "native-invocations"
        receipts.mkdir(mode=0o700)
        launcher = _new_file(root / "native-launch.py", native_launcher_source(node, codex, receipts))
        launch = shlex.join([str(python), "-I", str(launcher)])
        wrapper = _new_file(root / "bin/codex", '#!/bin/sh\nexec ' + launch + ' "$@"\n', mode=0o700)
        script = source_copy / "agents/generation/tests/run_example.sh"
        termination = {"arm": "R", "expected_returncode": 1,
            "reason": "original_iteration_limit_without_verified_blueprint",
            "script_path": str(script), "script_sha256": sha256(script),
            "original_script_sha256": sha256(source_root / "agents/generation/tests/run_example.sh"),
            "terminal_marker": "Reached MAX_ITERATIONS=1 without verified blueprint for problem_id=four-harness-smoke",
            "iteration_marker": "Finished problem_id=four-harness-smoke iter=0 -> ",
            "iteration_log": str(source_copy / "agents/generation/logs/four-harness-smoke_iter_0.md"),
            "launcher_path": str(launcher), "launcher_sha256": sha256(launcher),
            "invocation_receipts": str(receipts), "evidence_path": str(root / "native-evidence.json")}

        env.update({f"FOUR_HARNESS_{role.upper()}_TOKEN": token for role, token in role_tokens.items()})
        env.update(FOUR_HARNESS_ROLE="generator", FOUR_HARNESS_PYTHON_SITE=str(site), CODEX_BIN=str(wrapper),
                   PATH=str(wrapper.parent) + os.pathsep + env["PATH"], MODEL="gpt-6-astra", REASONING_EFFORT="max",
                   MAX_ITERATIONS="1", PROBLEM_FILE="data/four-harness-smoke.md", VERIFY_URL=base + "/R/verifier/health",
                   LOG_DIR=str(source_copy / "agents/generation/logs"))
        command, cwd = ["/bin/bash", str(source_copy / "agents/generation/tests/run_example.sh")], source_copy / "agents/generation"
        readable.append(site)
        # Python's frozen _ssl extension links these two Homebrew runtime files.
        # Grant only the resolved libraries, not the Cellar or host TLS settings.
        runtime_libraries = {}
        for name in ("libssl.3.dylib", "libcrypto.3.dylib"):
            library = (Path("/opt/homebrew/opt/openssl@3/lib") / name).resolve()
            require(library.is_file(), "RUNTIME", "Pinned Python TLS runtime library is missing")
            readable.append(library)
            runtime_libraries[str(library)] = sha256(library)
        preservation = {**preservation, "dependencies": dependency_receipt, "runtime_libraries": runtime_libraries,
                        "skill_scan": {"matches": [], "files": scan_hashes},
                        "skill_whitelist": skills, "post_preparation_files": {str(path.relative_to(source_copy)): sha256(path)
                         for path in source_copy.rglob("*") if path.is_file()}}
    config_path = _new_file(Path(env["CODEX_HOME"]) / "config.toml", config)
    # Pinned Codex creates apply_patch/applypatch symlinks on EVERY startup,
    # including --version, beneath this optional PATH-alias directory. Those
    # disabled native tools cannot be admitted into StateGuard's link-free
    # state. Keep just this runtime directory read-only at the OS boundary;
    # Codex explicitly continues without aliases when their setup is denied.
    # Never remove links after launch: that would race the shared watchdog.
    alias_root = Path(env["CODEX_HOME"]) / "tmp" / "arg0-macos"
    alias_root.mkdir(parents=True, mode=0o700)
    profile_text = macos_fixture_profile(root, readable=readable, writable=writable,
                                        gateway_url=base, root_writable=False)
    profile_text += f'\n(deny file-write* (subpath {json.dumps(str(alias_root))}))\n'
    profile = _new_file(root / "native.sb", profile_text)
    prefix = ["/usr/bin/sandbox-exec", "-f", str(profile)]
    probe = __import__("subprocess").run([*prefix, "/usr/bin/true"], env=env, cwd=workspace, capture_output=True, timeout=5)
    require(probe.returncode == 0, "OS_ISOLATION", "Native OS policy could not be applied")
    isolation_probe(prefix, node=node, env=env, work=workspace)
    managed = managed_preferences_probe(prefix, python=python, env=env, work=workspace)
    version = run_process([*prefix, str(node), str(codex), "--version"], env=env, cwd=workspace, timeout=10)
    require(version["returncode"] == 0 and PINS["C"]["version"] in version["stdout"], "BINARY_VERSION", "Native CLI version probe failed")
    intrinsic_names = {role: set(adapter.intrinsic_names) for role, adapter in adapters.items()}
    if arm == "R":
        for role in role_tokens:
            mode = "verification" if role == "verifier" else "generation"
            namespace = "mcp__verification_agent" if role == "verifier" else "mcp__reasoning_agent"
            intrinsic_names[role].update("rethlas_" + mode + "_" + name for name in R_TOOLS[namespace])
    return {"command": [*prefix, *command], "env": env, "cwd": str(cwd),
            "writable_roots": [str(path) for path in writable], "request_adapters": adapters,
            "intrinsic_names": {role: sorted(names) for role, names in intrinsic_names.items()},
            "catalog": catalog, "source": preservation, "isolation_probe_passed": True,
            "managed_preferences_probe": managed, "config_sha256": sha256(config_path), "sandbox_sha256": sha256(profile),
            "runtime_version": version["stdout"].strip(), "offline": offline,
            "runtime_alias_policy": {"path": str(alias_root), "mode": "os_read_only", "native_tools_disabled": ["apply_patch", "applypatch"]},
            "budget": expected_budget, "native_termination_contract": termination,
            "native_loop_modified": False,
            "mode": "offline_native_smoke_preflight" if offline else "authorized_native_smoke_plan"}

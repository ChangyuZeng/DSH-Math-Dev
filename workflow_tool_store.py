"""Run-bound tool receipts. Unknown effects are never automatically replayed."""
from copy import deepcopy
import hashlib
import os
import uuid

from backend import Projects, make_directory, write_new
from research_store import read_regular, safe_path, sync_directory
from schema import canonical, decode, fields, identifier, require, sha, string
from workflow_store import WorkflowStore


def tool_receipt(root, project_id, run_id, action_id, call_id, name, args_sha256, *,
                 phase='claim', arguments=None, arguments_json=None, result=None):
    """Caller holds workflow_service.transaction; returns an immutable receipt."""
    for value in (project_id, run_id, action_id):
        identifier(value)
    string(call_id)
    require(len(call_id) <= 200 and all(32 < ord(c) < 127 for c in call_id), 'TOOL_IDENTITY', 'Invalid native call identity')
    string(name)
    sha(args_sha256)
    require(phase in ('claim', 'finish', 'read'), 'INVALID_SCHEMA', 'Unknown receipt operation')
    run = WorkflowStore(root).dispatch({'action': 'read', 'project_id': project_id, 'run_id': run_id})
    require(run['spec']['format'] == 'dsh-math-workflow-v2', 'UNKNOWN_SCHEMA', 'Tool receipts require v2')
    require(name in {tool['name'] for tool in run['spec']['tools']}, 'TOOL_CAPABILITY', 'Tool was not frozen in this run')
    action = next((a for a in run['actions'] if a['action_id'] == action_id), None)
    require(action is not None, 'INVALID_STATE', 'Tool receipt has no action claim')
    directory = safe_path(Projects(root).path(project_id), 'workflow-tools/' + run_id)
    if phase != 'read':
        make_directory(directory)
    key = hashlib.sha256(call_id.encode()).hexdigest()
    path = safe_path(directory, key + '.json')
    previous = decode(read_regular(path)) if path.exists() else None
    identity = {'run_id': run_id, 'action_id': action_id, 'call_id': call_id, 'name': name, 'args_sha256': args_sha256}
    if previous is not None:
        require(all(previous.get(k) == v for k, v in identity.items()), 'OPERATION_CONFLICT', 'Native call identity was reused')
    if phase == 'read':
        return deepcopy(previous)
    if phase == 'claim':
        require(type(arguments) is dict, 'TOOL_IDENTITY', 'Arguments must be an object')
        if arguments_json is None:
            argument_bytes = canonical(arguments)
        else:
            string(arguments_json)
            require(canonical(decode(arguments_json)) == canonical(arguments), 'TOOL_IDENTITY', 'Raw and parsed arguments differ')
            argument_bytes = arguments_json.encode('utf-8')
        require(hashlib.sha256(argument_bytes).hexdigest() == args_sha256,
                'TOOL_IDENTITY', 'Arguments differ from their exact receipt hash')
        if previous is not None:
            require(previous['status'] == 'completed', 'RECONCILE_REQUIRED', 'Prior tool outcome is unknown; do not replay effects')
            return deepcopy(previous)
        require(run['status'] == 'running' and action['status'] == 'running', 'INVALID_STATE', 'Cannot claim a tool in an inactive action')
        receipt = {**identity, 'format': 'dsh-math-tool-receipt-v2', 'status': 'started',
                   'arguments': deepcopy(arguments), 'arguments_json': arguments_json, 'result': None, 'result_sha256': None}
        require(len(canonical(receipt)) <= 2 * 1024 * 1024, 'TOO_LARGE', 'Tool arguments exceed receipt bound')
        write_new(path, receipt)
        sync_directory(directory)
        return receipt
    require(previous is not None, 'RECONCILE_REQUIRED', 'No durable tool claim exists')
    require(type(result) is dict, 'INVALID_SCHEMA', 'Tool result must be an object')
    encoded = canonical(result)
    require(len(encoded) <= 2 * 1024 * 1024, 'TOO_LARGE', 'Tool result exceeds receipt bound')
    result_sha = hashlib.sha256(encoded).hexdigest()
    if previous['status'] == 'completed':
        require(previous['result_sha256'] == result_sha and previous['result'] == result,
                'OPERATION_CONFLICT', 'Completed tool result is immutable')
        return deepcopy(previous)
    require(previous['status'] == 'started', 'RECONCILE_REQUIRED', 'Tool receipt cannot be completed')
    receipt = {**previous, 'status': 'completed', 'result': deepcopy(result), 'result_sha256': result_sha}
    pending = safe_path(directory, '.pending-' + str(uuid.uuid4()))
    write_new(pending, receipt)
    # Both paths are confined above; atomically replace only this exact claimed receipt.
    os.replace(pending, path)
    sync_directory(directory)
    return receipt

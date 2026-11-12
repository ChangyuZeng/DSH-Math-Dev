"""Offline end-to-end native Agent qualification. Never creates HostTransport."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import time
import signal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import terminal
from bridge import DirectCommonTools
from ledger import GateError


def text(value='offline qualification summary'):
    return terminal.fixture_response([{'type': 'message', 'id': 'msg', 'role': 'assistant',
        'status': 'completed', 'content': [{'type': 'output_text', 'text': value, 'annotations': []}]}])


def call(index, name, arguments):
    return terminal.fixture_response([{'type': 'function_call', 'id': f'fc{index}',
        'call_id': f'call{index}', 'name': name, 'arguments': json.dumps(arguments), 'status': 'completed'}])


def candidate():
    # Public arithmetic fixture witness only, not a scored research solve.
    return {'values': [int((j-i) % 17 in {1,2,4,8,9,13,15,16})
                       for i in range(17) for j in range(i+1,17)]}


def json_file(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = terminal.scoped(args.output)
    root.mkdir()
    cases = {}
    def run(name, responses, **kwargs):
        result = terminal.run_fixture(root / name, 'S1', responses, **kwargs)
        cases[name] = result
        return result
    tools = [('materials_list', {}), ('materials_read', {'id': 'task.json'}),
             ('workspace_write', {'path': 'note.txt', 'text': 'fixture'}),
             ('workspace_read', {'path': 'note.txt'}), ('compute', {'code': 'print(2+3)'}),
             ('check', {'candidate': candidate()}), ('submit', {'candidate': candidate()})]
    result = run('seven-tools', [call(i, name, value) for i, (name, value) in enumerate(tools)])
    assert result['returncode'] == 0 and result['independent_success']
    assert result['physical_fixture_requests'] == 7
    identity = result['native_engine']
    assert identity['constructor'] == 'ReactLoopAgent' and not identity['math_plugin']
    assert set(identity['tools']) == {name for name, _ in tools}
    replies = [json_file(path) for path in (root / 'seven-tools' / 'audit').glob('*.tool_response.json')]
    assert len(replies) == 7 and all(value['ok'] for value in replies)
    assert any(value.get('result', {}).get('stdout') == '5\n' for value in replies)
    result = run('invalid-submit-continues', [call(0, 'submit', {'candidate': {'values': []}}),
        call(1, 'materials_list', {}), call(2, 'submit', {'candidate': candidate()})])
    assert result['returncode'] == 0 and result['physical_fixture_requests'] == 3 and result['independent_success']
    replies = [json_file(path) for path in (root / 'invalid-submit-continues' / 'audit').glob('*.tool_response.json')]
    assert any(value.get('result', {}).get('independent_check', {}).get('target_met') is False for value in replies)
    result = run('compaction', [text(), text()], qualification_compact=True)
    assert result['returncode'] == 0 and result['physical_fixture_requests'] == 2
    requests = json_file(root / 'compaction' / 'physical-requests.json')
    assert [value['role'] for value in requests] == ['worker', 'compaction']
    assert all(value['payload']['model'] == 'gpt-6-astra' and
               value['payload']['reasoning']['effort'] == 'max' and
               value['payload']['max_output_tokens'] == 128000 for value in requests)
    missing = text()
    missing.pop('usage')
    result = run('missing-usage', [missing, text()])
    snapshot = json_file(root / 'missing-usage' / 'ledger-snapshot.json')
    assert result['returncode'] != 0 and result['physical_fixture_requests'] == 1
    assert snapshot['total_tokens'] is None and snapshot['accounting_complete'] is False
    result = run('unknown-output-tool', [call(0, 'bash', {'command': 'echo disallowed'}), text()])
    assert result['returncode'] != 0 and result['physical_fixture_requests'] == 1
    assert all(value['isolation_probe_passed'] and value['upstream_requests'] == 0 for value in cases.values())
    events = [json.loads(line) for line in (root / 'seven-tools' / 'isolated' / 'native-events.jsonl').read_text().splitlines()]
    requests = [value['request'] for value in events if value['type'] == 'native_model_call']
    assert requests and requests[0]['messages'] and any(message.get('role') == 'system' and message.get('content') for message in requests[0]['messages'])
    cancel_output = root / 'sigterm'
    response_path = root / 'cancel-responses.json'
    terminal.save(response_path, [call(0, 'compute', {'code': 'import time; time.sleep(30)'})])
    program = 'import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import terminal;terminal.run_fixture(Path(sys.argv[2]),"S1",json.loads(Path(sys.argv[3]).read_text()))'
    child = subprocess.Popen([sys.executable, '-B', '-c', program, str(HERE), str(cancel_output), str(response_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and child.poll() is None:
        if cancel_output.exists() and list((cancel_output / 'audit').glob('*.tool_request.json')):
            break
        time.sleep(.05)
    assert child.poll() is None and list((cancel_output / 'audit').glob('*.tool_request.json'))
    child.send_signal(signal.SIGTERM)
    stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, stderr.decode()
    process = json_file(cancel_output / 'process.json')
    assert process['cleanup_complete_for_known_processes'] is True
    assert json_file(cancel_output / 'result.json')['returncode'] != 0
    assert 'signal_15' in (cancel_output / 'ledger-events.json').read_text()
    assert not process['cleanup']['remaining_known_pids']
    assert (cancel_output / 'ledger-snapshot.json').is_file() and (cancel_output / 'final.json').is_file()
    cases['sigterm'] = json_file(cancel_output / 'result.json')
    summary = {'passed': True, 'fixture_only': True, 'upstream_requests': 0,
               'cases': cases, 'assertions': ['native_identity', 'seven_tools', 'native_context_nonempty',
               'tool_receipts', 'compute_5', 'fresh_submit', 'invalid_submit_continues',
               'actual_native_compaction_metered', 'missing_usage_null', 'unknown_tool_rejected', 'os_canary_denials',
               'sigterm_native_process_cleanup_and_final_receipts']}
    terminal.save(root / 'tests.json', summary)
    print(json.dumps({'passed': True, 'cases': len(cases), 'output': str(root)}))


if __name__ == '__main__':
    main()

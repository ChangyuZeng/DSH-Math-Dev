"""Auditable offline entry point; this file contains no live model transport.

Commands: inspect, baseline, fixture C|X|R|M, freeze-draft, smoke-plan, launch.
`launch` admits only the separately authorized, frozen all-arm smoke campaign.
Formal execution stays closed. Generic proposals, formal drafts and offline
reports never grant permission to call a real model.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from ledger import Ledger, FORMAL_BUDGET, SMOKE_BUDGET, POLICY, RESOURCE_POLICY, canonical, GateError
from cold_start import PROTOCOL, INFORMATION_POLICY, input_manifest, write_materials, prepare as prepare_cold_start
from gateway import Gateway, FixtureTransport, capability, responses_tools
from native import (PINS, ROLES, source_identity, NativeBlocked, codex_fixture, DEFAULT_NODE, DEFAULT_CODEX,
                    codex_config, xean_settings, rethlas_config, run_process)
from tools import CommonTools, ToolLimits
from audit import validate_witness

# Public baseline replay requires the exact published certificate supplied by path.
TOY = 'Non-target qualification only. Read the common material, compute 2 + 3 using a common tool, and report the result through the native harness. Do not search or solve Ring15.'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def inspect_sources(sources):
    sources = Path(sources).resolve()
    paths = {'C': sources / 'codex-0.166.0', 'X': sources / 'xean', 'R': sources / 'rethlas-upstream-887cc46'}
    result = {}
    for arm, path in paths.items():
        try:
            result[arm] = {**source_identity(path, arm), 'source_verified': True}
            result[arm].pop('path')
        except (NativeBlocked, OSError) as error:
            result[arm] = {**PINS[arm], 'source_verified': False, 'reason': str(error)}
    result['M'] = {'repository': 'https://github.com/ChangyuZeng/DSH-Math-Dev',
                   'base_commit': 'ffc582c52585d958bcb3198259ea27c69ee6f8bb', 'version': '0.11.0',
                   'source_hashes': {name: sha(REPO / name) for name in
                       ['workflow_runner.mjs', 'workflow_store.py', 'workflow_service.py', 'workflow_context.py']}}
    return result


def smoke_plan():
    return {'format': 'four-harness-smoke-proposal-v1', 'authorized': False, 'executable': False,
            'target_problem_allowed': False, 'model': POLICY, 'fallback_allowed': False,
            'prompt': TOY, 'protocol': PROTOCOL, 'information_policy': INFORMATION_POLICY,
            'cold_input': input_manifest('smoke'), 'resource_policy': RESOURCE_POLICY,
            'common_tools': responses_tools('smoke'), 'target_checker_and_submission_allowed': False,
            'per_run_budget': SMOKE_BUDGET, 'max_requests_total': 8, 'max_tokens_total': 524288,
            'entry': 'qualify.py launch --scope smoke --arm all --manifest <authorized-smoke-freeze> --output <fresh-output>',
             'arms': [{'id': arm, 'max_requests': 2} for arm in 'CXRM'],
            'acceptance': ['exact provider/model/effort and raw usage for every physical role request',
                           'one common compute or material interaction reaches a native continuation',
                           'two-request run and eight-request campaign ceiling, no retry/replacement',
                           'incomplete native multi-role chain is reported, not replaced with a new loop'],
            'limits': ['Two requests cannot prove full Xean/Rethlas multi-role or long-context behavior',
                       'Resolver, provider output semantics, strict information isolation and native paths remain prerequisite gates; CPU/RSS are observations with separate host protection'],
            'authorization': 'Requires a new explicit human authorization after the concrete manifest and failed gates are reviewed'}


def prompt_inventory(sources, identities):
    """Pin native source templates; runtime assembled requests get separate hashes."""
    roots = {'C': Path(sources) / 'codex-0.166.0', 'X': Path(sources) / 'xean',
             'R': Path(sources) / 'rethlas-upstream-887cc46'}
    inventories = {}
    for arm, root in roots.items():
        if not identities[arm]['source_verified']:
            inventories[arm] = None
            continue
        tracked = subprocess.run(['/usr/bin/git', '-C', str(root), 'ls-tree', '-r', '--name-only', 'HEAD'],
                                 check=True, capture_output=True, text=True).stdout.splitlines()
        if arm == 'C':
            selected = [p for p in tracked if p == 'codex-rs/models-manager/models.json' or
                        (p.endswith('.md') and (p.endswith('_prompt.md') or '/templates/' in p or '/base_instructions/' in p))]
        elif arm == 'X':
            selected = [p for p in tracked if p.startswith(('src/policy/', 'patches/')) or
                        p in {'src/role-runtime.ts', 'src/source-runtime.ts', 'src/host.ts', 'src/workflow.ts', 'package.json', 'bun.lock'}]
        else:
            selected = [p for p in tracked if p.startswith('agents/') and
                        ('/.agents/skills/' in p or '/.codex/' in p or p.endswith(('/AGENTS.md', '/requirements.txt')))]
        inventories[arm] = {name: sha(root / name) for name in sorted(selected)}
    inventories['M'] = {name: sha(REPO / name) for name in
                        ['workflow_runner.mjs', 'workflow_context.py', 'workflow_service.py', 'workflow_store.py']}
    return inventories


def runtime_inventory():
    binary_paths = {'node': DEFAULT_NODE, 'python': Path(sys.executable).resolve(),
                    'codex': DEFAULT_CODEX.parent.parent / 'node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex',
                    'bun': REPO / 'dist/four-harness-builds/runtime/node_modules/@oven/bun-darwin-aarch64/bin/bun'}
    versions = {'node': '24.21.0', 'python': platform.python_version(), 'codex': '0.166.0', 'bun': '1.4.2'}
    binaries = {}
    for name, path in binary_paths.items():
        if not path.is_file():
            binaries[name] = {'expected_version': versions[name], 'present': False, 'qualified': False}
            continue
        result = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=10)
        observed = (result.stdout or result.stderr).strip()
        binaries[name] = {'expected_version': versions[name], 'observed_version': observed,
                          'version_matches': result.returncode == 0 and versions[name] in observed,
                          'sha256': sha(path), 'qualified': False}
    from importlib.metadata import distributions
    deps = {d.metadata['Name']: d.version for d in distributions(path=[str(REPO / 'dist/four-harness-builds/rethlas-python')])}
    r_site = REPO / 'dist/four-harness-builds/rethlas-mcp-python'
    r_installed = None
    if r_site.is_dir():
        from native import rethlas_mcp_dependencies
        r_installed = rethlas_mcp_dependencies(r_site)
    x_build = REPO / 'dist/four-harness-builds/xean-native-10'
    x_installed = None
    if (x_build / 'node_modules').is_dir():
        from x_full import _tree_manifest
        files, links = _tree_manifest(x_build)
        dependencies = {name: value for name, value in files.items() if name.startswith('node_modules/')}
        x_installed = {'file_count': len(dependencies),
                       'file_tree_sha256': hashlib.sha256(canonical(dependencies).encode()).hexdigest(),
                       'symlinks_sha256': hashlib.sha256(canonical(links).encode()).hexdigest()}
    return {'platform': platform.system(), 'platform_version': platform.release(), 'machine': platform.machine(),
            'binaries': binaries, 'rethlas_fixture_dependencies': dict(sorted(deps.items())),
            'rethlas_mcp_dependencies': r_installed, 'xean_installed_dependencies': x_installed,
            'xean_pi': {'version': '1.5.0', 'source_commit': 'f9a26365568127461c47a07a37dcb42c140a3ac5'},
            'python_libraries': 'stdlib-only for shared compute; no site packages', 'qualified': False}


def draft(sources):
    manifest = json.loads((HERE / 'manifest.template.json').read_text())
    identities = inspect_sources(sources)
    prompts = prompt_inventory(sources, identities)
    configurations = {
        'C': codex_config('http://127.0.0.1:1/C/agent/v1', mcp_command=('/frozen/python3', '-I', '/frozen/mcp.py'),
                          tools_url='http://127.0.0.1:1/C/agent/tools'),
        'X': xean_settings(),
        'R': rethlas_config('http://127.0.0.1:1', {role: '<ephemeral-capability>' for role in ROLES['R']}),
        'M': {'model_policy': POLICY, 'budget': FORMAL_BUDGET, 'workflow_base': identities['M']['base_commit']}}
    source_files = ['ledger.py', 'gateway.py', 'mcp.py', 'native.py', 'tools.py', 'compute_child.py', 'cold_start.py', 'observation.py',
                    'm_adapter.mjs', 'm_service.py', 'qualify.py', 'x_full.py', 'state_guard.py', 'transport.py']
    source_files += [name for name in ('smoke.py', 'x_live.py', 'codex_bridge.py') if (HERE / name).is_file()]
    if (HERE / 'm_bridge.mjs').is_file():
        source_files.append('m_bridge.mjs')
    source_files += [str(path.relative_to(HERE)) for path in sorted((HERE / 'patches').glob('*'))
                     if path.is_file() and path.suffix in {'.json', '.ts', '.py', '.md', '.txt'}]
    hashes = {name: sha(HERE / name) for name in source_files}
    common = ['ledger.py', 'gateway.py', 'tools.py', 'compute_child.py', 'qualify.py', 'cold_start.py', 'observation.py', 'state_guard.py', 'transport.py']
    common += [name for name in ('smoke.py',) if name in hashes]
    for arm in manifest['arms']:
        arm.update(source=identities[arm['id']], qualification='offline_evidence_required_live_blocked')
        arm['commit'] = identities[arm['id']].get('commit', identities[arm['id']].get('base_commit'))
        names = common + (['m_adapter.mjs', 'm_service.py', 'm_bridge.mjs'] if arm['id'] == 'M' else ['native.py', 'mcp.py'])
        if arm['id'] == 'X':
            names.append('x_full.py')
        helper = 'x_live.py' if arm['id'] in {'X','M'} else 'codex_bridge.py'
        if helper in hashes:
            names.append(helper)
        prefix = {'X': 'patches/xean', 'R': 'patches/rethlas'}.get(arm['id'])
        if prefix:
            names += [name for name in source_files if name.startswith(prefix)]
        arm['adapter_sha256'] = hashlib.sha256(canonical({name: hashes[name] for name in names}).encode()).hexdigest()
        arm['prompt_sources_sha256'] = prompts[arm['id']]
        arm['prompt_sha256'] = (hashlib.sha256(canonical({'sources': prompts[arm['id']],
            'adaptation': arm['adapter_sha256']}).encode()).hexdigest() if prompts[arm['id']] else None)
        arm['prompt_scope'] = 'Native source templates plus exact adaptation; assembled request payloads are hashed per request, not live-qualified'
        arm['configuration'] = configurations[arm['id']]
        arm['configuration_sha256'] = hashlib.sha256(canonical(configurations[arm['id']]).encode()).hexdigest()
    manifest['adapter_sources_sha256'] = hashes
    manifest['tool_schema_sha256'] = hashlib.sha256(canonical(responses_tools()).encode()).hexdigest()
    manifest['problem']['checker_sha256'] = sha(HERE / 'audit.py')
    manifest['live_smoke'] = smoke_plan()
    manifest['runtime'] = runtime_inventory()
    manifest['cold_input'] = input_manifest('formal')
    manifest['problem']['materials_sha256'] = manifest['cold_input']['materials_sha256']
    manifest['information_policy'] = INFORMATION_POLICY
    manifest['resource_policy'] = RESOURCE_POLICY
    manifest['pending_gates'] = ['live provider identity, pricing and request/output semantics',
                                'strict external-information isolation for complete native process trees',
                                'all native role/descendant and compaction paths reach the shared ledger',
                                'wall/artifact limits and truthful host-protection observations',
                                'canonical cold-input review; operator-only frontier refresh and independent reviewers',
                                'explicit separate smoke and formal human approvals']
    return manifest


def fixture_response(index, *, output):
    return {'id': f'resp_fixture_{index}', 'object': 'response', 'created_at': 0,
            'model': POLICY['model'], 'reasoning': {'effort': POLICY['effort']}, 'status': 'completed',
            'output': output, 'usage': {'input_tokens': 100, 'output_tokens': 80, 'total_tokens': 180,
                'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 10}}}


def fixture_acceptance(native, state, events, requests, *, call_id='call_fixture_1'):
    """Prove a successful host tool result reached the native second request."""
    receipts = [e['tool'] for e in events if e.get('type') == 'tool_finished' and
                e.get('tool', {}).get('tool') == 'materials.list' and e['tool'].get('ok') is True]
    def decoded(value):
        if isinstance(value, str):
            try:
                yield from decoded(json.loads(value))
            except (ValueError, TypeError):
                return
        elif isinstance(value, dict):
            yield value
            for key in ('content', 'text'):
                if key in value:
                    yield from decoded(value[key])
        elif isinstance(value, list):
            for item in value:
                yield from decoded(item)
    continuation = False
    if len(requests) == 2:
        context = requests[1]['payload'].get('input', [])
        if isinstance(context, list):
            for item in context:
                if not isinstance(item, dict) or item.get('type') != 'function_call_output' or item.get('call_id') != call_id:
                    continue
                for reply in decoded(item.get('output')):
                    metrics = reply.get('metrics', {})
                    if reply.get('ok') is True and isinstance(metrics, dict):
                        continuation |= any(metrics.get('request_id') == receipt.get('request_id') and
                                            metrics.get('result_sha256') == receipt.get('result_sha256') for receipt in receipts)
    checks = {'native_exit_zero': native.get('returncode') == 0,
              'run_not_stopped': state['terminal'] is None,
              'exact_two_requests': state['requests'] == 2 and len(requests) == 2,
              'usage_complete': state['accounting_complete'] and state['detail_complete'],
              'successful_common_tool': bool(receipts), 'native_tool_continuation': continuation}
    return {**checks, 'passed': all(checks.values())}


def fixture(arm, root, node, codex, *, sources=None, xean_build=None, bun=None, python_site=None, coverage='basic', tool_scope='offline'):
    if tool_scope not in {'offline', 'smoke'} or (tool_scope == 'smoke' and (arm not in {'C', 'M'} or coverage != 'basic')):
        raise GateError('TOOL_SCOPE', 'This smoke scope entry supports C/M basic fake fixtures only')
    root = Path(root).resolve()
    if coverage == 'native':
        if arm != 'R':
            raise GateError('COVERAGE', 'Native coverage is the original R commands with installed Codex; C basic already uses native Codex')
        from native import rethlas_codex_fixture
        result = rethlas_codex_fixture(root, source_root=Path(sources) / 'rethlas-upstream-887cc46',
                                      python_executable=sys.executable, python_site=python_site)
        result.update(arm=arm, mode='offline_native_codex_startup_probe', real_model_requests=0,
                      physical_fake_requests=len(result['requests']), passed=result['native_qualified'])
        new_json(root / 'report.json', result)
        return result
    if coverage == 'full':
        if arm == 'X':
            from x_full import xean_full_fixture
            result = xean_full_fixture(root, source_copy=xean_build, bun=bun)
            acceptance = {'native_host_path': result['native_host_path_qualified'],
                          'native_parent_links': result['parent_linkage_qualified']}
        elif arm == 'R':
            from native import rethlas_mcp_fixture
            result = rethlas_mcp_fixture(root, source_root=Path(sources) / 'rethlas-upstream-887cc46',
                                        python_executable=sys.executable, python_site=python_site)
            state = result['snapshot']
            output = json.loads(result['native']['stdout']) if result['native']['returncode'] == 0 else {}
            acceptance = {'native_mcp_exit_zero': result['native']['returncode'] == 0,
                          'native_mcp_assertions': output.get('mode') == 'offline_original_mcp_fixture',
                          'exact_three_fixture_requests': state['requests'] == len(result['requests']) == 3,
                          'native_entry_roles': [row['role'] for row in result['requests']] == ['generator', 'strategy', 'verifier'],
                          'parent_links': all(row['parent'] for row in state['request_states'][1:]),
                          'usage_complete': state['accounting_complete'] and state['detail_complete'],
                          'run_not_stopped': state['terminal'] is None}
        else:
            raise GateError('COVERAGE', 'Full coverage entry applies only to native X host or R MCP fixtures')
        acceptance['passed'] = all(acceptance.values())
        result.update(arm=arm, mode='offline_native_path_fixture', real_model_requests=0,
                      physical_fake_requests=len(result['requests']), acceptance=acceptance, passed=acceptance['passed'])
        new_json(root / 'report.json', result)
        return result
    if arm in {'X', 'R'}:
        from native import xean_fixture, rethlas_fixture
        result = (xean_fixture(root, source_copy=xean_build, bun=bun) if arm == 'X' else
                  rethlas_fixture(root, source_root=Path(sources) / 'rethlas-upstream-887cc46',
                                  python_executable=sys.executable, python_site=python_site))
        state, requests, events = result['snapshot'], result['requests'], result['events']
        if arm == 'X':
            acceptance = fixture_acceptance(result['native'], state, events, requests, call_id='call_x_1')
        else:
            checks = {'native_entry_exit_zero': result['native']['returncode'] == 0,
                      'run_not_stopped': state['terminal'] is None,
                      'three_fake_child_requests': state['requests'] == len(requests) == 3,
                      'usage_complete': state['accounting_complete'] and state['detail_complete'],
                      'native_entry_roles': [r['role'] for r in requests] == ['generator', 'generator', 'verifier']}
            acceptance = {**checks, 'passed': all(checks.values())}
        result.update(arm=arm, mode='offline_partial_native_fixture', real_model_requests=0,
                      physical_fake_requests=len(requests), acceptance=acceptance, passed=acceptance['passed'])
        new_json(root / 'report.json', result)
        return result
    if root.exists():
        raise GateError('FRESH_RUN', 'Offline fixture output directory must not exist')
    root.mkdir(parents=True, mode=0o700)
    ledger = Ledger(root / 'host' / 'ledger.sqlite')
    budget = dict(SMOKE_BUDGET) if tool_scope == 'smoke' else {**FORMAL_BUDGET, 'model_requests': 2, 'total_tokens': 131072}
    ledger.create_campaign('offline-fixture', max_requests=2, max_tokens=budget['total_tokens'], scope=tool_scope)
    ledger.create_run(arm + '1', 'offline-fixture', arm, budget=budget)
    materials = write_materials(root / 'materials', 'smoke')
    limits = ToolLimits(timeout_seconds=budget['tool_timeout_seconds'], artifact_bytes=budget['artifact_bytes'])
    tools = CommonTools(arm + '1', root / 'tools', materials, limits=limits,
                        accounting=lambda event: ledger.tool_event(arm + '1', event))
    role = 'agent' if arm == 'C' else 'worker'
    tool_name = 'mcp__common__materials_list' if arm == 'C' else 'materials_list'
    proposal = {'summary': 'Synthetic arithmetic fixture finished', 'changes': [], 'next_action': 'stop',
                'next_target': None, 'progress': 'new_argument', 'limitations': ['Synthetic candidate only']}
    transport = FixtureTransport([
        fixture_response(1, output=[{'id': 'fc_fixture_1', 'type': 'function_call', 'call_id': 'call_fixture_1',
                                     'name': tool_name, 'arguments': '{}', 'status': 'completed'}]),
        fixture_response(2, output=[{'id': 'msg_fixture_2', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                                     'content': [{'type': 'output_text', 'annotations': [],
                                                  'text': 'fixture complete' if arm == 'C' else json.dumps(proposal)}]}])])
    token = capability()
    common_names = {tool['name'] for tool in responses_tools(tool_scope)}
    allowed = {f'mcp__common__{name}' for name in common_names} if arm == 'C' else common_names
    with Gateway(ledger, transport=transport, bindings={token: {'run_id': arm + '1', 'arm': arm, 'role': role, 'require_native_metadata': arm == 'C'}},
                 tools={arm + '1': tools}, allowed_tools={arm + '1': allowed}, inject_output_cap=True) as gw:
        if arm == 'C':
            native = codex_fixture(root / 'native', gateway=gw, token=token,
                                  mcp_command=[sys.executable, '-I', str(HERE / 'mcp.py')], node=node, codex=codex)
        else:
            bridge = HERE / 'm_bridge.mjs'
            if not bridge.is_file():
                raise GateError('ADAPTER_INCOMPLETE', 'M gateway bridge not yet available')
            workflow_root = Path(tempfile.mkdtemp(prefix='four-harness-m-')).resolve()
            env = {'PATH': os.pathsep.join([str(Path(node).parent), '/usr/bin', '/bin']),
                   'FOUR_HARNESS_TOKEN': token, 'HOME': str(root), 'TMPDIR': str(workflow_root.parent), 'LANG': 'C.UTF-8'}
            native = run_process([str(node), str(bridge), '--offline', '--gateway', gw.url,
                                  '--token-env', 'FOUR_HARNESS_TOKEN', '--root', str(workflow_root), '--run-id', arm + '1', '--python', sys.executable],
                                 env=env, cwd=REPO, timeout=120)
            native['workflow_root'] = str(workflow_root)
        state = ledger.snapshot(arm + '1')
        events = ledger.events()
        acceptance = fixture_acceptance(native, state, events, transport.requests)
        result = {'arm': arm, 'mode': 'offline_fixture', 'tool_scope': tool_scope, 'real_model_requests': 0, 'native': native,
                  'ledger': state, 'events': events, 'common_tools': tools.capabilities(),
                  'physical_fake_requests': len(transport.requests), 'acceptance': acceptance,
                  'passed': acceptance['passed']}
    new_json(root / 'report.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'freeze-draft'):
        p = sub.add_parser(name); p.add_argument('--sources', default=str(REPO / 'dist/four-harness-sources'))
        if name == 'freeze-draft': p.add_argument('--output', required=True)
    p = sub.add_parser('baseline'); p.add_argument('certificate')
    p = sub.add_parser('fixture'); p.add_argument('arm', choices=list('CXRM')); p.add_argument('--output', required=True)
    p.add_argument('--node', default=str(DEFAULT_NODE)); p.add_argument('--codex', default=str(DEFAULT_CODEX))
    p.add_argument('--sources', default=str(REPO / 'dist/four-harness-sources'))
    p.add_argument('--xean-build', default=str(REPO / 'dist/four-harness-builds/xean-native-10'))
    p.add_argument('--bun', default=str(REPO / 'dist/four-harness-builds/runtime/node_modules/@oven/bun-darwin-aarch64/bin/bun'))
    p.add_argument('--coverage', choices=['basic', 'full', 'native'], default='basic',
                   help='Full: original X host or R MCP paths; native: original R startup with installed Codex, still fake transport')
    p.add_argument('--python-site', help='Isolated R dependency directory; defaults to the selected fixture dependency set')
    p.add_argument('--tool-scope', choices=['offline', 'smoke'], default='offline',
                   help='C/M basic fake fixtures can exercise the cold smoke budget and five-tool boundary')
    sub.add_parser('smoke-plan')
    p = sub.add_parser('prepare-cold'); p.add_argument('--scope', choices=['formal', 'smoke'], default='formal')
    p.add_argument('--output', required=True)
    p = sub.add_parser('launch'); p.add_argument('--scope', choices=['smoke', 'formal'], required=True)
    p.add_argument('--arm', choices=list('CXRM')+['all'], required=True); p.add_argument('--manifest', required=True)
    p.add_argument('--output', help='Fresh persistent output for the single authorized smoke campaign')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect': result = inspect_sources(args.sources)
        elif args.command == 'smoke-plan': result = smoke_plan()
        elif args.command == 'prepare-cold': result = prepare_cold_start(args.output, scope=args.scope)
        elif args.command == 'freeze-draft':
            result = draft(args.sources); new_json(args.output, result)
        elif args.command == 'baseline':
            certificate = json.loads(Path(args.certificate).read_text())
            checked = validate_witness(certificate)
            if checked['H'] != 1179648:
                raise GateError('BASELINE_MISMATCH', 'Certificate does not reproduce the published 9D/8 baseline')
            result = {**checked, 'certificate_sha256': sha(args.certificate), 'checker_sha256': sha(HERE / 'audit.py'),
                      'interpretation': 'public baseline replay only; no search or new target result'}
        elif args.command == 'fixture':
            python_site = args.python_site or str(REPO / 'dist/four-harness-builds' /
                                                   ('rethlas-mcp-python' if args.coverage in {'full', 'native'} else 'rethlas-python'))
            result = fixture(args.arm, args.output, args.node, args.codex, sources=args.sources,
                             xean_build=args.xean_build, bun=args.bun, python_site=python_site,
                             coverage=args.coverage, tool_scope=args.tool_scope)
        else:
            manifest = json.loads(Path(args.manifest).read_text())
            if args.scope != 'smoke' or args.arm != 'all' or not args.output:
                raise GateError('LIVE_NOT_AUTHORIZED', 'Formal execution is closed. Smoke requires one frozen all-arm campaign and fresh output.')
            from smoke import run_campaign
            result = run_campaign(manifest,args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 2 if args.command == 'fixture' and not result['passed'] else 0
    except (GateError, NativeBlocked, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'ok': False, 'code': getattr(error, 'code', 'PREFLIGHT_FAILED'), 'message': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

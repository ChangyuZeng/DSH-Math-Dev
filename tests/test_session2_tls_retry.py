"""Offline composition: real native DSH and production math loops over fake TLS.

No auth files or upstream sockets are used. All mutable evidence is retained in a
fresh plain/.offline/tls-retry-* directory. Set S2_TLS_BASELINE_LEDGER=1 to load
the unmodified e8386a6 ledger AND transport in-memory for pre-repair RED.
"""
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
PLAIN = SOURCE / 'evaluation/session2/plain'
CORE = SOURCE / 'evaluation/four-harness-v1'
# Discovery runs alongside suites that use another bare module named protocol.
# Keep all native/runtime imports and sys.path changes in the child interpreter.
if __name__ != '__main__':
    class Session2TLSRetryDiscovery(unittest.TestCase):
        def test_real_loops_in_isolated_child(self):
            before_path = list(sys.path)
            before_modules = {name: sys.modules.get(name) for name in
                              ('protocol', 'terminal', 'gateway', 'ledger', 'transport')}
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve())],
                cwd=SOURCE, capture_output=True, text=True, timeout=300,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertEqual(sys.path, before_path)
            for name, module in before_modules.items():
                self.assertIs(sys.modules.get(name), module, name+' changed in discovery parent')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Ran 6 tests', result.stderr)
            self.assertIn('TLS_RETRY_EVIDENCE=', result.stdout)
            print(result.stdout, end='', flush=True)
else:
    sys.path[:0] = [str(CORE), str(PLAIN), str(SOURCE), str(SOURCE / 'tests')]
    if os.environ.get('S2_TLS_BASELINE_LEDGER') == '1':
        for name in ('ledger','transport'):
            old = subprocess.run(['git','show','e8386a6:evaluation/four-harness-v1/'+name+'.py'],
                                 cwd=SOURCE,capture_output=True,text=True,check=True).stdout
            module = types.ModuleType(name)
            module.__file__ = str(CORE/(name+'.py'))
            sys.modules[name] = module
            exec(compile(old,'<e8386a6-'+name+'>','exec'),module.__dict__)

    from gateway import Gateway
    from ledger import Ledger, OPTIMIZATION_BUDGET
    from transport import HostTransport, Credential, _DeadlineHTTPSConnection
    from tools import CommonTools, ToolLimits
    from native import isolated_environment, macos_fixture_profile, run_process
    from bridge import DirectCommonTools
    import terminal as plain
    from test_four_harness_transport import FakeConnection, terminal as sse_terminal
    from backend import Projects
    from workflow_runtime import WorkflowRuntime, qualified_spec, certificate_contract, rendered_checker, CANDIDATE_SCHEMA
    from workflow_service import dispatch_internal


    def witness():
        return {'values': [int((j-i) % 17 in {1, 2, 4, 8, 9, 13, 15, 16})
                           for i in range(17) for j in range(i+1, 17)]}


    def tool_response(index, name, args):
        return plain.fixture_response([{'type': 'function_call', 'id': 'fc' + str(index),
            'call_id': 'fixture-call-' + str(index), 'name': name,
            'arguments': json.dumps(args), 'status': 'completed'}])


    def save(path, value):
        path.write_text(json.dumps(value, indent=2, default=str) + '\n')


    class OfflineTLSConnection(_DeadlineHTTPSConnection):
        """Instrumented connection identity; all I/O methods are fake and local."""
        def __init__(self, raw, eof):
            FakeConnection.__init__(self, raw=raw)
            self.eof = eof

        def connect(self):
            self.diagnostics.enter('tls')
            if self.eof:
                error = ssl.SSLEOFError(8, 'offline TLS EOF')
                error.reason = 'UNEXPECTED_EOF_WHILE_READING'
                raise error
            self.connected = True

        request = FakeConnection.request
        getresponse = FakeConnection.getresponse
        close = FakeConnection.close


    class Connections:
        """Real HostTransport parser/receipt path; fake connection has no socket I/O."""
        def __init__(self, scenario):
            responses = [tool_response(1, 'compute', {'code': 'print(2+3)'}),
                         tool_response(2, 'check', {'candidate': witness()}),
                         tool_response(3, 'submit', {'candidate': witness()})]
            if scenario == 'missing':
                responses[0].pop('usage')
            self.queue = ['EOF'] * (3 if scenario == 'exhausted' else 1) + responses
            self.connections = []
            self.lock = threading.Lock()

        def __call__(self, *args, **kwargs):
            with self.lock:
                if not self.queue:
                    raise AssertionError('Unexpected extra physical request')
                value = self.queue.pop(0)
                connection = OfflineTLSConnection(b'' if value == 'EOF' else sse_terminal(value), value == 'EOF')
                self.connections.append(connection)
                return connection

        def transport(self, **options):
            return HostTransport(profile='workflow', max_output_tokens=128000,
                auth_loader=lambda: Credential('offline-token', 'offline-account', time.time()*1000+60000),
                connection_factory=self, **options)


    NODE_DRIVER = r'''
    const {readFile} = await import('node:fs/promises');
    const {pathToFileURL} = await import('node:url');
    const p=JSON.parse(await readFile(process.argv[1], 'utf8'));
    const {createWorkflowCall}=await import(pathToFileURL(p.source+'/workflow_runtime.mjs'));
    const {createMeteredPorts}=await import(pathToFileURL(p.source+'/workflow_ports.mjs'));
    const {executeToolLoop}=await import(pathToFileURL(p.source+'/workflow_executor.mjs'));
    const call=createWorkflowCall({root:p.root,python:p.python});
    const ports=createMeteredPorts({gateway:p.ready.gateway,bindings:p.ready.bindings});
    let failure=null,run;
    try {
     run=await call('workflow-state',{action:'read',project_id:p.project,run_id:p.run});
     run=await executeToolLoop({call,...ports,projectId:p.project,runId:p.run,expectedRev:run.rev,kind:'derive'});
    } catch(e) {failure={code:e.code,message:e.message};
     run=await call('workflow-state',{action:'read',project_id:p.project,run_id:p.run});}
    console.log(JSON.stringify({run,failure}));
    if(failure)process.exitCode=1;
    '''


    class Session2TLSRetry(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.root = PLAIN / '.offline' / ('tls-retry-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
            cls.root.mkdir(parents=True)
            save(cls.root / 'scope.json', {'fixture_only': True, 'upstream_requests': 0,
                'baseline_ledger': os.environ.get('S2_TLS_BASELINE_LEDGER') == '1',
                'layers': ['HostTransport', 'Gateway', 'Ledger', 'CommonTools', 'ReactLoopAgent', 'executeToolLoop']})
            print('TLS_RETRY_EVIDENCE=' + str(cls.root), flush=True)

        @classmethod
        def tearDownClass(cls):
            cases = [json.loads(path.read_text()) for path in sorted(cls.root.glob('*/assertions.json'))]
            save(cls.root/'tests.json',{'passed':len(cases)==6,'cases':cases,'fixture_only':True,'upstream_requests':0,
                 'baseline_modules':['ledger','transport'] if os.environ.get('S2_TLS_BASELINE_LEDGER')=='1' else []})

        def native(self, directory, factory):
            isolated = directory / 'isolated'
            isolated.mkdir()
            task = directory / 'task.json'
            card = certificate_contract('S1')['task']
            save(task, {k: card[k] for k in ('id', 'statement', 'parameters', 'certificate')})
            checker = directory / 'checker.py'
            checker.write_bytes(rendered_checker(card))
            run = 'native-fixture'
            ledger = Ledger(directory / 'ledger.sqlite')
            ledger.create_campaign(run, max_requests=120, max_tokens=None, scope='optimization')
            ledger.create_run(run, run, 'M', budget=OPTIMIZATION_BUDGET)
            host = CommonTools(run, directory / 'common', materials={'task.json': {'path': task, 'sha256': plain.sha(task)}},
                checker_path=checker, limits=ToolLimits(cpu_seconds=None, timeout_seconds=None, artifact_bytes=1073741824))
            token = 'offline-native-capability'
            audit = directory / 'audit'
            audit.mkdir()
            guard = plain.StateGuard([directory], max_bytes=1073741824, on_stop=lambda reason: ledger.stop(run, reason))
            gateway = Gateway(ledger, transport=factory.transport(), state_guards={run: guard},
                bindings={token: {'run_id': run, 'arm': 'M', 'role': 'worker'}}, tools={run: host},
                allowed_tools={run: set(plain.TOOL_NAMES) if hasattr(plain, 'TOOL_NAMES') else
                               {'materials_list','materials_read','workspace_read','workspace_write','compute','check','submit'}},
                inject_output_cap=True, frozen_materials={run: host.material_inventory()}, candidate_schema=CANDIDATE_SCHEMA,
                execution={'mode':'authorized_optimization','arm':'M','run_id':run,'campaign_id':run,
                           'model_requests':120,'campaign_max_requests':120},
                audit_sink=lambda kind, rid, value: save(audit / (rid+'.'+kind+'.json'), value))
            gateway.request_adapters[(run, 'worker')] = DirectCommonTools(gateway.manifest_tools(run))
            gateway.start()
            guard.start()
            config = {'modules': str(plain.MODULES), 'gateway': gateway.url+'/M/worker', 'sessionId': run,
                      'events': str(isolated/'native-events.jsonl'), 'prompt': 'Offline public certificate fixture: '+task.read_text(),
                      'tools': [{k:v for k,v in tool.items() if k!='type'} for tool in gateway.manifest_tools(run)]}
            save(isolated/'config.json', config)
            driver = isolated/'runner.mjs'
            driver.write_bytes((PLAIN/'runner.mjs').read_bytes())
            profile = isolated/'sandbox.sb'
            profile.write_text(macos_fixture_profile(isolated, readable=[plain.MODULES, plain.NODE.parent.parent], gateway_url=gateway.url))
            env = isolated_environment(isolated, token=token)
            env['DSH_HOME'] = str(isolated/'home')
            try:
                process = run_process(['/usr/bin/sandbox-exec','-f',profile,plain.NODE,driver,isolated/'config.json'],
                                      env=env,cwd=isolated,timeout=60,capture_dir=directory/'captures')
            finally:
                gateway.close()
                save(directory/'state-guard.json', guard.stop())
            save(directory/'process.json',process)
            events = [json.loads(line) for line in (isolated/'native-events.jsonl').read_text().splitlines()]
            self.assertTrue(any(e['type']=='engine_identity' and e['constructor']=='ReactLoopAgent' and not e['math_plugin'] for e in events))
            return ledger, run, host, {'returncode':process['returncode'], 'events':events}

        def math(self, directory, factory):
            root = directory/'store'
            project = Projects(root).create('offline-session', certificate_contract('S1')['problem'])['id']
            run = 'math-fixture'
            state = dispatch_internal(root,'workflow-state',{'action':'prepare','project_id':project,'run_id':run,
                'operation_id':'fixture-prepare','expected_rev':0,'spec':qualified_spec(project,'S1','model-directed-v1')})
            dispatch_internal(root,'workflow-state',{'action':'start','project_id':project,'run_id':run,
                'operation_id':'fixture-start','expected_rev':state['rev']})
            original = HostTransport.__init__
            def injected(instance, **kwargs):
                kwargs.update(auth_loader=lambda:Credential('offline-token','offline-account',time.time()*1000+60000),connection_factory=factory)
                original(instance, **kwargs)
            runtime = WorkflowRuntime(root,project,run)
            with patch.object(HostTransport,'__init__',injected):
                ready = runtime.start()
            packet = directory/'node-packet.json'
            save(packet,{'source':str(SOURCE),'root':str(root),'python':sys.executable,'project':project,'run':run,'ready':ready})
            try:
                process = subprocess.run([str(plain.NODE),'--input-type=module','-e',NODE_DRIVER,str(packet)],
                                         cwd=SOURCE,capture_output=True,text=True,timeout=60,
                                         env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                save(directory/'node-process.json',{'returncode':process.returncode,'stdout':process.stdout,'stderr':process.stderr})
                output = json.loads(process.stdout)
            finally:
                runtime.close()
            save(directory/'workflow.json',output)
            return runtime.ledger,run,runtime.host,{'returncode':process.returncode,'workflow':output}

        def case(self, arm, scenario):
            directory = self.root/(arm+'-'+scenario)
            directory.mkdir()
            factory = Connections(scenario)
            ledger, run, host, result = getattr(self,arm)(directory,factory)
            snapshot, events = ledger.snapshot(run), ledger.events()
            save(directory/'snapshot.json',snapshot)
            save(directory/'events.json',events)
            save(directory/'result.json',result)
            self.assertIsNone(snapshot['total_tokens'])
            self.assertFalse(snapshot['accounting_complete'])
            rows = snapshot['request_states']
            self.assertEqual(len({r['id'] for r in rows}),len(rows))
            self.assertEqual(rows[0]['state'],'unknown')
            self.assertIsNone(rows[0]['usage'])
            self.assertIsNone(rows[0]['raw_usage'])
            self.assertGreater(rows[0]['reservation'],0)
            reservations = [e for e in events if e['type']=='request_reserved']
            self.assertEqual([e['request_id'] for e in reservations], [r['id'] for r in rows])
            self.assertIsNone(reservations[0]['retry_of'])
            for index in range(1, min(len(rows),3 if scenario=='exhausted' else 2)):
                self.assertEqual(reservations[index]['retry_of'], rows[index-1]['id'])
                self.assertIsNone(rows[index]['parent'])
            expected_failure = 'tls_protocol' if os.environ.get('S2_TLS_BASELINE_LEDGER')=='1' else 'tls_unexpected_eof'
            tls = [e['evidence'] for e in events if e['type']=='transport_evidence' and
                   e.get('evidence',{}).get('failure_kind')==expected_failure]
            self.assertEqual(len(tls),3 if scenario=='exhausted' else 1)
            self.assertTrue(all(e['stage']=='tls' and e['send_state']=='not_started' for e in tls))
            self.assertEqual(len(factory.connections),snapshot['requests'])
            self.assertTrue(all(not c.sent for c in factory.connections[:(3 if scenario=='exhausted' else 1)]))
            submitted,_,_ = host.submissions.inventory()
            receipts = [json.loads(host.submissions.read(name)) for name,_ in submitted if name.startswith('receipt-')]
            if scenario == 'success':
                self.assertEqual(result['returncode'],0,'Inspect '+str(directory/'result.json'))
                self.assertEqual(snapshot['requests'],4)
                self.assertEqual([r['state'] for r in rows],['unknown','finished','finished','finished'])
                self.assertEqual([r['parent'] for r in rows],[None,None,rows[1]['id'],rows[2]['id']])
                self.assertEqual([e['retry_of'] for e in reservations],[None,rows[0]['id'],None,None])
                self.assertEqual(snapshot['known_total_tokens'],330)
                self.assertEqual(snapshot['charged_or_reserved_tokens'],rows[0]['reservation']+330)
                contexts = [e for e in events if e['type']=='native_tool_context']
                self.assertEqual([e['tool_name'] for e in contexts],['compute','check','submit'])
                self.assertEqual([e['parent_request_id'] for e in contexts],[r['id'] for r in rows[1:]])
                finished = [e for e in events if e['type']=='tool_finished']
                self.assertEqual(len(finished),3)
                self.assertTrue(all(e['tool']['metrics']['ok'] for e in finished))
                self.assertEqual(snapshot['resource_observations']['tool_count'],3)
                if arm=='native':
                    self.assertEqual(sum(e['type']=='native_model_call' for e in result['events']),3)
                    self.assertTrue(json.loads((directory/'process.json').read_text())['cleanup_complete_for_known_processes'])
                    replies = [json.loads(p.read_text()) for p in (directory/'audit').glob('*.tool_response.json')]
                    self.assertTrue(any(r.get('result',{}).get('stdout')=='5\n' for r in replies))
                else:
                    workflow = result['workflow']['run']
                    self.assertEqual(len(workflow['physical_requests']),4)
                    self.assertIsNone(workflow['usage']['total_tokens'])
                    self.assertEqual(workflow['usage']['unknown_requests'],1)
                    self.assertEqual(sum(e['type']=='model_started' for e in workflow['execution_events']),3)
                    self.assertEqual([e['parent_request_id'] for e in workflow['execution_events'] if e['type']=='tool_started'],
                                     [r['id'] for r in rows[1:]])
                self.assertTrue(any(r.get('independent_check',{}).get('target_met') is True and
                                    r.get('verification_basis')=='fresh_process_exact_audit_rerun' for r in receipts))
            else:
                self.assertNotEqual(result['returncode'],0)
                self.assertEqual(snapshot['requests'],3 if scenario=='exhausted' else 2)
                self.assertFalse(receipts)
                self.assertTrue(all(r['state']=='unknown' for r in rows))
                self.assertEqual(snapshot['known_total_tokens'],0)
                self.assertEqual(snapshot['charged_or_reserved_tokens'],sum(r['reservation'] for r in rows))
                self.assertEqual(snapshot['resource_observations']['tool_count'],0)
                self.assertFalse(any(e['type']=='native_tool_context' for e in events))
                if scenario=='missing':
                    self.assertEqual(snapshot['terminal'],'usage_unknown')
                    self.assertIsNone(json.loads(rows[-1]['raw_usage']))
                    self.assertIsNone(json.loads(rows[-1]['usage'])['total_tokens'])
                if arm=='native':
                    self.assertEqual(sum(e['type']=='native_model_call' for e in result['events']),1)
                else:
                    workflow = result['workflow']['run']
                    self.assertEqual(len(workflow['physical_requests']),len(rows))
                    self.assertIsNone(workflow['usage']['total_tokens'])
                    self.assertEqual(sum(e['type']=='model_started' for e in workflow['execution_events']),1)
            save(directory/'assertions.json',{'passed':True,'arm':arm,'scenario':scenario,'fixture_only':True,
                 'upstream_requests':0,'physical_requests':snapshot['requests'],'known_total_tokens':snapshot['known_total_tokens'],
                 'total_tokens':snapshot['total_tokens'],'unknown_reservation_retained':True,
                 'retry_chain_and_tool_parent_checked':True})
            return directory

        def test_native_success(self): self.case('native','success')
        def test_math_success(self): self.case('math','success')
        def test_native_exhausted(self): self.case('native','exhausted')
        def test_math_exhausted(self): self.case('math','exhausted')
        def test_native_missing(self): self.case('native','missing')
        def test_math_missing(self): self.case('math','missing')


    if __name__ == '__main__':
        unittest.main()

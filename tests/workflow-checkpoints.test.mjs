import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, realpathSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createWorkflowCall} from '../workflow_runtime.mjs';
import {executeToolStep} from '../workflow_executor.mjs';
import {parseExact} from '../workflow_ports.mjs';

const source = fileURLToPath(new URL('..', import.meta.url));
const python = process.env.PYTHON ?? 'python3';

test('product executor consumes authorized persistent checkpoint in a later action', async t => {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'workflow-checkpoint-')));
  t.after(() => { assert.equal(realpathSync(directory), directory); rmSync(directory, {recursive: true}); });
  const code = `import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_workflow_checkpoints import SearchCheckpointTests
fixture=SearchCheckpointTests()
fixture.setUp(Path(sys.argv[1]))
checkpoint=fixture.archive()
fixture.finish()
print(json.dumps({'root':str(fixture.f.root),'projectId':fixture.f.project,'runId':'r1','checkpoint':checkpoint,'sourceAction':fixture.f.action_id}))`;
  const result = spawnSync(python, ['-B', '-c', code, join(directory, 'store')], {cwd: source, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  const fixture = {...parseExact(result.stdout), python};
  const call = createWorkflowCall(fixture);
  const before = await call('workflow-state', {action: 'read', project_id: fixture.projectId, run_id: fixture.runId});
  let actualInput;
  const modelPort = {
    prepare: async () => ({tools: []}),
    complete: async ({request, role}) => {
      actualInput = parseExact(request.input[0].content[0].text);
      return {requestId: 'checkpoint-consumer', response: {model: before.spec.model_policy.model, status: 'completed',
        output: [{type: 'message', content: [{type: 'output_text', text: JSON.stringify({
          summary: 'Offline checkpoint input observed', changes: [], next_action: 'stop', next_target: null,
          progress: 'no_progress', limitations: ['No model inference or mathematical improvement measured.']})}]}]},
        accounting: {format: 'dsh-math-physical-accounting-v2', requests: [{request_id: 'checkpoint-consumer',
          logical_request_id: 'checkpoint-consumer', parent_request_id: null, role, status: 'finished', usage_complete: true,
          usage: {input_tokens: 1, output_tokens: 1, total_tokens: 2, cached_input_tokens: null, reasoning_tokens: null}, raw_usage: {}}]}};
    },
  };
  const ended = await executeToolStep({call, modelPort, toolPort: {dispatch: async () => assert.fail('No tool requested')},
    projectId: fixture.projectId, runId: fixture.runId, expectedRev: before.rev, kind: 'derive', checkpointRefs: [fixture.checkpoint]});
  const loaded = actualInput.search_checkpoints?.[0];
  assert.ok(loaded, 'Selected checkpoint must reach real executor model input');
  assert.equal(loaded.source_action_id, fixture.sourceAction);
  assert.equal(loaded.verification.verdict, 'fail');
  assert.equal(loaded.materials.seed.attachments[0].text, '{"seed":39,"parameters":{"steps":0}}');
  assert.equal(loaded.read_evidence.decision_influence, 'not_observed');
  assert.equal(ended.actions.length, 2);
  const next = ended.actions.at(-1);
  assert.notEqual(next.action_id, fixture.sourceAction);
  assert.deepEqual(parseExact(next.context_snapshot.text).search_checkpoints[0], loaded);
  assert.equal(next.status, 'committed');
});

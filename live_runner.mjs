import {readFileSync, writeFileSync, mkdirSync, existsSync, symlinkSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
export async function run(manifestPath, {sdkPath, codexPath, python = 'python3', fakePlugin = null} = {}) {
  const manifest = JSON.parse(readFileSync(manifestPath));
  manifest.provider ??= 'openai-codex';
  const output = manifest.output_dir;
  const profileHome = join(output, 'profile');
  const profile = join(profileHome, 'profiles', 'research');
  mkdirSync(profile, {recursive: true});
  mkdirSync(manifest.workspace, {recursive: true});
  const desktopProfile = '/Users/zcy/.dsh/profiles/desktop';
  try { mkdirSync(join(profile, 'node_modules')); } catch {}
  for (const name of ['dsh-codex', '@earendil-works', '@deepseek-ai', 'react', 'react-dom', 'undici', 'zod']) {
    const link = join(profile, 'node_modules', name);
    if (existsSync(link)) continue;
    for (const source of [join(desktopProfile, 'node_modules', name), join('/Users/zcy/.dsh/profiles/node_modules', name)]) {
      if (existsSync(source)) { try { symlinkSync(source, link, 'dir'); } catch {} break; }
    }
  }
  writeFileSync(join(profile, 'package.json'), JSON.stringify({name: 'dsh-math-run-profile', private: true, dsh: {profile: {bundles: ['@deepseek-ai/dsh-sdk-minimal']}}}));
  writeFileSync(join(profile, 'cordis.yml'), '[]\n');
  writeFileSync(join(profile, 'cordis.patch.yml'), '[]\n');
  const disabled = ['llm-deepseek', 'persistent-bash', 'persistent-pwsh', 'tool-fs', 'tool-working-directory', 'compaction-basic', 'llm-retry', 'session-title-llm'];
  const patch = disabled.map(id => ({id, disabled: true}));
  patch.push({id: 'sessions', config: {root: join(output, 'sessions'), compression: 'none'}});
  patch.push({insert: [{id: 'math-web', name: '@deepseek-ai/dsh-web'},
    ...(fakePlugin ? [{id: 'math-fake', name: fakePlugin, config: {manifest: manifestPath}}] : [{id: 'math-codex', name: codexPath,
      config: {credentialFile: join(process.env.HOME, '.dsh', '.openai-codex-auth.json'), models: [manifest.model], automaticModelFallback: false, modifyReadImage: false, shareImagegenWithOtherModels: false,
        useWebSocketContextReuse: false, useNativeCompaction: false, fastModeDefault: false}}]),
    {id: 'math-live', name: join(here, 'live_plugin.mjs'), config: {root: join(output, 'data'), python, manifest: manifestPath}}]});
  const patchPath = join(output, 'runtime.patch.json');
  writeFileSync(patchPath, JSON.stringify(patch, null, 2));
  const {HarnessClient} = await import(pathToFileURL(sdkPath).href);
  const env = Object.fromEntries(['HOME', 'PATH', 'TMPDIR', 'LANG', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'].filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]]));
  const client = new HarnessClient({dshHome: profileHome, profile: 'research', patches: [patchPath], processCwd: manifest.workspace, env,
    initializeTimeoutMs: 30000, shutdownTimeoutMs: 5000});
  const notifications = [];
  let subscription, pump, failure = null, closeReceipt = null;
  const stopFile = join(output, 'cancel-request.json');
  const onSignal = () => { if (!existsSync(stopFile)) writeFileSync(stopFile, JSON.stringify({reason: 'user_cancelled'}), {flag: 'wx'}); };
  process.on('SIGTERM', onSignal);
  process.on('SIGINT', onSignal);
  try {
    client.start();
    subscription = client.subscribe();
    pump = (async () => { try { for await (const row of subscription) { notifications.push(row); } } catch {} })();
    await client.initialize({cwd: manifest.workspace, provider: manifest.provider, model: manifest.model, reasoningEffort: manifest.effort});
    if (!existsSync(join(output, 'runtime-audit.jsonl')) || !readFileSync(join(output, 'runtime-audit.jsonl'), 'utf8').includes('runtime.ready')) throw Error('Accounting plugin did not become ready; no prompt sent');
    writeFileSync(join(output, 'runner-status.json'), JSON.stringify({phase: 'research', run_id: manifest.run_id, pid: process.pid, started_at: new Date().toISOString()}));
    const prompt = manifest.materials.find(row => row.name === 'prompt.txt');
    if (!prompt) throw Error('Frozen prompt.txt material missing');
    await client.prompt(manifest.session_id, [{type: 'text', text: readFileSync(prompt.path, 'utf8')}]);
    await client.request('session/wait', {sessionId: manifest.session_id});
  } catch (error) {
    failure = String(error.message ?? error);
  } finally {
    let nativeConfirmed = false;
    try { await client.close(); nativeConfirmed = true; } catch (error) { failure ??= String(error.message ?? error); }
    subscription?.close();
    await pump;
    writeFileSync(join(output, 'sdk-notifications.json'), JSON.stringify(notifications));
    const stopped = existsSync(join(output, 'runtime-stop.json')) ? JSON.parse(readFileSync(join(output, 'runtime-stop.json'))) : null;
    const reason = stopped?.reason ?? (failure ? 'technical_failed' : 'model_final_idle');
    const toolsCleanup = existsSync(join(output, 'tools-cleanup.json')) ? JSON.parse(readFileSync(join(output, 'tools-cleanup.json'))) : null;
    if (existsSync(join(output, 'closeout.json'))) {
      closeReceipt = {ok: nativeConfirmed && toolsCleanup?.status === 'cleanup_confirmed', value: JSON.parse(readFileSync(join(output, 'closeout.json')))};
    } else {
      const closeout = spawnSync(python, ['-B', join(here, 'run_closeout.py'), join(output, 'data')], {
        input: JSON.stringify({manifest: manifestPath, reason, components: {native_runtime: {confirmed: nativeConfirmed}, model_tools: {confirmed: toolsCleanup?.status === 'cleanup_confirmed'}}, timed_out: false}), encoding: 'utf8', timeout: 120000,
      });
      try { closeReceipt = JSON.parse(closeout.stdout); } catch { closeReceipt = {ok: false, error: 'closeout_unavailable'}; }
    }
    writeFileSync(join(output, 'runner-status.json'), JSON.stringify({phase: 'stopped', run_id: manifest.run_id, reason, failure, closeout: closeReceipt, model: manifest.model, effort: manifest.effort}, null, 2));
    process.off('SIGTERM', onSignal); process.off('SIGINT', onSignal);
  }
  return {failure, closeout: closeReceipt};
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [manifest, sdkPath, codexPath, python] = process.argv.slice(2);
  run(resolve(manifest), {sdkPath: resolve(sdkPath), codexPath: resolve(codexPath), python}).then(value => {
    console.log(JSON.stringify(value)); if (value.failure || !value.closeout?.ok) process.exitCode = 1;
  }).catch(error => { console.error(error.message); process.exitCode = 1; });
}

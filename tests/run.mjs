import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const explicitPython = process.env.DSH_MATH_PYTHON;
const candidates = explicitPython
  ? [explicitPython]
  : ['python3.14', 'python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3'];
const probe = 'import json,sys; print(json.dumps({"executable":sys.executable,"version":sys.version.split()[0]})); raise SystemExit(0 if sys.version_info >= (3,10) else 1)';
let python;

for (const candidate of candidates) {
  const result = spawnSync(candidate, ['-B', '-c', probe], { encoding: 'utf8' });
  if (result.status === 0) {
    python = JSON.parse(result.stdout);
    break;
  }
}

if (!python) {
  console.error(explicitPython
    ? `DSH_MATH_PYTHON must point to Python 3.10+: ${explicitPython}`
    : 'Python 3.10+ not found; set DSH_MATH_PYTHON to its absolute path.');
  process.exit(1);
}

console.log(`Python: ${python.executable} (${python.version})`);
const tests = spawnSync(python.executable, ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], {
  cwd: root,
  stdio: 'inherit',
});
if (tests.error) throw tests.error;
if (tests.status !== 0) process.exit(tests.status ?? 1);

const nodeTests = readdirSync(join(root, 'tests'))
  .filter((name) => name.endsWith('.test.mjs'))
  .sort()
  .map((name) => join(root, 'tests', name));
const nodeResult = spawnSync(process.execPath, ['--test', ...nodeTests], {
  cwd: root,
  env: { ...process.env, DSH_MATH_PYTHON: python.executable },
  stdio: 'inherit',
});
if (nodeResult.error) throw nodeResult.error;
process.exit(nodeResult.status ?? 1);

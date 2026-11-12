"""Build immutable offline qualification pins; never starts a solver."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
DSH = Path('/Users/zcy/.dsh/cli/lib/node_modules/@deepseek-ai/dsh')
NODE = Path('/Users/zcy/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node').resolve()


def rows(paths):
    return [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted({path.resolve() for path in paths})]


def native_fingerprint():
    # Freeze all executable JS/JSON/native-module resources in the installed
    # dependency tree, covering transitive imports rather than entry points only.
    paths = [path for path in DSH.rglob('*') if path.is_file() and
             path.suffix in {'.js', '.mjs', '.cjs', '.json', '.wasm', '.node'}]
    paths.append(NODE)
    return {'native_version': json.loads((DSH / 'package.json').read_text())['version'],
            'native_files': rows(paths)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    evidence = Path(args.evidence).resolve()
    output = Path(args.output).resolve()
    if not output.is_relative_to(HERE) or output.exists():
        parser.error('Output must be new inside plain/')
    tests = json.loads((evidence / 'tests.json').read_text())
    if tests.get('passed') is not True or tests.get('upstream_requests') != 0:
        parser.error('Offline native suite did not pass')
    fingerprint = native_fingerprint()
    evidence_paths = [path for path in evidence.rglob('*') if path.is_file() and
                     path.suffix in {'.json', '.jsonl', '.log', '.sb'}]
    value = {'format': 'dsh-math-session2-plain-qualification-v1', 'qualified': True,
             'fixture_only': True, **fingerprint, 'evidence_files': rows(evidence_paths),
             'engine': 'installed rc.5 ordinary ReactLoopAgent service entry; not headless CLI invocation',
             'math_plugin': False, 'research_scheduler': False, 'upstream_requests': 0,
             'compaction': 'native BasicCompactionEngine auto retained; actual manual native compaction fixture passed',
             'model_policy': {'provider': 'openai-codex', 'model': 'gpt-6-astra', 'effort': 'max',
                              'physical_requests': 120, 'max_output_tokens': 128000},
             'passed_assertions': tests['assertions'],
             'limitations': ['Live upstream compatibility remains untested by authorization.',
                            'Fixture success is not a formal research-task score.',
                            'Process observations and artifact watchdog have documented sampling gaps.']}
    with output.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        stream.write('\n')
    print(json.dumps({'path': str(output), 'native_files': len(value['native_files']),
                      'evidence_files': len(value['evidence_files']), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()

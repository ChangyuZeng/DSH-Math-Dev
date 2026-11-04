"""Minimal stdio MCP adapter to the host common-tool gateway; no file access.

This file deliberately uses only stdlib so isolated native CLI processes do not
load the daily Python environment. No model request endpoint is implemented.
"""
import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener


def serve(url, token, input_stream=sys.stdin, output_stream=sys.stdout):
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or not parsed.path.endswith('/tools'):
        raise ValueError('MCP accepts only a numeric loopback common-tool endpoint')
    opener = build_opener(ProxyHandler({}))
    for line in input_stream:
        request = None
        try:
            if len(line) > 2 * 1024 * 1024:
                raise ValueError('MCP request exceeds limit')
            request = json.loads(line)
            if 'id' not in request:
                continue
            method, params = request.get('method'), request.get('params', {})
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'four-harness-common-tools', 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                req = Request(url.removesuffix('/tools') + '/manifest', b'{}',
                              {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                with opener.open(req, timeout=15) as response:
                    manifest = json.load(response)
                result = {'tools': [{'name': tool['name'], 'description': tool['description'], 'inputSchema': tool['parameters']}
                                    for tool in manifest['tools']]}
            elif method == 'tools/call':
                data = json.dumps({'name': params['name'], 'arguments': params.get('arguments', {})}).encode()
                headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
                meta = params.get('_meta', {})
                if not isinstance(meta, dict):
                    raise ValueError('MCP metadata must be an object')
                if 'x-codex-turn-metadata' in meta:
                    if not isinstance(meta['x-codex-turn-metadata'], dict):
                        raise ValueError('Native MCP turn metadata must be an object')
                    headers['X-Codex-Turn-Metadata'] = json.dumps(meta['x-codex-turn-metadata'], allow_nan=False)
                if 'callId' in meta:
                    call_id=meta['callId']
                    if not isinstance(call_id,str) or not 0<len(call_id)<=200 or not all(32<ord(c)<127 for c in call_id):
                        raise ValueError('Native MCP call identity is invalid')
                    headers['X-Four-Native-Call-Id']=call_id
                req = Request(url, data, headers)
                try:
                    with opener.open(req, timeout=75) as response:
                        payload = json.load(response)
                except HTTPError as error:
                    payload = json.load(error)
                result = {'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}],
                          'isError': not payload.get('ok', False)}
            else:
                raise ValueError('Unsupported MCP method')
            answer = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as error:
            answer = {'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None,
                      'error': {'code': -32602, 'message': str(error)}}
        output_stream.write(json.dumps(answer, ensure_ascii=False, allow_nan=False) + '\n')
        output_stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--token-env', default='FOUR_HARNESS_TOKEN')
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        parser.error('Run capability environment variable is missing')
    serve(args.url, token)


if __name__ == '__main__':
    main()

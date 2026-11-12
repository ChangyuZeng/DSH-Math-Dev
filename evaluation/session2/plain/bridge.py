"""Frozen direct common-tool namespace validator for installed ordinary DSH."""
from ledger import require


class DirectCommonTools:
    def __init__(self, catalog):
        self.catalog = {item['name']: {key: value for key, value in item.items() if key != 'strict'}
                        for item in catalog}

    def validate_request(self, payload, *, allowed_names, scope):
        declared = list(payload.get('tools', []))
        for item in payload.get('input', []):
            if item.get('type') == 'additional_tools':
                require(set(item) <= {'type', 'role', 'tools'} and item.get('role') == 'developer',
                        'PLAIN_NAMESPACE_DRIFT', 'Only ordinary DSH direct tool namespaces are qualified')
                declared.extend(item.get('tools', []))
        names = []
        for tool in declared:
            name = tool.get('name')
            require(tool.get('type') == 'function' and name in allowed_names and name in self.catalog,
                    'TOOL_NOT_ALLOWED', 'Only the seven common direct tools are qualified')
            expected = self.catalog[name]
            require(tool.get('parameters') == expected['parameters'] and
                    tool.get('description') == expected['description'] and
                    set(tool) <= {'type', 'name', 'description', 'parameters', 'strict'},
                    'PLAIN_TOOL_SCHEMA_DRIFT', 'Native tool definition differs from frozen shared tool')
            names.append(name)
        require(len(names) == len(set(names)), 'PLAIN_DUPLICATE_TOOL', 'Duplicate native tool declaration')

    def observe_inputs(self, payload, *, ledger, run_id):
        ledger.record(run_id, 'plain_native_direct_namespace',
                      policy='seven_common_tools_only', context_preserved=True)

    def validate_response(self, response, *, request_id, ledger, run_id):
        for item in response.get('output', []):
            kind = item.get('type')
            require(kind in {'message', 'reasoning', 'function_call'}, 'PLAIN_OUTPUT_TOOL_DRIFT',
                    'Unqualified native output or tool type')
            if kind == 'function_call':
                require(item.get('name') in self.catalog, 'TOOL_NOT_ALLOWED', 'Unqualified model tool call')

"""Build an offline Youmi runtime overlay from audited native source.

The output is a new build context. Running containers, configuration and databases
are never edited. Historical wire/storage keys remain compatible.
"""

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import tokenize


NATIVE_PREIMAGES = {
    'app.py': '4722505bfb4bd6516fd928157cc5ba0dfd735e58dd6de0934f53c8165e538cc4',
    'core/websocket_server.py': '7aa5273faca909ce8fe92669572850999d30560b03d7421a3f7935e7616c1b75',
    'core/connection.py': 'd9340d63ed57ac28e3bc39b2b599d8b7d0d353687cd05bbaed5152e08e3f33ca',
    'core/utils/dialogue.py': 'f292666870d05db3e183bd4c84c015384e35ebff40e84f0a78b9ccd6e044c18e',
    'core/providers/tools/unified_tool_manager.py': 'e4e525f5879e660a5f99042f60549aca92878455b808965ae3ff417cb7f65bf7',
    'core/handle/intentHandler.py': '0ec283e00c62d273178bb4c6c324ca56e3cb0ad0efbf9a7974d5b74c53d4bd59',
    'core/handle/reportHandle.py': '478ccf4b9de78553c8f0034fdb61f064114c805a94f08ad5dab936ae1b1265f4',
    'core/handle/sendAudioHandle.py': '61773d98dded88c4d69837949eadf41cc7dad5fbcfbe21464a40cf2d44180e29',
    'core/http_server.py': 'e3805233763aae09e25fbe651d19d7629460137ca148c64b0112436579313eb2',
}
MODULES = ('__init__', 'affect', 'astrbot_api', 'astrbot_identity', 'astrbot_media', 'astrbot_turns',
           'authorization', 'character', 'context', 'embedding', 'emotional_system', 'emotions',
           'events', 'extraction', 'features', 'jobs', 'management', 'memory', 'model_requests',
           'persona', 'proposal', 'reply', 'request_accounting', 'retrieval', 'schema', 'semantic', 'youmi_adapter')


def normalize_native(source):
    names = {'mia_companion': 'youmi_companion', 'xiaozhi_adapter': 'youmi_adapter',
             'mia': 'youmi', 'mia_auth': 'youmi_auth', 'MiaMemoryProvider': 'YoumiMemoryProvider'}
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        value = token.string
        if token.type == tokenize.NAME:
            value = names.get(value, value.replace('_mia_', '_youmi_'))
        elif token.type == tokenize.STRING:
            value = value.replace('_mia_', '_youmi_')
        tokens.append(token._replace(string=value))
    result = tokenize.untokenize(tokens)
    old_route = '                app.router.add_post("/mia/management", ManagementHandler(self.config).handle)\n'
    if old_route in result:
        result = result.replace(old_route, old_route +
            '                app.router.add_post("/youmi/management", ManagementHandler(self.config).handle)\n', 1)
    tree = ast.parse(result)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('mia_companion'):
            raise ValueError('Legacy runtime import remains')
    return result


def changes(root):
    result = {}
    for name, expected in NATIVE_PREIMAGES.items():
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Unreviewed native runtime: ' + name)
        result[name] = normalize_native(raw.decode())
    bundle = Path(__file__).parent
    for module in MODULES:
        result['youmi_companion/' + module + '.py'] = (bundle / (module + '.py')).read_text()
    for path in bundle.glob('schema*.sql'):
        result['youmi_companion/' + path.name] = path.read_text()
    for name, source in result.items():
        if name.endswith('.py'):
            ast.parse(source, filename=name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installed_source', type=Path, help='offline native source copy')
    parser.add_argument('output', type=Path, help='new overlay directory')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = changes(args.installed_source)
    if args.output.exists():
        raise ValueError('Output already exists; refusing to overwrite')
    manifest = {'preimages': NATIVE_PREIMAGES,
                'replacement': {name: hashlib.sha256(source.encode()).hexdigest() for name, source in result.items()}}
    if args.apply:
        args.output.mkdir(parents=True)
        for name, source in result.items():
            target = args.output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()

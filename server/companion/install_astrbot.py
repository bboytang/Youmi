"""Offline candidate-image updater for the audited installed V7 runtime (never a deploy)."""

import argparse
import ast
import hashlib
from pathlib import Path
import shutil


EXPECTED = {
    'core/http_server.py': '4328d0acde360c619f0f8bf91927923dc9e927806a43bebe4bc3ae6f3df74d24',
    'mia_companion/xiaozhi_adapter.py': '6325997fe98f992b64d657d5c3e6689168feb8d90fa36a30624f7605f6694385',
    'mia_companion/schema.py': '3e6ee813382608bfe611ceb4248151dfde006d5a25bf4ef780a40804dfc24a32',
}
PAYLOADS = ('astrbot_api.py', 'astrbot_identity.py', 'astrbot_turns.py', 'astrbot_media.py', 'schema-v8.sql',
            'schema-v9.sql', 'schema-v10.sql', 'schema-v11.sql', 'schema-v12.sql', 'schema-v13.sql', 'schema-v14.sql', 'schema-v15.sql', 'schema-v16.sql', 'schema-v17.sql', 'cognition.py',
            'emotional_system.py', 'memory_state.py', 'memory_emotion.py')


def changes(root):
    originals = {}
    for name, expected in EXPECTED.items():
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Unreviewed installed source: ' + name)
        originals[name] = raw.decode('utf-8')
    http = originals['core/http_server.py']
    adapter = originals['mia_companion/xiaozhi_adapter.py']
    if http.count('                app = web.Application()\n') != 1 or adapter.count('    await worker.start()\n') != 1:
        raise ValueError('Runtime anchors differ')
    result = {'core/http_server.py': http.replace('from aiohttp import web\n',
        'from aiohttp import web\nfrom mia_companion.astrbot_api import register_routes as register_astrbot_routes\n', 1)
        .replace('                app = web.Application()\n',
                 '                app = web.Application()\n                register_astrbot_routes(app, self.config)\n', 1),
        'mia_companion/xiaozhi_adapter.py': adapter.replace('    await worker.start()\n',
            '    await worker.start()\n    from .astrbot_api import attach_runtime\n    attach_runtime(store, worker)\n', 1),
        'mia_companion/schema.py': Path(__file__).with_name('schema.py').read_text()}
    for name in PAYLOADS:
        if (root / 'mia_companion' / name).exists():
            raise ValueError('Existing bridge file; refusing to overwrite: ' + name)
        result['mia_companion/' + name] = Path(__file__).with_name(name).read_text()
    for name, text in result.items():
        if name.endswith('.py'):
            ast.parse(text, filename=name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('--apply', action='store_true', help='Apply to an offline candidate directory only')
    args = parser.parse_args()
    result = changes(args.runtime)
    if args.apply:
        for name in EXPECTED:
            if (args.runtime / (name + '.astrbot-original')).exists():
                raise ValueError('Backup already exists; refusing to overwrite')
        for name in EXPECTED:
            shutil.copy2(args.runtime / name, args.runtime / (name + '.astrbot-original'))
        try:
            for name, text in result.items():
                (args.runtime / name).write_text(text)
        except BaseException:
            for name in EXPECTED:
                shutil.copy2(args.runtime / (name + '.astrbot-original'), args.runtime / name)
            for name in PAYLOADS:
                (args.runtime / 'mia_companion' / name).unlink(missing_ok=True)
            raise
    for name, text in result.items():
        print(hashlib.sha256(text.encode()).hexdigest(), name)
    print('Offline source only. No SQLite opened, no container started, no manager changed.')


if __name__ == '__main__':
    main()

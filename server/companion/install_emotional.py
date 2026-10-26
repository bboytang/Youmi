"""Prepare an offline emotional-system update after exact source preimage checks.

Never run against a live mounted source tree. No database or service is opened.
"""

import argparse
import ast
import hashlib
from pathlib import Path
import subprocess


BASE = 'cb6c1388c128f1476a952fcf451bfc1a1ff208dc'
UPDATED = ('affect.py', 'character.py', 'context.py', 'reply.py', 'features.py',
           'memory.py', 'management.py', 'schema.py', 'astrbot_api.py')
ADDED = ('emotional_system.py', 'schema-v9.sql', 'schema-v10.sql', 'memory_runtime.py',
         'memory_repository.py', 'memory_shadow.py', 'memory_search.py', 'memory_state.py', 'schema-v11.sql',
         'requirements-memory-shadow.txt', 'YOUMI_MEMORY_NOTICE.md', 'YOUMI_MEMORY_LICENSE')


def runtime_text(name, source, legacy):
    if legacy and name not in {'schema.py', 'astrbot_api.py', *ADDED}:
        return source.replace('youmi_adapter', 'xiaozhi_adapter').replace('Youmi', 'Mia').replace('youmi', 'mia')
    return source


def changes(root, legacy=False):
    bundle = Path(__file__).parent
    result = {}
    for name in UPDATED:
        baseline = subprocess.check_output(['git', 'show', BASE + ':server/companion/' + name],
                                            cwd=bundle, text=True)
        if (root / name).read_text() != runtime_text(name, baseline, legacy):
            raise ValueError('Unreviewed emotional system source: ' + name)
        result[name] = (bundle / name).read_text()
    for name in ADDED:
        if (root / name).exists():
            raise ValueError('Emotional system already present: ' + name)
        result[name] = (bundle / name).read_text()
    for name, source in result.items():
        if name.endswith('.py'):
            ast.parse(source, filename=name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='offline runtime source copy; deploy the result as youmi_companion')
    parser.add_argument('--legacy', action='store_true', help='accept audited historical input; output always uses Youmi naming')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = changes(args.source, args.legacy)
    if args.apply:
        for name, source in result.items():
            (args.source / name).write_text(source)
    for name, source in result.items():
        print(hashlib.sha256(source.encode()).hexdigest(), name)
    print('Offline source only; SQLite, running services and credentials untouched.')


if __name__ == '__main__':
    main()

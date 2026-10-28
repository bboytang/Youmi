"""Exercise real Docker namespace recovery in a disposable Compose project."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile import Reconciler, load_config  # noqa: E402


def run(command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def inode(name):
    output = subprocess.check_output(
        ['docker', 'exec', name, 'python3', '-c', 'import os;print(os.stat("/proc/1/ns/net").st_ino)'],
        text=True, stderr=subprocess.DEVNULL)
    return int(output.strip())


def main():
    project = 'youmi-ci-' + uuid.uuid4().hex[:10]
    dep = {'condition': 'service_healthy', 'restart': True}
    image = 'youmi-network-ci'
    config = {'services': {
        'youmi-netkeeper': {'image': image, 'cap_add': ['NET_ADMIN'], 'networks': ['lab']},
        'youmi-local-proxy': {
            'image': image, 'entrypoint': ['python3', '-m', 'http.server', '31987', '--bind', '127.0.0.1'],
            'network_mode': 'service:youmi-netkeeper', 'depends_on': {'youmi-netkeeper': dep},
            'healthcheck': {'test': ['CMD', 'python3', '-c',
                'import socket;socket.create_connection(("127.0.0.1",31987),timeout=2).close()'],
                'interval': '2s', 'timeout': '2s', 'retries': 15},
        },
        'youmi-browserless': {'image': image, 'entrypoint': ['sleep', '180'],
                              'network_mode': 'service:youmi-netkeeper',
                              'depends_on': {'youmi-netkeeper': dep, 'youmi-local-proxy': dep}},
        'astrbot': {'image': image, 'entrypoint': ['sleep', '180'],
                    'network_mode': 'service:youmi-netkeeper',
                    'depends_on': {'youmi-netkeeper': dep, 'youmi-local-proxy': dep}},
    }, 'networks': {'lab': {'internal': True}}}
    with tempfile.TemporaryDirectory(prefix='youmi-network-ci-') as tmp:
        compose_file = Path(tmp) / 'compose.json'
        compose_file.write_text(json.dumps(config), encoding='utf-8')
        settings = {'project': project, 'project_directory': tmp,
                    'files': [{'path': str(compose_file),
                               'sha256': hashlib.sha256(compose_file.read_bytes()).hexdigest()}],
                    'environment': {key: 'fixture' for key in (
                        'YOUMI_NET_KEEPER_IMAGE', 'YOUMI_NET_PROXY_CONFIG', 'YOUMI_NET_WAIT_SCRIPT',
                        'YOUMI_NET_BROWSER_WAIT_SCRIPT', 'YOUMI_NET_RESOLV_CONF')}}
        settings_file = Path(tmp) / 'settings.json'
        settings_file.write_text(json.dumps(settings), encoding='utf-8')
        compose = ['docker', 'compose', '-p', project, '-f', str(compose_file)]
        names = [project + '-' + name + '-1' for name in
                 ('youmi-netkeeper', 'youmi-local-proxy', 'youmi-browserless', 'astrbot')]
        try:
            run(compose + ['up', '-d'])
            assert len({inode(name) for name in names}) == 1
            run(['docker', 'restart', names[0]])
            assert len({inode(name) for name in names}) > 1
            Reconciler(load_config(settings_file)).run()
            assert len({inode(name) for name in names}) == 1
            compose_file.write_text(compose_file.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            try:
                load_config(settings_file)
            except ValueError as error:
                assert str(error) == 'compose_file_changed'
            else:
                raise AssertionError('modified compose file was accepted')
            print('isolated namespace recovery and change protection passed')
        finally:
            subprocess.run(compose + ['down', '--remove-orphans', '--timeout', '1'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()

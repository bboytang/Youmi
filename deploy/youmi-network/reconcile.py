"""Reattach approved Compose services if the keeper network namespace changes."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


ORDER = ('youmi-netkeeper', 'youmi-local-proxy', 'youmi-browserless', 'astrbot')
ENV_KEYS = {'YOUMI_NET_KEEPER_IMAGE', 'YOUMI_NET_PROXY_CONFIG', 'YOUMI_NET_WAIT_SCRIPT',
            'YOUMI_NET_BROWSER_WAIT_SCRIPT', 'YOUMI_NET_RESOLV_CONF'}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if set(config) != {'project', 'project_directory', 'files', 'environment'}:
        raise ValueError('invalid_reconcile_config')
    if (not config['project'] or not Path(config['project_directory']).is_absolute()
            or set(config['environment']) != ENV_KEYS or not config['files']):
        raise ValueError('invalid_reconcile_config')
    for item in config['files']:
        if set(item) != {'path', 'sha256'} or not Path(item['path']).is_absolute():
            raise ValueError('invalid_compose_file')
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('compose_file_changed')
    return config


class Reconciler:
    def __init__(self, config):
        self.config = config
        self.env = dict(os.environ, **config['environment'])
        self.compose = ['docker', 'compose', '--project-directory', config['project_directory'],
                        '-p', config['project']]
        for item in config['files']:
            self.compose.extend(['-f', item['path']])

    def inspect(self, service):
        name = self.config['project'] + '-' + service + '-1'
        result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
        if result.returncode:
            return None
        item = json.loads(result.stdout)[0]
        labels = item['Config'].get('Labels') or {}
        if (labels.get('com.docker.compose.project') != self.config['project']
                or labels.get('com.docker.compose.service') != service):
            raise RuntimeError('unexpected_container_identity')
        return item

    def up(self, service, *, recreate):
        args = self.compose + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never']
        if recreate:
            args.append('--force-recreate')
        subprocess.run(args + [service], env=self.env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait(self, service, healthy=False):
        for _ in range(60):
            item = self.inspect(service)
            if item and item['State']['Running']:
                if not healthy or item['State'].get('Health', {}).get('Status') == 'healthy':
                    return item
            time.sleep(1)
        raise RuntimeError('service_not_ready:' + service)

    @staticmethod
    def namespace(item):
        return os.stat('/proc/%d/ns/net' % item['State']['Pid']).st_ino

    def run(self):
        keeper = self.inspect(ORDER[0])
        if not keeper or not keeper['State']['Running']:
            self.up(ORDER[0], recreate=False)
        keeper = self.wait(ORDER[0], healthy=True)
        keeper_namespace = self.namespace(keeper)
        for service in ORDER[1:]:
            item = self.inspect(service)
            if not item or not item['State']['Running'] or self.namespace(item) != keeper_namespace:
                self.up(service, recreate=True)
            item = self.wait(service, healthy=service == 'youmi-local-proxy')
            if self.namespace(item) != keeper_namespace:
                raise RuntimeError('namespace_mismatch:' + service)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--lock', default='/run/lock/youmi-network-reconcile.lock')
    args = parser.parse_args()
    with open(args.lock, 'a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Reconciler(load_config(args.config)).run()


if __name__ == '__main__':
    main()

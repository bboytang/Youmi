"""Install the reviewed firewall plan inside a dedicated network namespace."""

import argparse
import subprocess

from firewall_plan import ROUTE_MARK, ROUTE_TABLE, TCP_CHAIN, UDP_CHAIN, commands


def _run(command, *, check=True):
    return subprocess.run(command, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _undo(command):
    result = list(command)
    if result[0] == 'iptables':
        if '-A' in result:
            result[result.index('-A')] = '-D'
        elif '-N' in result:
            result[result.index('-N')] = '-X'
        else:
            raise ValueError('unsupported_rule')
    elif result[0] == 'ip' and 'add' in result:
        result[result.index('add')] = 'del'
    else:
        raise ValueError('unsupported_rule')
    return result


def preflight():
    ipv6 = _run(['ip', '-6', 'route', 'show', 'table', 'all'])
    for line in ipv6.stdout.splitlines():
        if not line.strip():
            continue
        first = line.split()[0]
        # Local, rejected, multicast and link-local routes cannot provide a
        # public IPv6 exit. Any other route needs IPv6 interception first.
        if first not in {'local', 'unreachable', 'blackhole', 'prohibit', 'throw'} and not first.startswith(('fe80:', 'ff')):
            raise RuntimeError('ipv6_public_route_not_supported')
    for table, chain in (('nat', TCP_CHAIN), ('mangle', UDP_CHAIN)):
        if _run(['iptables', '-t', table, '-S', chain], check=False).returncode == 0:
            raise RuntimeError('network_chain_already_exists')
    rules = _run(['ip', '-4', 'rule', 'show']).stdout
    if any(('lookup ' + str(ROUTE_TABLE)) in line or ('fwmark ' + hex(ROUTE_MARK)) in line
           for line in rules.splitlines()):
        raise RuntimeError('network_policy_already_exists')
    routes = _run(['ip', '-4', 'route', 'show', 'table', 'all']).stdout
    if any(('table ' + str(ROUTE_TABLE)) in line for line in routes.splitlines()):
        raise RuntimeError('network_route_table_in_use')


def install(expected_namespace):
    # A caller must independently resolve the keeper container's namespace.
    import os
    if os.stat('/proc/self/ns/net').st_ino != expected_namespace:
        raise RuntimeError('wrong_network_namespace')
    preflight()
    applied = []
    try:
        for command in commands():
            _run(command)
            applied.append(command)
    except Exception:
        for command in reversed(applied):
            _run(_undo(command), check=False)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-namespace', type=int, required=True)
    options = parser.parse_args()
    install(options.expected_namespace)

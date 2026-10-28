"""Generate isolated-namespace IPv4 routing rules for Youmi's egress proxy.

This module produces a plan only. It does not install rules on a host or container.
"""

from ipaddress import ip_network

from xray_config import OUTBOUND_MARK, TCP_PORT, UDP_PORT


ROUTE_MARK = 0x594D
ROUTE_TABLE = 219
TCP_CHAIN = 'YOUMI_NET_TCP'
UDP_CHAIN = 'YOUMI_NET_UDP'

# Local service, container, and link-local addresses must remain reachable.
DEFAULT_INTERNAL = (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
    '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/4',
    '240.0.0.0/4',
)


def commands(internal=DEFAULT_INTERNAL):
    networks = []
    for value in internal:
        network = ip_network(value, strict=True)
        if network.version != 4:
            raise ValueError('ipv4_network_required')
        networks.append(str(network))
    if not networks:
        raise ValueError('internal_exclusions_required')

    result = [
        ['iptables', '-t', 'nat', '-N', TCP_CHAIN],
        ['iptables', '-t', 'mangle', '-N', UDP_CHAIN],
    ]
    for table, chain in (('nat', TCP_CHAIN), ('mangle', UDP_CHAIN)):
        result.append(['iptables', '-t', table, '-A', chain, '-m', 'mark',
                       '--mark', str(OUTBOUND_MARK), '-j', 'RETURN'])
        for network in networks:
            result.append(['iptables', '-t', table, '-A', chain, '-d', network, '-j', 'RETURN'])
    result.extend([
        ['iptables', '-t', 'nat', '-A', TCP_CHAIN, '-p', 'tcp', '-j', 'REDIRECT',
         '--to-ports', str(TCP_PORT)],
        ['iptables', '-t', 'mangle', '-A', UDP_CHAIN, '-p', 'udp', '-j', 'MARK',
         '--set-mark', str(ROUTE_MARK)],
        ['ip', '-4', 'rule', 'add', 'fwmark', str(ROUTE_MARK), 'lookup', str(ROUTE_TABLE)],
        ['ip', '-4', 'route', 'add', 'local', '0.0.0.0/0', 'dev', 'lo', 'table', str(ROUTE_TABLE)],
        ['iptables', '-t', 'mangle', '-A', 'PREROUTING', '-i', 'lo', '-p', 'udp',
         '-m', 'mark', '--mark', str(ROUTE_MARK), '-j', 'TPROXY', '--on-ip', '127.0.0.1',
         '--on-port', str(UDP_PORT), '--tproxy-mark', str(ROUTE_MARK)],
        # Add hooks last, once both chains and policy routing are ready.
        ['iptables', '-t', 'nat', '-A', 'OUTPUT', '-p', 'tcp', '-j', TCP_CHAIN],
        ['iptables', '-t', 'mangle', '-A', 'OUTPUT', '-p', 'udp', '-j', UDP_CHAIN],
    ])
    return result

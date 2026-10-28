"""Build the transparent listener without changing an existing Xray route."""

import copy
import json
import os
from pathlib import Path
import tempfile


OUTBOUND_MARK = 255
TCP_PORT = 31990
UDP_PORT = 31991


def make_config(original):
    config = copy.deepcopy(original)
    inbounds = config.get('inbounds')
    outbounds = config.get('outbounds')
    if not isinstance(inbounds, list) or not isinstance(outbounds, list) or not outbounds:
        raise ValueError('invalid_xray_config')
    if any(item.get('port') in {TCP_PORT, UDP_PORT} for item in inbounds):
        raise ValueError('transparent_port_in_use')
    for outbound in outbounds:
        stream = outbound.setdefault('streamSettings', {})
        options = stream.setdefault('sockopt', {})
        if 'mark' in options and options['mark'] != OUTBOUND_MARK:
            raise ValueError('outbound_mark_conflict')
        options['mark'] = OUTBOUND_MARK
    for protocol, port, mode in (('tcp', TCP_PORT, 'redirect'), ('udp', UDP_PORT, 'tproxy')):
        inbounds.append({
            'tag': 'youmi-transparent-' + protocol,
            'listen': '127.0.0.1',
            'port': port,
            'protocol': 'dokodemo-door',
            'settings': {'network': protocol, 'followRedirect': True},
            'streamSettings': {'sockopt': {'tproxy': mode}},
            'sniffing': {'enabled': True, 'destOverride': ['http', 'tls', 'quic']},
        })
    return config


def write_config(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('source_equals_destination')
    config = make_config(json.loads(source.read_text(encoding='utf-8')))
    fd, temporary = tempfile.mkstemp(prefix='.youmi-network-', dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    write_config(args.source, args.destination)

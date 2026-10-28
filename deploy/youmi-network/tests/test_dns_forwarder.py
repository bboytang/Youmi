import importlib.util
from pathlib import Path
import socketserver
import struct
import threading
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('youmi_network_dns',
    Path(__file__).resolve().parents[1] / 'dns_forwarder.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def query(name):
    labels = b''.join(bytes([len(x)]) + x.encode('ascii') for x in name.split('.'))
    return bytes.fromhex('123401000001000000000000') + labels + b'\0' + bytes.fromhex('00010001')


class DNSForwarderTests(unittest.TestCase):
    def test_internal_service_names_and_public_domains(self):
        self.assertTrue(module.is_internal(module.query_name(query('xiaozhi-esp32-server'))))
        self.assertTrue(module.is_internal(module.query_name(query('service.internal'))))
        self.assertFalse(module.is_internal(module.query_name(query('www.example.org'))))
        for value in [b'', query('example.org')[:-2], bytes.fromhex('123401000002000000000000')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.query_name(value)

    def test_failure_response_does_not_keep_unrelated_extra_data(self):
        source = query('example.org') + b'unrelated-extra-data'
        response = module.fail_response(source)
        self.assertEqual(response[:2], source[:2])
        self.assertEqual(struct.unpack('!H', response[2:4])[0] & 0x000f, 2)
        self.assertEqual(response[12:], query('example.org')[12:])

    def test_routes_internal_and_public_queries_to_separate_upstreams(self):
        seen = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                request, sock = self.request
                seen.append(self.server.kind)
                response = request[:2] + bytes([request[2] | 0x80]) + request[3:]
                sock.sendto(response, self.client_address)

        local = socketserver.UDPServer(('127.0.0.1', 0), Handler)
        public = socketserver.UDPServer(('127.0.0.1', 0), Handler)
        local.kind, public.kind = 'local', 'public'
        threads = [threading.Thread(target=x.serve_forever, daemon=True) for x in (local, public)]
        for thread in threads:
            thread.start()
        try:
            with patch.object(module, 'LOCAL_DNS', local.server_address), \
                    patch.object(module, 'PUBLIC_DNS', public.server_address):
                self.assertTrue(module.forward(query('backend'))[2] & 0x80)
                self.assertTrue(module.forward(query('www.example.org'))[2] & 0x80)
            self.assertEqual(seen, ['local', 'public'])
        finally:
            for server in (local, public):
                server.shutdown()
                server.server_close()

    def test_tcp_dns_request_and_response_framing(self):
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                size = struct.unpack('!H', module.read_exact(self.request, 2))[0]
                request = module.read_exact(self.request, size)
                response = request[:2] + bytes([request[2] | 0x80]) + request[3:]
                self.request.sendall(struct.pack('!H', len(response)) + response)

        server = socketserver.TCPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.object(module, 'PUBLIC_DNS', server.server_address):
                self.assertTrue(module.forward(query('example.org'), tcp=True)[2] & 0x80)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()

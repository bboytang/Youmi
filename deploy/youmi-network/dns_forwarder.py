"""DNS relay: container-local service names stay local; public names use the proxy."""

import socket
import socketserver
import struct
import threading


LOCAL_DNS = ('127.0.0.11', 53)
PUBLIC_DNS = ('1.1.1.1', 53)
GEO_DNS = ('127.0.0.1', 31993)
MAX_QUERY = 4096


def parse_question(message):
    if len(message) < 17 or struct.unpack('!H', message[4:6])[0] != 1:
        raise ValueError('invalid_dns_query')
    labels, index = [], 12
    while True:
        if index >= len(message):
            raise ValueError('invalid_dns_query')
        length = message[index]
        index += 1
        if length == 0:
            break
        if length > 63 or index + length > len(message):
            raise ValueError('invalid_dns_query')
        labels.append(message[index:index + length].decode('ascii').lower())
        index += length
    if not labels or index + 4 > len(message):
        raise ValueError('invalid_dns_query')
    return '.'.join(labels), index + 4


def query_name(message):
    return parse_question(message)[0]


def is_internal(name):
    return '.' not in name or name.endswith(('.local', '.internal'))


def fail_response(query):
    try:
        _, end = parse_question(query)
    except (ValueError, UnicodeError, struct.error):
        return b''
    flags = 0x8002 | (struct.unpack('!H', query[2:4])[0] & 0x0100)
    return query[:2] + struct.pack('!HHHHH', flags, 1, 0, 0, 0) + query[12:end]


def read_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise OSError('dns_connection_closed')
        result.extend(chunk)
    return bytes(result)


def forward(query, tcp=False):
    name, end = parse_question(query)
    query_type = struct.unpack('!H', query[end - 4:end - 2])[0]
    destination = LOCAL_DNS if is_internal(name) else GEO_DNS if query_type in (1, 28) else PUBLIC_DNS
    kind = socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as upstream:
        upstream.settimeout(5)
        upstream.connect(destination)
        if tcp:
            upstream.sendall(struct.pack('!H', len(query)) + query)
            response = read_exact(upstream, struct.unpack('!H', read_exact(upstream, 2))[0])
        else:
            upstream.sendall(query)
            response = upstream.recv(65535)
    if len(response) < 12 or response[:2] != query[:2] or not response[2] & 0x80:
        raise ValueError('invalid_dns_response')
    return response


class BoundedThreads(socketserver.ThreadingMixIn):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(64)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # DNS names are user activity; do not include packet contents in logs.
        pass


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        query, downstream = self.request
        try:
            response = forward(query)
        except (OSError, ValueError, UnicodeError):
            response = fail_response(query)
        if response:
            downstream.sendto(response, self.client_address)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(6)
        try:
            size = struct.unpack('!H', read_exact(self.request, 2))[0]
            if size > MAX_QUERY:
                return
            query = read_exact(self.request, size)
            try:
                response = forward(query, tcp=True)
            except (OSError, ValueError, UnicodeError):
                response = fail_response(query)
            if response:
                self.request.sendall(struct.pack('!H', len(response)) + response)
        except (OSError, ValueError, struct.error):
            return


class UDPServer(BoundedThreads, socketserver.UDPServer):
    max_packet_size = MAX_QUERY


class TCPServer(BoundedThreads, socketserver.TCPServer):
    allow_reuse_address = True


def start():
    udp = UDPServer(('127.0.0.53', 53), UDPHandler)
    try:
        tcp = TCPServer(('127.0.0.53', 53), TCPHandler)
    except Exception:
        udp.server_close()
        raise
    for server in (udp, tcp):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return udp, tcp

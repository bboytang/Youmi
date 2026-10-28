"""Install network rules before announcing the keeper namespace is ready."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import os

from install_rules import install
from dns_forwarder import start as start_dns


class Readiness(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/ready':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'youmi-network-ready')

    def log_message(self, format, *args):
        pass


def main():
    install(os.stat('/proc/self/ns/net').st_ino)
    start_dns()
    HTTPServer(('127.0.0.1', 31992), Readiness).serve_forever()


if __name__ == '__main__':
    main()

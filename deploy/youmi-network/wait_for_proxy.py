"""Start a program only after the local firewall and proxy are ready."""

import os
import socket
import sys
import time
import urllib.error
import urllib.request


def ready():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:31992/ready', timeout=2) as response:
            if response.read(128) != b'youmi-network-ready':
                return False
        with socket.create_connection(('127.0.0.1', 31987), timeout=2):
            return True
    except (OSError, urllib.error.URLError):
        return False


def main(argv):
    if not argv:
        raise SystemExit('missing application command')
    while not ready():
        time.sleep(1)
    os.execvp(argv[0], argv)


if __name__ == '__main__':
    main(sys.argv[1:])

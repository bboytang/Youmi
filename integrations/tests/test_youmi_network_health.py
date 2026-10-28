import asyncio
import unittest

from integrations.astrbot_plugin_youmi_web.network_health import listening


class NetworkHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_port_reachability(self):
        async def close(reader, writer):
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(close, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertTrue(await listening(port))
        finally:
            server.close()
            await server.wait_closed()
        self.assertFalse(await listening(port))


if __name__ == '__main__':
    unittest.main()

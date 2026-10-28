"""Read-only checks for the local network backend."""

import asyncio


async def listening(port):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), timeout=2)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def status():
    proxy, browser, compatibility, keeper = await asyncio.gather(
        listening(31987), listening(31988), listening(31989), listening(31992))
    return {'proxy_port_reachable': proxy, 'browser_port_reachable': browser,
            'compatibility_proxy_port_reachable': compatibility, 'network_keeper_port_reachable': keeper}

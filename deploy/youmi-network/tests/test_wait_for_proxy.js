const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const {ready} = require('../wait_for_proxy');

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

test('browser waits for both network marker and proxy', async () => {
  const keeper = http.createServer((req, res) => res.end('youmi-network-ready'));
  const proxy = net.createServer((socket) => socket.end());
  const keeperPort = await listen(keeper);
  const proxyPort = await listen(proxy);
  try {
    assert.equal(await ready(keeperPort, proxyPort), true);
    await new Promise((resolve) => proxy.close(resolve));
    assert.equal(await ready(keeperPort, proxyPort), false);
  } finally {
    await new Promise((resolve) => keeper.close(resolve));
    if (proxy.listening) await new Promise((resolve) => proxy.close(resolve));
  }
});

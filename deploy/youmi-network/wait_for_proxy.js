// Browserless has no Python; wait for the same local network gate as AstrBot.
const http = require('http');
const net = require('net');
const path = require('path');

function marker(port) {
  return new Promise((resolve) => {
    const request = http.get({host: '127.0.0.1', port, path: '/ready', timeout: 2000}, (response) => {
      let body = '';
      response.on('data', (chunk) => { body += chunk.toString(); if (body.length > 128) response.destroy(); });
      response.on('end', () => resolve(response.statusCode === 200 && body === 'youmi-network-ready'));
      response.on('error', () => resolve(false));
      response.on('close', () => resolve(false));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

function portOpen(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({host: '127.0.0.1', port});
    socket.setTimeout(2000);
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('timeout', () => socket.destroy());
    socket.on('error', () => resolve(false));
    socket.on('close', () => resolve(false));
  });
}

async function ready(keeperPort = 31992, proxyPort = 31987) {
  return await marker(keeperPort) && await portOpen(proxyPort);
}

async function main(args) {
  if (!args.length || typeof process.execve !== 'function') throw new Error('missing_command_or_execve');
  while (!await ready()) await new Promise((resolve) => setTimeout(resolve, 1000));
  const executable = path.resolve(args[0]);
  process.execve(executable, [executable, ...args.slice(1)], process.env);
}

if (require.main === module) main(process.argv.slice(2)).catch(() => { process.exitCode = 1; });
module.exports = {ready};

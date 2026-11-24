import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';

// Intentionally serves only a bootstrap test fixture, not the LedgerFlow product.
const routes = new Map(await Promise.all([
  ['/', 'index.html', 'text/html; charset=utf-8'],
  ['/fixture.css', 'fixture.css', 'text/css; charset=utf-8'],
  ['/fixture.js', 'fixture.js', 'text/javascript; charset=utf-8'],
].map(async ([route, file, type]) => [route, {
  body: await readFile(new URL(`../tests/fixtures/${file}`, import.meta.url)), type,
}])));

const server = createServer((request, response) => {
  response.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Referrer-Policy', 'no-referrer');
  response.setHeader('Cache-Control', 'no-store');

  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405, { Allow: 'GET, HEAD', 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Method not allowed');
    return;
  }

  const pathname = (request.url ?? '/').split('?')[0];
  if (pathname === '/health') {
    response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    response.end(request.method === 'HEAD' ? undefined : JSON.stringify({ status: 'ok', service: 'ledgerflow-qa-fixture' }));
    return;
  }

  const file = routes.get(pathname);
  if (!file) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end(request.method === 'HEAD' ? undefined : 'Not found');
    return;
  }
  response.writeHead(200, { 'Content-Type': file.type });
  response.end(request.method === 'HEAD' ? undefined : file.body);
});

server.on('error', (error) => {
  console.error(`QA fixture server failed: ${error.code ?? error.message}`);
  process.exitCode = 1;
});
server.listen(4173, '127.0.0.1', () => {
  console.log('LedgerFlow QA fixture: http://127.0.0.1:4173 (product specification pending)');
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => { process.exitCode = 0; }));
}

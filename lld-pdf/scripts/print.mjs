// Print an HTML file to PDF through the Chrome DevTools Protocol, with a page-number footer.
// Usage: node print.mjs <input.html> <output.pdf> "<footer>"
import { spawn } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const [, , input, output, footerText = ''] = process.argv;
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const port = 9400 + Math.floor(process.pid % 500);
const profile = mkdtempSync(join(tmpdir(), 'lld-cdp-'));
const chrome = spawn('chromium', ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pageWs() {
  for (let i = 0; i < 100; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t) => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(150);
  }
  throw new Error('chromium did not start');
}

try {
  const ws = new WebSocket(await pageWs());
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const waiting = new Map(); let loaded = false;
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
    if (m.method === 'Page.loadEventFired') loaded = true;
  };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  await send('Page.enable');
  await send('Page.navigate', { url: 'file://' + resolve(input) });
  for (let i = 0; i < 200 && !loaded; i++) await sleep(100);
  await sleep(800);
  const footer = `<div style="font-family:'Noto Sans',Helvetica,Arial,sans-serif;font-size:7.5px;color:#6a737d;width:100%;padding:0 12mm;display:flex;justify-content:space-between;">
    <span>${esc(footerText)}</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`;
  const res = await send('Page.printToPDF', {
    paperWidth: 8.27, paperHeight: 11.69, marginTop: 0.5, marginBottom: 0.6, marginLeft: 0.55, marginRight: 0.55,
    printBackground: true, displayHeaderFooter: true, headerTemplate: '<div></div>', footerTemplate: footer,
    preferCSSPageSize: false,
  });
  if (!res.result) throw new Error(JSON.stringify(res.error));
  writeFileSync(output, Buffer.from(res.result.data, 'base64'));
  ws.close();
} finally {
  chrome.kill();
}

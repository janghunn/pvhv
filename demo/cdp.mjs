// Shared helpers of demo/check_demo.mjs and demo/crosscheck.mjs: a loopback static
// server for the repository root, headless Chrome over the DevTools protocol (global
// WebSocket of Node 22, no packages) and per-page error/request collection.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export const GL_FLAGS = {
  swiftshader: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  gpu: ['--use-gl=angle', '--use-angle=gl-egl'],
};

/** Run-wide abort controller with deadline helpers and signal handling. */
export class Run {
  constructor(totalMs, label = 'Whole run') {
    this.controller = new AbortController();
    this.cleanups = [];
    this.timer = setTimeout(() => this.abort(new Error(`${label} exceeded ${totalMs / 1000} s`)), totalMs);
    this.handlers = new Map();
    for (const signal of ['SIGINT', 'SIGTERM']) {
      const h = () => this.abort(new Error(`Received ${signal}`));
      this.handlers.set(signal, h);
      process.once(signal, h);
    }
  }

  get signal() { return this.controller.signal; }

  abort(error) {
    if (!this.signal.aborted) this.controller.abort(error);
  }

  within(promise, ms, label) {
    let timer;
    let onAbort;
    const limit = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error(`${label}: deadline exceeded (${ms} ms)`)), ms);
      onAbort = () => reject(this.signal.reason || new Error('Run interrupted'));
      this.signal.addEventListener('abort', onAbort, { once: true });
      if (this.signal.aborted) onAbort();
    });
    return Promise.race([promise, limit]).finally(() => {
      clearTimeout(timer);
      this.signal.removeEventListener('abort', onAbort);
    });
  }

  async waitFor(predicate, ms, label) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (this.signal.aborted) throw this.signal.reason;
      const value = await this.within(Promise.resolve().then(predicate), Math.max(1, deadline - Date.now()), label);
      if (value) return value;
      await this.within(new Promise(r => setTimeout(r, 50)), Math.max(1, deadline - Date.now()), label);
    }
    throw new Error(`${label}: deadline exceeded (${ms} ms)`);
  }

  onCleanup(fn) { this.cleanups.push(fn); }

  async finish() {
    clearTimeout(this.timer);
    const errors = [];
    for (const fn of this.cleanups.reverse()) {
      try { await fn(); } catch (error) { errors.push(error.message); }
    }
    this.cleanups = [];
    for (const [signal, h] of this.handlers) process.removeListener(signal, h);
    return errors;
  }
}

/** Fail if `dir` exists and is not empty (exit code 2 is left to the caller). */
export async function requireEmptyDir(dir) {
  try {
    const entries = await readdir(dir);
    return entries.length === 0;
  } catch (error) {
    if (error.code === 'ENOENT') return true;
    throw error;
  }
}

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.bin': 'application/octet-stream', '.jpg': 'image/jpeg', '.png': 'image/png', '.css': 'text/css; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
};

/** Serve the repository root on 127.0.0.1:<random>; returns the origin. */
export async function serveRepo(run) {
  const server = createServer(async (req, res) => {
    try {
      const pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
      const file = path.resolve(REPO, `.${pathname}`);
      if (!file.startsWith(REPO + path.sep)) {
        res.writeHead(403).end();
        return;
      }
      const body = await readFile(file);
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
        'Cache-Control': 'no-store' });
      res.end(body);
    } catch (error) {
      res.writeHead(error.code === 'ENOENT' || error.code === 'EISDIR' ? 404 : 500).end();
    }
  });
  await run.within(new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  }), 10000, 'Loopback server startup');
  run.onCleanup(async () => {
    if (!server.listening) return;
    server.closeAllConnections();
    await new Promise(r => server.close(r));
  });
  return `http://127.0.0.1:${server.address().port}`;
}

class CDP {
  constructor(socket, run) {
    this.socket = socket;
    this.run = run;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Set();
    socket.addEventListener('message', event => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (error) {
        run.abort(error);
        return;
      }
      if (msg.id) {
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
        else p.resolve(msg.result);
      } else {
        for (const l of this.listeners) {
          try { l(msg); } catch (error) { run.abort(error); }
        }
      }
    });
    socket.addEventListener('close', () => this.rejectPending('DevTools connection closed'));
  }

  rejectPending(message) {
    for (const p of this.pending.values()) p.reject(new Error(message));
    this.pending.clear();
  }

  send(method, params = {}, sessionId, ms = 15000) {
    const id = ++this.nextId;
    const response = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
    return this.run.within(response, ms, method).finally(() => this.pending.delete(id));
  }

  close() {
    this.rejectPending('DevTools client closing');
    try { this.socket.close(); } catch { /* already closed */ }
  }
}

/** Launch headless Chrome with the given GL backend and extra flags; returns {cdp, stderr()}. */
export async function launchChrome(run, { chromePath, gl, extraFlags = [] }) {
  const profile = await mkdtemp(path.join(tmpdir(), 'pvhv-demo-chrome-'));
  const flags = ['--headless=new', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, ...(gl ? GL_FLAGS[gl] : []), ...extraFlags];
  const chrome = spawn(chromePath, flags, { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  let spawnError;
  chrome.once('error', e => { spawnError = e; });
  chrome.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-12000); });
  let cdp;
  run.onCleanup(async () => {
    cdp?.close();
    if (chrome.exitCode === null && chrome.signalCode === null) {
      await new Promise(resolve => {
        const t = setTimeout(() => { chrome.kill('SIGKILL'); resolve(); }, 3000);
        chrome.once('exit', () => { clearTimeout(t); resolve(); });
        chrome.kill('SIGTERM');
      });
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  });
  const active = await run.waitFor(async () => {
    if (spawnError) throw spawnError;
    if (chrome.exitCode !== null || chrome.signalCode !== null) throw new Error(`Chrome exited during startup: ${stderr}`);
    try {
      const lines = (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).trim().split('\n');
      return lines.length >= 2 && lines;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      return false;
    }
  }, 20000, 'Chrome startup');
  const socket = new WebSocket(`ws://127.0.0.1:${active[0]}${active[1]}`);
  await run.within(new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', () => reject(new Error('Cannot connect to DevTools')), { once: true });
  }), 10000, 'DevTools WebSocket startup');
  cdp = new CDP(socket, run);
  return { cdp, stderr: () => stderr };
}

/** Open a page target, collect console errors/exceptions/log errors/external requests, navigate. */
export async function openPage(run, cdp, origin, url, { width = 1440, height = 900, dsf = 1, mobile = false,
  touch = false, loadMs = 60000 } = {}) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const page = {
    errors: [], warnings: [], external: [], loaded: false, targetId, sessionId,
    send: (method, params = {}, ms) => cdp.send(method, params, sessionId, ms),
  };
  page.evaluate = async (expression, ms = 15000) => {
    const v = await page.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, ms);
    if (v.exceptionDetails) {
      throw new Error(v.exceptionDetails.exception?.description || v.exceptionDetails.text);
    }
    return v.result.value;
  };
  const listener = msg => {
    if (msg.sessionId !== sessionId) return;
    const { method, params } = msg;
    if (method === 'Page.loadEventFired') page.loaded = true;
    if (method === 'Runtime.consoleAPICalled') {
      const text = params.args.map(a => a.value ?? a.description ?? a.type).join(' ');
      if (['error', 'assert'].includes(params.type)) page.errors.push(`console.${params.type}: ${text}`);
      else if (params.type === 'warning') page.warnings.push(text);
    }
    if (method === 'Runtime.exceptionThrown') {
      page.errors.push(`exception: ${params.exceptionDetails.exception?.description || params.exceptionDetails.text}`);
    }
    if (method === 'Log.entryAdded') {
      if (params.entry.level === 'error') page.errors.push(`log: ${params.entry.text} ${params.entry.url || ''}`.trim());
      else if (params.entry.level === 'warning') page.warnings.push(params.entry.text);
    }
    if (method === 'Network.requestWillBeSent') {
      const u = params.request.url;
      if (!u.startsWith('data:') && !u.startsWith('blob:') && new URL(u).origin !== origin) page.external.push(u);
    }
  };
  cdp.listeners.add(listener);
  page.close = async () => {
    cdp.listeners.delete(listener);
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  };
  for (const domain of ['Page', 'Runtime', 'Log', 'Network']) await page.send(`${domain}.enable`);
  await page.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: dsf, mobile });
  if (touch) await page.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
  const nav = await page.send('Page.navigate', { url: `${origin}${url}` }, loadMs);
  if (nav.errorText) throw new Error(`Navigation to ${url}: ${nav.errorText}`);
  await run.waitFor(() => page.loaded, loadMs, `Load of ${url}`);
  return page;
}

/** Screenshot of the bounding box of `selector` (CSS pixels) as a PNG buffer. */
export async function screenshotElement(page, selector) {
  const box = await page.evaluate(`(() => {
    const e = document.querySelector(${JSON.stringify(selector)});
    if (!e) return null;
    e.scrollIntoView({ block: 'start', inline: 'nearest' });
    const r = e.getBoundingClientRect();
    return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height };
  })()`);
  if (!box) throw new Error(`screenshot: ${selector} not found`);
  // the element is in the viewport after scrollIntoView; capturing beyond the viewport would
  // resize the page (dropping the scrollbar) and shift the layout under the clip
  const shot = await page.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false,
    clip: { ...box, scale: 1 } }, 60000);
  return Buffer.from(shot.data, 'base64');
}

/** Full-page screenshot as a PNG buffer. */
export async function screenshotPage(page) {
  await page.evaluate("window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); true");
  const metrics = await page.send('Page.getLayoutMetrics');
  const c = metrics.cssContentSize || metrics.contentSize;
  const shot = await page.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: c.width, height: c.height, scale: 1 } }, 60000);
  return Buffer.from(shot.data, 'base64');
}

/** The WebGL renderer string of a fresh WebGL2 context in the page (unmasked when available). */
export const RENDERER_EXPR = `(() => {
  const gl = document.createElement('canvas').getContext('webgl2');
  if (!gl) return null;
  const e = gl.getExtension('WEBGL_debug_renderer_info');
  const r = e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const lose = gl.getExtension('WEBGL_lose_context');
  if (lose) lose.loseContext();
  return r;
})()`;

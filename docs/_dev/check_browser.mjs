#!/usr/bin/env node
// Node 22+, Chrome and the Chrome DevTools Protocol; no package installation.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const docs = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
let output;
let chromePath = process.env.CHROME || 'google-chrome';
let gl = 'swiftshader';
let allowFallback = false;
const GL_FLAGS = {
  swiftshader: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  gpu: ['--use-gl=angle', '--use-angle=gl-egl'],
};
const TAB_BUDGET = 180;
for (let i = 0; i < args.length; i += 1) {
  if (args[i] === '--out' && args[i + 1]) output = path.resolve(args[++i]);
  else if (args[i] === '--chrome' && args[i + 1]) chromePath = args[++i];
  else if (args[i] === '--gl' && args[i + 1]) gl = args[++i];
  else if (args[i] === '--allow-fallback') allowFallback = true;
  else throw new Error(`Unknown or incomplete argument: ${args[i]}`);
}
if (!Object.hasOwn(GL_FLAGS, gl)) throw new Error('--gl must be swiftshader or gpu');
if (!output) throw new Error('Usage: node docs/_dev/check_browser.mjs --out <dir> [--chrome <path>] [--gl swiftshader|gpu] [--allow-fallback]');

const controller = new AbortController();
const summary = { gl, allowFallback, widths: [], failures: [] };
let chrome;
let profile;
let server;
let cdp;
let devtoolsSocket;
let stderr = '';
let cleanupPromise;

function within(promise, milliseconds, label) {
  let timer;
  let abort;
  const limit = new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(new Error(`${label}: deadline exceeded (${milliseconds} ms)`)), milliseconds);
    abort = () => reject(controller.signal.reason || new Error('Run interrupted'));
    controller.signal.addEventListener('abort', abort, { once: true });
    if (controller.signal.aborted) abort();
  });
  return Promise.race([promise, limit]).finally(() => {
    clearTimeout(timer);
    controller.signal.removeEventListener('abort', abort);
  });
}

async function waitFor(predicate, milliseconds, label) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) {
    if (controller.signal.aborted) throw controller.signal.reason;
    const value = await within(Promise.resolve().then(predicate), Math.max(1, deadline - Date.now()), label);
    if (value) return value;
    await within(new Promise(resolve => setTimeout(resolve, 50)), Math.max(1, deadline - Date.now()), label);
  }
  throw new Error(`${label}: deadline exceeded (${milliseconds} ms)`);
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Set();
    socket.addEventListener('message', event => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        controller.abort(error);
        return;
      }
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
        else pending.resolve(message.result);
      } else {
        for (const listener of this.listeners) {
          try { listener(message); }
          catch (error) { controller.abort(error); }
        }
      }
    });
    socket.addEventListener('close', () => this.rejectPending('DevTools connection closed'));
    socket.addEventListener('error', () => this.rejectPending('DevTools connection error'));
  }

  rejectPending(message) {
    for (const pending of this.pending.values()) pending.reject(new Error(message));
    this.pending.clear();
  }

  async send(method, params = {}, sessionId, milliseconds = 10000) {
    const id = ++this.nextId;
    const response = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
    return within(response, milliseconds, method).finally(() => this.pending.delete(id));
  }

  close() {
    this.rejectPending('DevTools client closing');
    this.socket.close();
  }
}

async function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    cdp?.close();
    if (!cdp && devtoolsSocket) devtoolsSocket.close();
    if (chrome && chrome.exitCode === null && chrome.signalCode === null) {
      await new Promise(resolve => {
        const timer = setTimeout(() => {
          chrome.kill('SIGKILL');
          resolve();
        }, 3000);
        chrome.once('exit', () => { clearTimeout(timer); resolve(); });
        chrome.kill('SIGTERM');
      });
    }
    if (server?.listening) {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
    if (profile) await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  })();
  return cleanupPromise;
}

const signals = new Map();
for (const signal of ['SIGINT', 'SIGTERM']) {
  const handler = () => controller.abort(new Error(`Received ${signal}`));
  signals.set(signal, handler);
  process.once(signal, handler);
}
const wholeRun = setTimeout(() => controller.abort(new Error('Whole run exceeded 400 s')), 400000);
const uncaught = error => controller.abort(error instanceof Error ? error : new Error(String(error)));
process.once('uncaughtException', uncaught);
process.once('unhandledRejection', uncaught);

async function serve() {
  const types = {
    '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.md': 'text/markdown; charset=utf-8',
    '.json': 'application/json; charset=utf-8', '.png': 'image/png', '.webp': 'image/webp',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.bin': 'application/octet-stream'
  };
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const filename = path.resolve(docs, `.${pathname === '/' ? '/index.html' : pathname}`);
      if (!filename.startsWith(docs + path.sep)) {
        response.writeHead(403).end();
        return;
      }
      const content = await readFile(filename);
      response.writeHead(200, { 'Content-Type': types[path.extname(filename)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      response.end(content);
    } catch (error) {
      response.writeHead(error.code === 'ENOENT' ? 404 : 500).end();
    }
  });
  await within(new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  }), 10000, 'Loopback server startup');
  return `http://127.0.0.1:${server.address().port}`;
}

async function connectChrome() {
  profile = await mkdtemp(path.join(tmpdir(), 'pvhv-browser-'));
  chrome = spawn(chromePath, [
    '--headless=new', '--no-first-run', '--no-default-browser-check', ...GL_FLAGS[gl],
    '--remote-debugging-port=0', `--user-data-dir=${profile}`
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  let spawnError;
  chrome.once('error', error => { spawnError = error; });
  chrome.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-12000); });
  const active = await waitFor(async () => {
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
  devtoolsSocket = socket;
  await within(new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', () => reject(new Error('Cannot connect to DevTools')), { once: true });
  }), 10000, 'DevTools WebSocket startup');
  cdp = new CDP(socket);
}

async function checkWidth(origin, width, height) {
  const result = { width, height, errors: [], externalRequests: [], failures: [] };
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params = {}, milliseconds) => cdp.send(method, params, sessionId, milliseconds);
  const evaluate = async (expression, milliseconds = 10000) => {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, milliseconds);
    if (value.exceptionDetails) throw new Error(value.exceptionDetails.exception?.description || value.exceptionDetails.text);
    return value.result.value;
  };
  const key = async (name, code) => {
    for (const type of ['keyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent', { type, key: name, code: name,
        windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
    }
  };
  const tabFromTop = async identity => {
    // Reset both DOM focus and Chrome's sequential-navigation starting point.
    await evaluate(`window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
      document.body.tabIndex = -1; document.body.focus(); document.body.removeAttribute('tabindex'); true`);
    for (let count = 0; count < TAB_BUDGET; count += 1) {
      await key('Tab', 9);
      if (await evaluate(identity)) return count + 1;
    }
    return null;
  };
  let loaded = false;
  let collecting = true;
  const collect = event => {
    if (event.sessionId !== sessionId) return;
    const { method, params } = event;
    if (method === 'Page.loadEventFired') loaded = true;
    if (!collecting) return;
    if (method === 'Runtime.consoleAPICalled' && ['error', 'assert'].includes(params.type)) {
      result.errors.push(params.args.map(arg => arg.value ?? arg.description ?? arg.type).join(' '));
    }
    if (method === 'Runtime.exceptionThrown') result.errors.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
    if (method === 'Log.entryAdded' && params.entry.level === 'error') result.errors.push(params.entry.text);
    if (method === 'Network.requestWillBeSent') {
      const url = params.request.url;
      if (!url.startsWith('data:') && !url.startsWith('blob:') && new URL(url).origin !== origin) result.externalRequests.push(url);
    }
  };
  cdp.listeners.add(collect);
  try {
    for (const domain of ['Page', 'Runtime', 'Log', 'Network']) await send(`${domain}.enable`);
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: width === 390 ? 2 : 1, mobile: width === 390 });
    try {
      await cdp.send('Browser.grantPermissions', { origin, permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] });
      result.clipboardPermission = 'granted';
    } catch (error) {
      result.clipboardPermission = error.message;
    }
    loaded = false;
    await within((async () => {
      const navigation = await send('Page.navigate', { url: `${origin}/index.html` }, 30000);
      if (navigation.errorText) throw new Error(navigation.errorText);
      await waitFor(() => loaded, 30000, `Load at ${width}px`);
    })(), 30000, `Navigation/load at ${width}px`);
    try {
      await evaluate('document.fonts.ready.then(() => true)', 10000);
    } catch (error) {
      result.failures.push(`Fonts: ${error.message}`);
    }
    try {
      result.demo = await waitFor(() => evaluate(`(() => {
        if (document.querySelector('.pvhv-demo-fallback')) return 'fallback';
        if (!window.PVHVDemo) return null;
        if (!window.__pvhvPageCheckReady) {
          window.__pvhvPageCheckReady = 'waiting';
          Promise.resolve(PVHVDemo.ready).then(() => { window.__pvhvPageCheckReady = 'ready'; },
            () => { window.__pvhvPageCheckReady = 'failed'; });
        }
        return window.__pvhvPageCheckReady === 'ready' ? 'ready' : null;
      })()`, 120000), 120000, `Demo ready or fallback at ${width}px`);
      if (result.demo === 'fallback' && !allowFallback) result.failures.push('Demo fallback requires --allow-fallback');
      result.renderer = await evaluate(`(() => {
        const context = document.createElement('canvas').getContext('webgl2');
        if (!context) return null;
        const info = context.getExtension('WEBGL_debug_renderer_info');
        const renderer = info ? context.getParameter(info.UNMASKED_RENDERER_WEBGL) : context.getParameter(context.RENDERER);
        context.getExtension('WEBGL_lose_context')?.loseContext();
        return renderer;
      })()`);
      if (gl === 'gpu' && /SwiftShader|llvmpipe/i.test(result.renderer || '')) {
        result.failures.push(`GPU backend is software: ${result.renderer}`);
      }
      if (result.demo === 'ready') {
        result.tabsToDemo = await tabFromTop("document.activeElement.matches('#demo-root canvas.pvhv-demo-view')");
        if (!result.tabsToDemo) result.failures.push(`Demo canvas unreachable within ${TAB_BUDGET} Tab presses`);
        else {
          const before = await evaluate('PVHVDemo.getState()');
          await key('ArrowRight', 39);
          await evaluate('PVHVDemo.whenIdle().then(() => true)', 30000);
          const after = await evaluate('PVHVDemo.getState()');
          result.demoKeyboard = { before: before.t, after: after.t, E: before.E };
          const tolerance = Math.max(1e-10, before.E * 1e-6);
          if (Math.abs(after.t[0] - before.t[0] - before.E / 20) > tolerance ||
              Math.abs(after.t[1] - before.t[1]) > tolerance) {
            result.failures.push('ArrowRight did not move the focused demo by +E/20 in x');
          }
        }
      }
    } catch (error) {
      result.failures.push(`Demo: ${error.message}`);
    }
    try {
      await waitFor(() => evaluate('[...document.images].every(image => image.complete)'), 10000, 'Image decoding');
    } catch (error) {
      result.failures.push(`Images: ${error.message}`);
    }
    try {
      Object.assign(result, await evaluate(`(() => {
      const scrollWidth = document.documentElement.scrollWidth;
      const viewportWidth = innerWidth;
      const offenders = [...document.querySelectorAll('body *')].filter(element => {
        const box = element.getBoundingClientRect();
        return box.right > viewportWidth + 1 || box.left < -1;
      }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
      const element = offenders[0];
      const selector = element ? element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') + [...element.classList].map(name => '.' + name).join('') : null;
      return { scrollWidth, innerWidth: viewportWidth, overflow: scrollWidth > viewportWidth,
        widestOffender: scrollWidth > viewportWidth ? selector : null,
        lato700: document.fonts.check('700 16px Lato'), slots: document.querySelectorAll('[data-slot]').length,
        unfilledSlots: [...document.querySelectorAll('.slot-box')].filter(e => !e.closest('#demo-root')).length,
        demoPlaceholders: document.querySelectorAll('#demo-root .slot-box').length,
        images: [...document.images].map(image => ({ src: image.getAttribute('src'), complete: image.complete,
          naturalWidth: image.naturalWidth })) };
      })()`));
      if (result.overflow) result.failures.push(`Horizontal overflow: ${result.scrollWidth} > ${result.innerWidth}; ${result.widestOffender}`);
      if (!result.lato700) result.failures.push('Lato 700 failed to load');
      if (result.slots !== 5) result.failures.push(`Expected 5 slots, found ${result.slots}`);
      if (result.unfilledSlots) result.failures.push(`${result.unfilledSlots} slot-box placeholders outside demo`);
      if (result.demoPlaceholders) result.failures.push('Demo placeholder was not replaced');
      for (const image of result.images) {
        if (!image.complete || image.naturalWidth <= 0) result.failures.push(`Image not decoded: ${image.src}`);
      }
    } catch (error) {
      result.failures.push(`Layout: ${error.message}`);
    }

    try {
      result.tabsToCopy = await tabFromTop("document.activeElement.id === 'copy'");
      result.copyReachable = Boolean(result.tabsToCopy);
      if (!result.copyReachable) result.failures.push(`Copy button unreachable within ${TAB_BUDGET} Tab presses`);
      else {
        await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: '\r', unmodifiedText: '\r' });
        await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
        await waitFor(() => evaluate("document.getElementById('copy-status').textContent.trim()"), 5000, 'Copy status');
        const copyResult = await evaluate(`(async () => {
          const expected = document.getElementById('citation').textContent;
          const status = document.getElementById('copy-status').textContent;
          try {
            const actual = await navigator.clipboard.readText();
            return { mode: 'clipboard', exact: actual === expected, status };
          } catch {
            return { mode: 'selection', exact: getSelection().toString() === expected && status.includes('selected'), status };
          }
        })()`);
        result.copy = copyResult;
        if (!copyResult.status || !copyResult.exact) result.failures.push('Copy did not preserve exact BibTeX content');
      }
    } catch (error) {
      result.failures.push(`Keyboard/copy: ${error.message}`);
    }

    try {
      // Capture evidence even when keyboard/copy or other page checks fail.
      await evaluate("document.activeElement.blur(); window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); true");
      const metrics = await send('Page.getLayoutMetrics');
      const content = metrics.cssContentSize || metrics.contentSize;
      const shot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 85, captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: content.width, height: content.height, scale: 1 } });
      result.screenshot = path.join(output, `shot-${width}.jpg`);
      await writeFile(result.screenshot, Buffer.from(shot.data, 'base64'));
    } catch (error) {
      result.failures.push(`Screenshot: ${error.message}`);
    }
    // Protocol round trip drains initial resource/error events before the video phase.
    try {
      await evaluate('document.readyState');
    } catch (error) {
      result.failures.push(`Resource/error collection: ${error.message}`);
    }
    collecting = false;
    if (result.errors.length) result.failures.push(`${result.errors.length} console/runtime/log errors`);
    if (result.externalRequests.length) result.failures.push(`${result.externalRequests.length} external requests before video activation`);

    if (width === 1440) {
      await send('Network.setBlockedURLs', { urls: ['*youtube*'] });
      result.videoActivated = await evaluate(`(() => {
        document.getElementById('video-load').click();
        const frame = document.querySelector('#video-facade iframe');
        return Boolean(frame && frame.src.startsWith('https://www.youtube-nocookie.com/embed/cnR8iujBu7A'));
      })()`);
      if (!result.videoActivated) result.failures.push('Video button failed to create the expected iframe');
    }
  } catch (error) {
    result.failures.push(error.message);
  } finally {
    collecting = false;
    cdp.listeners.delete(collect);
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
  return result;
}

try {
  await mkdir(output, { recursive: true });
  const origin = await serve();
  await connectChrome();
  for (const [width, height] of [[1440, 900], [390, 844]]) {
    const result = await checkWidth(origin, width, height);
    summary.widths.push(result);
    summary.failures.push(...result.failures.map(message => `${width}px: ${message}`));
  }
} catch (error) {
  summary.failures.push(error.message);
  if (stderr) summary.chromeStderr = stderr;
} finally {
  clearTimeout(wholeRun);
  try {
    await cleanup();
  } catch (error) {
    summary.failures.push(`Cleanup: ${error.message}`);
  }
  for (const [signal, handler] of signals) process.removeListener(signal, handler);
  process.removeListener('uncaughtException', uncaught);
  process.removeListener('unhandledRejection', uncaught);
}
summary.result = summary.failures.length ? 'FAIL' : 'OK';
console.log(JSON.stringify(summary, null, 2));
process.exitCode = summary.failures.length ? 1 : 0;

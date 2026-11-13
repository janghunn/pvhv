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
const MIN_LABEL_PX = 11;
const MIN_TEXT_CONTRAST = 4.5;
// Visible page strings per language (kit.js, main.js, demo.js and the data-ko attributes of index.html).
const TEXT = {
  en: { affiliation: 'Sungkyunkwan University, Suwon, South Korea',
    venue: 'ACM Transactions on Graphics 42(4), Article 86, August 2023 (SIGGRAPH 2023)', venuePrefix: 'Published in ',
    resources: ['Paper', 'PDF', 'Code', 'BibTeX'], firstLinks: ['Overview', 'Method'],
    toDark: 'Switch to dark theme', toLight: 'Switch to light theme', langButton: '한국어',
    zoom: 'Zoom to read labels', fit: 'Fit image to width', region: 'Enlarged image, scroll sideways',
    copied: 'Copied.', selected: 'Selected.',
    fallback: 'This interactive demo needs WebGL2 with float render targets (EXT_color_buffer_float), which this browser does not provide.',
    label: "Re-implementation (2026), not the code used for the paper's experiments",
    attribution: 'Scene: Chess Set by Riley Queen, Poly Haven, CC0 1.0' },
  ko: { affiliation: '성균관대학교, 대한민국 수원',
    venue: 'ACM Transactions on Graphics 42(4), Article 86, 2023년 8월 (SIGGRAPH 2023)', venuePrefix: '게재: ',
    resources: ['논문', 'PDF', '코드', 'BibTeX'], firstLinks: ['개요', '방법'],
    toDark: '다크 테마로 전환', toLight: '라이트 테마로 전환', langButton: 'English',
    zoom: '라벨을 읽도록 확대', fit: '이미지를 너비에 맞춤', region: '확대한 이미지, 옆으로 스크롤',
    copied: '복사했습니다.', selected: '선택했습니다.',
    fallback: '이 인터랙티브 데모에는 부동소수점 렌더 타깃(EXT_color_buffer_float)을 지원하는 WebGL2가 필요하지만, 이 브라우저는 지원하지 않습니다.',
    label: '재구현(2026)이며, 논문 실험에 쓰인 코드가 아닙니다',
    attribution: '장면: Chess Set by Riley Queen, Poly Haven, CC0 1.0',
    blocksAttribution: '장면: 절차적으로 생성한 블록 장면 (이 재구현의 일부)' }
};
// Visible Latin strings allowed without Hangul in Korean (names and code words that stay English).
const I18N_ALLOWED = ['PVHV', 'BibTeX', 'English'];
const HANGUL = /[\uac00-\ud7a3]/;
const NO_JS_NOTICE = 'The interactive demo needs JavaScript and WebGL2 with float render targets.';
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
const summary = { gl, allowFallback, runs: [], failures: [] };
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
const wholeRun = setTimeout(() => controller.abort(new Error('Whole run exceeded 1500 s')), 1500000);
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

// These functions are serialized into the page by the checker.
function pageOverflow() {
  const viewportWidth = innerWidth;
  const visible = element => {
    const box = element.getBoundingClientRect(), style = getComputedStyle(element);
    return element.getClientRects().length && box.width > 0 && box.height > 0 &&
      style.visibility !== 'hidden' && style.visibility !== 'collapse' && Number(style.opacity) > 0;
  };
  const offenders = [...document.querySelectorAll('body *')].filter(element => {
    if (!visible(element) || element.closest('[aria-hidden="true"]')) return false;
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (/(auto|scroll)/.test(getComputedStyle(parent).overflowX)) return false;
    }
    const box = element.getBoundingClientRect();
    return box.right > viewportWidth + 1 || box.left < -1;
  }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
  const describe = element => element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') +
    [...element.classList].map(name => '.' + name).join('');
  const scrollWidth = document.documentElement.scrollWidth;
  return { scrollWidth, innerWidth: viewportWidth, overflow: scrollWidth > viewportWidth || offenders.length > 0,
    widestOffender: offenders.length ? describe(offenders[0]) : null, overflowOffenders: offenders.map(describe) };
}

function renderedContrast(samples, options = {}) {
  const colour = value => {
    value = value.trim();
    if (value === 'transparent') return [0, 0, 0, 0];
    if (value.startsWith('#')) {
      let hex = value.slice(1);
      if (hex.length === 3) hex = [...hex].map(char => char + char).join('');
      return [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16)).concat(1);
    }
    const numbers = (value.match(/[+-]?(?:\d*\.\d+|\d+)(?:e[+-]?\d+)?/gi) || []).map(Number);
    if (value.startsWith('color(srgb ')) return numbers.slice(0, 3).map(channel => channel * 255).concat(numbers[3] ?? 1);
    if (value.startsWith('rgb')) return numbers.slice(0, 3).concat(numbers[3] ?? 1);
    throw new Error('Unsupported computed colour: ' + value);
  };
  const over = (front, back) => {
    const alpha = front[3] + back[3] * (1 - front[3]);
    return [0, 1, 2].map(index => alpha ?
      (front[index] * front[3] + back[index] * back[3] * (1 - front[3])) / alpha : 0).concat(alpha);
  };
  const background = (element, underlay) => {
    if (underlay) return over(colour(getComputedStyle(element).backgroundColor), underlay);
    let back = [0, 0, 0, 0];
    for (let node = element; node; node = node.parentElement) {
      back = over(back, colour(getComputedStyle(node).backgroundColor));
      if (back[3] >= 1) return back;
    }
    return over(back, [255, 255, 255, 1]);
  };
  const luminance = values => values.slice(0, 3).map(channel => channel / 255)
    .map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4)
    .reduce((sum, channel, index) => sum + channel * [.2126, .7152, .0722][index], 0);
  const ratio = (a, b) => {
    const [dark, light] = [luminance(a), luminance(b)].sort((x, y) => x - y);
    return (light + .05) / (dark + .05);
  };
  const busy = options.showBusy ? document.querySelector('.pvhv-demo-busy') : null;
  const wasHidden = busy?.hidden;
  if (busy) busy.hidden = false;
  try {
    return samples.map(({ name, selector, minimum = 4.5, token, backgroundToken }) => {
      const element = document.querySelector(selector);
      const style = element && getComputedStyle(element);
      const box = element?.getBoundingClientRect();
      if (!element || !element.getClientRects().length || !box.width || !box.height ||
          style.visibility === 'hidden' || Number(style.opacity) === 0) return { name, selector, missing: true, minimum };
      const underlay = name === 'busy' ? (options.theme === 'dark' ? [255, 255, 255, 1] : [0, 0, 0, 1]) : null;
      const back = backgroundToken ? colour(style.getPropertyValue(backgroundToken)) : background(element, underlay);
      const foreground = colour(token ? style.getPropertyValue(token) : style.color);
      const value = ratio(over(foreground, back), back);
      return { name, selector, color: token ? style.getPropertyValue(token) : style.color,
        background: back, ratio: value, minimum };
    });
  } finally {
    if (busy) busy.hidden = wasHidden;
  }
}

function targetSizes() {
  const candidates = new Set(document.querySelectorAll('a, button, select'));
  for (const input of document.querySelectorAll('input')) {
    for (const label of input.labels || []) candidates.add(label);
  }
  // The skip link is clipped to 1px until it has focus; the checker measures it focused (skipLinkState).
  return [...candidates].filter(element => !element.matches('.skip:not(:focus)')).flatMap(element => {
    const style = getComputedStyle(element), box = element.getBoundingClientRect();
    if (!element.getClientRects().length || !box.width || !box.height || style.visibility === 'hidden' ||
        Number(style.opacity) === 0 || (element.tagName === 'A' && style.display === 'inline')) return [];
    const iconOnly = element.tagName === 'BUTTON' && !element.textContent.trim();
    if (box.height >= 44 && (!iconOnly || box.width >= 44)) return [];
    return [{ tag: element.tagName, id: element.id, label: element.getAttribute('aria-label') || element.textContent.trim(),
      height: box.height, width: box.width, iconOnly }];
  });
}

function skipLinkState() {
  const skip = document.querySelector('.skip');
  skip.focus({ preventScroll: true });
  const box = skip.getBoundingClientRect();
  const state = { focused: document.activeElement === skip, width: box.width, height: box.height,
    inViewport: box.left >= 0 && box.top >= 0 && box.right <= innerWidth && box.bottom <= innerHeight,
    href: skip.getAttribute('href'), target: Boolean(document.getElementById('main')) };
  skip.blur();
  return state;
}

function mastheadState() {
  const top = document.querySelector('#top'), byline = top.querySelector('.byline');
  const paperLinks = top.querySelector('.paper-links');
  const [after, before] = [parseFloat(getComputedStyle(byline).marginBottom), parseFloat(getComputedStyle(paperLinks).marginTop)];
  return { images: top.querySelectorAll('img').length,
    logoClass: [...document.querySelectorAll('[class]')].filter(element => /logo/i.test(element.getAttribute('class'))).length,
    logoSrc: [...document.querySelectorAll('img, source')].filter(element =>
      ['src', 'srcset'].some(attribute => /logo/i.test(element.getAttribute(attribute) || ''))).length,
    adjacent: Boolean(byline && paperLinks && byline.nextElementSibling === paperLinks),
    gap: paperLinks.getBoundingClientRect().top - byline.getBoundingClientRect().bottom,
    // Both are block-level boxes in normal flow, so their vertical margins collapse (CSS 2.2 §8.3.1).
    expectedGap: after >= 0 && before >= 0 ? Math.max(after, before) : after < 0 && before < 0 ? Math.min(after, before) : after + before,
    affiliation: top.querySelector('#affiliation').innerText,
    venue: top.querySelector('#venue').innerText,
    affiliationEn: top.querySelector('#affiliation').getAttribute('data-en'),
    venueEn: top.querySelector('#venue').getAttribute('data-en'),
    inViewport: ['.venue', '#authors', '#affiliation', '.paper-links'].every(selector => {
      const element = top.querySelector(selector), bounds = element?.getBoundingClientRect();
      return Boolean(bounds && bounds.width > 0 && bounds.height > 0 && bounds.left >= 0 && bounds.top >= 0 &&
        bounds.right <= innerWidth + 1 && bounds.bottom <= innerHeight + 1);
    }) };
}

async function recordMasthead(evaluate, result) {
  result.masthead = await evaluate(`(${mastheadState.toString()})()`);
  const state = result.masthead, text = TEXT[result.lang || 'en'];
  if (state.images !== 0) result.failures.push(`Masthead must contain no images, found ${state.images}`);
  if (state.logoClass !== 0) result.failures.push(`Page must contain no logo classes, found ${state.logoClass}`);
  if (state.logoSrc !== 0) result.failures.push(`Page must contain no logo image sources, found ${state.logoSrc}`);
  if (!state.adjacent) result.failures.push('Masthead byline must be followed directly by the resource row');
  if (Math.abs(state.gap - state.expectedGap) > 1) {
    result.failures.push(`Masthead gap ${state.gap}px differs from expected ${state.expectedGap}px`);
  }
  if (state.affiliation !== text.affiliation) result.failures.push('Masthead affiliation differs');
  if (state.venue !== text.venue) result.failures.push('Masthead venue differs');
  // kit.js keeps the markup's English in data-en; the publication facts must survive in it.
  if (state.affiliationEn !== TEXT.en.affiliation || state.venueEn !== TEXT.en.venue) {
    result.failures.push('Masthead English affiliation or venue (data-en) differs');
  }
  if (!state.inViewport) result.failures.push('Masthead venue, authors, affiliation and resource row must be in the first viewport');
}

// Desktop masthead layout: one button row, a short title, the venue line under the title block.
function mastheadLayoutState() {
  const top = document.querySelector('#top'), box = top.getBoundingClientRect();
  const titleBlock = top.querySelector('.title-block').getBoundingClientRect();
  const brandBlock = top.querySelector('.brand-block').getBoundingClientRect();
  const buttonTops = [...top.querySelectorAll('.paper-links a')].map(link => link.getBoundingClientRect().top);
  const range = document.createRange();
  range.selectNodeContents(top.querySelector('#title'));
  const lineTops = [];
  for (const rect of range.getClientRects()) {
    if (rect.width > 0 && rect.height > 0 && !lineTops.some(lineTop => Math.abs(lineTop - rect.top) <= 2)) lineTops.push(rect.top);
  }
  const inside = bounds => bounds.left >= box.left - 1 && bounds.right <= box.right + 1 &&
    bounds.top >= box.top - 1 && bounds.bottom <= box.bottom + 1;
  return { buttons: buttonTops.length, buttonSpread: Math.max(...buttonTops) - Math.min(...buttonTops), titleLines: lineTops.length,
    titleLeft: titleBlock.left, venueLeft: brandBlock.left, venueGap: brandBlock.top - titleBlock.bottom,
    inside: inside(titleBlock) && inside(brandBlock), scrollWidth: document.scrollingElement.scrollWidth, innerWidth,
    clip: { x: box.left + scrollX, y: box.top + scrollY, width: box.width, height: box.height } };
}

function zoomState(slot) {
  const figure = document.querySelector('[data-slot="' + slot + '"]');
  const button = figure.querySelector('[data-zoom]'), frame = figure.querySelector('.figure-frame');
  const image = frame.querySelector('img');
  return { pressed: button.getAttribute('aria-pressed'), label: button.textContent,
    zoomed: frame.classList.contains('is-zoomed'), width: image.getBoundingClientRect().width,
    inlineWidth: image.style.width, naturalWidth: image.naturalWidth, labelPx: Number(button.dataset.labelPx),
    scrollWidth: frame.scrollWidth, clientWidth: frame.clientWidth,
    tabindex: frame.getAttribute('tabindex'), role: frame.getAttribute('role'), labelRegion: frame.getAttribute('aria-label') };
}

async function checkZoom(send, evaluate, press, result) {
  const text = TEXT[result.lang];
  const controls = await evaluate(`([...document.querySelectorAll('[data-zoom]')].map(button => ({
    slot: button.closest('[data-slot]').dataset.slot, width: Number(button.dataset.zoomWidth),
    visible: Boolean(button.getClientRects().length) })))`);
  if (controls.length !== 3) result.failures.push(`Expected three zoom controls, found ${controls.length}`);
  if (result.width !== 390) {
    if (controls.some(control => control.visible)) result.failures.push('Zoom controls must be hidden on desktop');
    return;
  }
  result.zoom = [];
  for (const control of controls) {
    const before = await evaluate(`(${zoomState.toString()})(${JSON.stringify(control.slot)})`);
    const focusButton = () => evaluate(`document.querySelector('[data-slot="${control.slot}"] [data-zoom]').focus({ preventScroll: true }); true`);
    await focusButton();
    await press('Enter', 13);
    try {
      const enlarged = await evaluate(`(${zoomState.toString()})(${JSON.stringify(control.slot)})`);
      const record = { slot: control.slot, before, enlarged };
      result.zoom.push(record);
      if (!control.visible || enlarged.pressed !== 'true' || enlarged.label !== text.fit || !enlarged.zoomed ||
          Math.abs(enlarged.width - control.width) > 1 || enlarged.inlineWidth !== control.width + 'px' ||
          !(enlarged.scrollWidth > enlarged.clientWidth) || enlarged.tabindex !== '0' || enlarged.role !== 'region' ||
          enlarged.labelRegion !== text.region || !(enlarged.width / enlarged.naturalWidth * enlarged.labelPx >= 11)) {
        result.failures.push(`Zoom did not enlarge accessible raster labels: ${JSON.stringify(record)}`);
      }
      record.layout = await evaluate(`(${pageOverflow.toString()})()`);
      if (record.layout.overflow) result.failures.push(`Zoom caused page overflow: ${control.slot}`);
      const clip = await evaluate(`(async () => {
        const frame = document.querySelector('[data-slot="${control.slot}"] .figure-frame');
        frame.focus({ preventScroll: true });
        if (document.activeElement !== frame) throw new Error('Zoom region could not receive focus');
        frame.scrollLeft = 0;
        const deadline = performance.now() + 5000;
        let consecutive = 0;
        while (performance.now() < deadline) {
          frame.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const box = frame.getBoundingClientRect(), bar = document.querySelector('.nav').getBoundingClientRect();
          consecutive = box.top >= bar.bottom && box.bottom <= innerHeight ? consecutive + 1 : 0;
          if (consecutive === 2) {
            const height = Math.min(220, box.height), x = Math.max(0, box.left), right = Math.min(innerWidth, box.right);
            return { x: x + window.scrollX, y: (${JSON.stringify(control.slot)} === 'teaser' ? box.bottom - height : box.top) + window.scrollY,
              width: right - x, height, scale: 1 };
          }
        }
        throw new Error('Zoom label area did not settle in the viewport');
      })()`, 10000);
      const shot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 85, captureBeyondViewport: false, clip });
      record.screenshot = path.join(output, `zoom-390-${result.theme}${result.lang === 'ko' ? '-ko' : ''}-${control.slot}.jpg`);
      await writeFile(record.screenshot, Buffer.from(shot.data, 'base64'));
    } finally {
      await focusButton();
      await press('Enter', 13);
      const restored = await evaluate(`(${zoomState.toString()})(${JSON.stringify(control.slot)})`);
      if (result.zoom.length && result.zoom.at(-1).slot === control.slot) result.zoom.at(-1).restored = restored;
      if (restored.pressed !== 'false' || restored.label !== text.zoom || restored.zoomed ||
          restored.inlineWidth !== '' || Math.abs(restored.width - before.width) > 1 || restored.tabindex !== null ||
          restored.role !== null || restored.labelRegion !== null || restored.scrollWidth > restored.clientWidth + 1) {
        result.failures.push(`Zoom did not restore the image: ${JSON.stringify(restored)}`);
      }
    }
  }
}

function mastheadStyleState() {
  const brand = document.querySelector('.t-brand'), top = document.querySelector('#top');
  const venue = document.querySelector('#venue'), style = getComputedStyle(brand), box = venue.getBoundingClientRect();
  // Resolve --accent to a computed colour through a probe in the masthead.
  const probe = document.createElement('span');
  probe.style.color = 'var(--accent)';
  top.appendChild(probe);
  const accent = getComputedStyle(probe).color;
  probe.remove();
  const brandRange = document.createRange();
  brandRange.selectNodeContents(brand);
  const brandBox = brand.getBoundingClientRect(), brandText = brandRange.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(venue);
  const textBoxes = [...range.getClientRects()];
  const container = venue.closest('.venue').getBoundingClientRect();
  const prefix = document.querySelector('.venue .vh'), prefixBox = prefix.getBoundingClientRect();
  return { brand: brand.textContent, brandColor: style.color, accent, size: parseFloat(style.fontSize),
    brandFits: brandText.width > 0 && brandText.left >= brandBox.left - 1 && brandText.right <= brandBox.right + 1 &&
      brandText.left >= -1 && brandText.right <= innerWidth + 1,
    border: getComputedStyle(top).borderBottomColor, borderWidth: parseFloat(getComputedStyle(top).borderBottomWidth),
    venuePrefix: prefix.textContent, prefixHidden: prefixBox.width <= 1 && prefixBox.height <= 1, venue: venue.innerText,
    venueFits: box.left >= 0 && box.right <= innerWidth + 1 && box.width > 0 && box.height >= 44 &&
      textBoxes.every(line => line.left >= container.left - 1 && line.right <= container.right + 1 &&
        line.top >= container.top - 1 && line.bottom <= container.bottom + 1) &&
      venue.scrollWidth <= venue.clientWidth + 1 && venue.scrollHeight <= venue.clientHeight + 1 };
}

// Korean audit: a visible string (own text, aria-label, alt or title) in header, main or footer with no Hangul and a
// Latin word of 4+ letters fails, unless it is one of the parts that stay English or an allowed name.
function i18nAudit(allowed) {
  const hangul = /[\uac00-\ud7a3]/;
  const exempt = 'h1, #authors, #citation, [lang="en"], .credit a, .pvhv-demo-attrib a, code';
  const visible = element => {
    const style = getComputedStyle(element);
    return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.visibility !== 'collapse';
  };
  const describe = element => element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') +
    [...element.classList].map(name => '.' + name).join('');
  const offenders = [];
  let checked = 0;
  const test = (element, kind, value) => {
    const text = (value || '').trim();
    if (!text) return;
    checked += 1;
    if (hangul.test(text) || !/[A-Za-z]{4,}/.test(text) || allowed.includes(text)) return;
    offenders.push({ element: describe(element), kind, text: text.slice(0, 160) });
  };
  for (const element of document.querySelectorAll('header, header *, main, main *, footer, footer *')) {
    // Options have no boxes of their own; their select decides.
    if (!visible(element.tagName === 'OPTION' ? element.closest('select') : element) || element.closest(exempt)) continue;
    test(element, 'text', [...element.childNodes].filter(node => node.nodeType === 3).map(node => node.textContent).join(''));
    for (const attribute of ['aria-label', 'alt', 'title']) test(element, attribute, element.getAttribute(attribute));
  }
  return { allowed, checked, offenders };
}

function demoStrings() {
  const demo = document.querySelector('.pvhv-demo');
  return { legend: demo.querySelector('legend').textContent,
    labels: [...demo.querySelectorAll('label')].map(label => label.textContent),
    options: [...demo.querySelectorAll('option')].map(option => option.textContent),
    button: demo.querySelector('button').textContent, help: demo.querySelector('.pvhv-demo-help').textContent,
    status: demo.querySelector('.pvhv-demo-status').textContent,
    view: demo.querySelector('canvas').getAttribute('aria-label'),
    label: demo.querySelector('.pvhv-demo-label').textContent,
    attribution: demo.querySelector('.pvhv-demo-attrib').textContent };
}

async function capturePage(send, evaluate, result, prefix) {
  await evaluate(`(async () => {
    await Promise.all([...document.images].map(image => image.decode()));
    document.activeElement.blur();
    const deadline = performance.now() + 5000;
    let consecutive = 0, lastScrollY = window.scrollY;
    while (performance.now() < deadline) {
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      lastScrollY = window.scrollY;
      consecutive = lastScrollY === 0 ? consecutive + 1 : 0;
      if (consecutive === 2) return true;
    }
    throw new Error('Screenshot scroll did not settle; last scrollY: ' + lastScrollY);
  })()`, 30000);
  const deviceScaleFactor = await evaluate('devicePixelRatio');
  const segmentHeight = Math.floor(8000 / deviceScaleFactor);
  if (!(segmentHeight > 0)) throw new Error('Invalid screenshot scale');
      const metrics = await send('Page.getLayoutMetrics');
      const content = metrics.cssContentSize || metrics.contentSize;
  result.contentHeight = content.height;
  result.screenshots = [];
  result.screenshotSegments = [];
  if (!(result.contentHeight > 0)) throw new Error('Empty screenshot content');
  let top = 0;
  while (top < result.contentHeight) {
    const bottom = Math.min(top + segmentHeight, result.contentHeight);
    const clip = { x: 0, y: top, width: content.width, height: bottom - top, scale: 1 };
    const shot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 85, captureBeyondViewport: true, clip });
    const filename = path.join(output, `${prefix}-${result.width}-${result.theme}${result.lang === 'ko' ? '-ko' : ''}-part${result.screenshots.length + 1}.jpg`);
    await writeFile(filename, Buffer.from(shot.data, 'base64'));
    result.screenshots.push(filename);
    result.screenshotSegments.push({ y: top, height: bottom - top });
    top = bottom;
  }
}

async function checkWidth(origin, width, height, theme, lang = 'en') {
  const result = { width, height, theme, lang, errors: [], externalRequests: [], failedRequests: [], fontRequests: [], failures: [] };
  const text = TEXT[lang];
  // With no saved choice the kit follows the system, so the OS prefers the other theme and the toggle picks this one.
  const os = theme === 'dark' ? 'light' : 'dark';
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
  const press = async (name, code) => {
    // Enter needs its text so Chrome activates the focused button.
    const text = name === 'Enter' ? { text: '\r', unmodifiedText: '\r' } :
      name === ' ' ? { text: ' ', unmodifiedText: ' ' } : {};
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code: name === ' ' ? 'Space' : name,
      windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, ...text });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code: name === ' ' ? 'Space' : name,
      windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
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
  let collecting = false;
  const requests = new Set();
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
      requests.add(params.requestId);
      const url = params.request.url;
      if (params.type === 'Font' || /\.(?:woff2?|otf|ttf)(?:[?#]|$)/i.test(url)) result.fontRequests.push(url);
      if (!url.startsWith('data:') && !url.startsWith('blob:') && new URL(url).origin !== origin) result.externalRequests.push(url);
    }
    if (method === 'Network.loadingFailed' && requests.has(params.requestId)) {
      result.failedRequests.push({ requestId: params.requestId, error: params.errorText });
    }
    if (method === 'Network.responseReceived' && requests.has(params.requestId) && params.response.status >= 400) {
      result.failedRequests.push({ url: params.response.url, status: params.response.status });
    }
  };
  cdp.listeners.add(collect);
  try {
    for (const domain of ['Page', 'Runtime', 'Log', 'Network']) await send(`${domain}.enable`);
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: width === 390 ? 2 : 1, mobile: width === 390 });
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: os }] });
    try {
      await cdp.send('Browser.grantPermissions', { origin, permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] });
      result.clipboardPermission = 'granted';
    } catch (error) {
      result.clipboardPermission = error.message;
    }
    const reload = async final => {
      loaded = false;
      collecting = final;
      requests.clear();
      await send('Page.reload', { ignoreCache: true }, 30000);
      await waitFor(() => loaded, 30000, `Reload at ${width}px in ${theme}`);
    };
    loaded = false;
    const navigation = await send('Page.navigate', { url: `${origin}/index.html${lang === 'ko' ? '?lang=ko' : ''}` }, 30000);
    if (navigation.errorText) throw new Error(navigation.errorText);
    await waitFor(() => loaded, 30000, `Load at ${width}px`);
    await evaluate("localStorage.removeItem('ws-theme'); localStorage.removeItem('ws-lang'); true");
    await reload(false);
    const themeState = `({ theme: document.documentElement.dataset.theme, lang: document.documentElement.lang,
      saved: localStorage.getItem('ws-theme'), savedLang: localStorage.getItem('ws-lang'),
      label: document.querySelector('[data-theme-toggle]').getAttribute('aria-label'),
      langButton: document.querySelector('[data-lang-toggle]').textContent.trim(),
      scheme: getComputedStyle(document.documentElement).colorScheme })`;
    result.initialTheme = await evaluate(themeState);
    if (result.initialTheme.theme !== os || result.initialTheme.scheme !== os || result.initialTheme.saved !== null) {
      result.failures.push(`With no saved choice the page must follow the OS theme (${os})`);
    }
    if (result.initialTheme.lang !== lang || result.initialTheme.langButton !== text.langButton) {
      result.failures.push(`Page must open in ${lang} with the language toggle offering the other language`);
    }
    await evaluate("document.querySelector('[data-theme-toggle]').focus(); true");
    await press('Enter', 13);
    result.themeToggle = await evaluate(themeState);
    if (result.themeToggle.theme !== theme || result.themeToggle.saved !== theme ||
        result.themeToggle.label !== (theme === 'dark' ? text.toLight : text.toDark) || result.themeToggle.scheme !== theme) {
      result.failures.push('Keyboard theme toggle did not update theme, saved choice, action label and colour scheme');
    }
    await reload(true);
    result.finalTheme = await evaluate(themeState);
    if (result.finalTheme.theme !== theme || result.finalTheme.scheme !== theme || result.finalTheme.saved !== theme ||
        result.finalTheme.label !== (theme === 'dark' ? text.toLight : text.toDark) ||
        result.finalTheme.lang !== lang || result.finalTheme.savedLang !== null) {
      result.failures.push('Theme did not survive the final reload');
    }
    try {
      await evaluate('document.fonts.ready.then(() => true)', 10000);
      result.fonts = await evaluate(`({ body: getComputedStyle(document.body).fontFamily,
        loaded: [...document.fonts].filter(face => face.status === 'loaded').map(face => face.family.replace(/["']/g, '') + ' ' + face.weight) })`);
      const local = result.fontRequests.filter(url => url.startsWith(`${origin}/assets/fonts/`));
      const korean = result.fontRequests.filter(url => /\/SCDream\d\.otf$/.test(url));
      if (local.length !== result.fontRequests.length) result.failures.push(`Font requests outside assets/fonts: ${JSON.stringify(result.fontRequests)}`);
      if (lang === 'en' && (!/^Lato,/.test(result.fonts.body) || !result.fonts.loaded.some(face => face.startsWith('Lato ')) || korean.length)) {
        result.failures.push(`English must render in the bundled Lato without requesting S-Core Dream: ${JSON.stringify(result.fonts)}`);
      }
      if (lang === 'ko' && (!/^"S-Core Dream",/.test(result.fonts.body) || !korean.length ||
          !result.fonts.loaded.some(face => face.startsWith('S-Core Dream ')))) {
        result.failures.push(`Korean must render in the bundled S-Core Dream: ${JSON.stringify(result.fonts)}`);
      }
    } catch (error) {
      result.failures.push(`Fonts: ${error.message}`);
    }
    try {
      await recordMasthead(evaluate, result);
      result.skipLink = await evaluate(`(${skipLinkState.toString()})()`);
      if (!result.skipLink.focused || result.skipLink.height < 44 || !result.skipLink.inViewport ||
          result.skipLink.href !== '#main' || !result.skipLink.target) {
        result.failures.push(`Focused skip link must be a visible 44 px target to #main: ${JSON.stringify(result.skipLink)}`);
      }
    } catch (error) {
      result.failures.push(`Masthead: ${error.message}`);
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
          await evaluate("document.querySelector('.pvhv-demo button').focus(); true");
          await press('Enter', 13);
          await evaluate('PVHVDemo.whenIdle().then(() => true)', 30000);
          const reset = await evaluate('PVHVDemo.getState()');
          result.demoKeyboard.reset = reset.t;
          if (reset.t.some((value, index) => Math.abs(value - before.t[index]) > tolerance)) {
            result.failures.push('Reset view did not return t to its value before ArrowRight');
          }
        }
        result.demoControls = [];
        for (const mode of ['single', 'bdp', 'edp']) {
          // The native radio change comes from Space, followed by the render completion promise.
          await evaluate(`document.querySelector('.pvhv-demo input[type="radio"][value="${mode}"]').focus(); true`);
          await press(' ', 32);
          await evaluate('PVHVDemo.whenIdle().then(() => true)', 30000);
          const observed = await evaluate(`({ mode: PVHVDemo.getState().mode,
            checked: document.querySelector('.pvhv-demo input[type="radio"]:checked')?.value,
            status: document.querySelector('.pvhv-demo-status').textContent.trim() })`);
          result.demoControls.push({ requested: mode, ...observed });
          if (observed.mode !== mode || observed.checked !== mode || !observed.status) {
            result.failures.push(`Warping input ${mode} did not update state and status`);
          }
        }
        const highlight = await evaluate('PVHVDemo.getState().highlight');
        await evaluate("document.querySelector('.pvhv-demo input[type=checkbox]').focus(); true");
        await press(' ', 32);
        await evaluate('PVHVDemo.whenIdle().then(() => true)', 30000);
        result.demoHighlight = await evaluate(`({ state: PVHVDemo.getState().highlight,
          checked: document.querySelector('.pvhv-demo input[type=checkbox]').checked })`);
        if (result.demoHighlight.state !== !highlight || result.demoHighlight.checked !== !highlight) {
          result.failures.push('Highlight checkbox did not update the highlight state');
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
      return { ...(${pageOverflow.toString()})(), slots: document.querySelectorAll('[data-slot]').length,
        unfilledSlots: [...document.querySelectorAll('.slot-box')].filter(e => !e.closest('#demo-root')).length,
        demoPlaceholders: document.querySelectorAll('#demo-root .slot-box').length,
        removedSectionPresent: Boolean(document.querySelector('#video')),
        iframeCount: document.querySelectorAll('iframe').length,
        resourceLabels: [...document.querySelectorAll('.paper-links a')].map(anchor => anchor.textContent.trim()),
        images: [...document.images].map(image => ({ src: image.getAttribute('src'), complete: image.complete,
          naturalWidth: image.naturalWidth })) };
      })()`));
      if (result.overflow) result.failures.push(`Horizontal overflow: ${result.scrollWidth} > ${result.innerWidth}; ${result.widestOffender}`);
      if (result.slots !== 5) result.failures.push(`Expected 5 slots, found ${result.slots}`);
      if (result.unfilledSlots) result.failures.push(`${result.unfilledSlots} slot-box placeholders outside demo`);
      if (result.demoPlaceholders) result.failures.push('Demo placeholder was not replaced');
      if (result.removedSectionPresent) result.failures.push('Removed media section is still present');
      if (result.iframeCount) result.failures.push(`Expected no iframe, found ${result.iframeCount}`);
      if (JSON.stringify(result.resourceLabels) !== JSON.stringify(text.resources)) {
        result.failures.push(`Unexpected resource labels: ${JSON.stringify(result.resourceLabels)}`);
      }
      for (const image of result.images) {
        if (!image.complete || image.naturalWidth <= 0) result.failures.push(`Image not decoded: ${image.src}`);
      }
    } catch (error) {
      result.failures.push(`Layout: ${error.message}`);
    }

    try {
      // Method panels: smallest SVG text run at its rendered size (font-size x rendered width / viewBox width).
      result.methodLabels = await evaluate(`(async () => {
        const panels = [...document.querySelectorAll('[data-slot="method-overview"] img')];
        return Promise.all(panels.map(async image => {
          const source = await (await fetch(image.getAttribute('src'))).text();
          const svg = new DOMParser().parseFromString(source, 'image/svg+xml').documentElement;
          const viewBoxWidth = Number(svg.getAttribute('viewBox').split(/\\s+/)[2]);
          const sizes = [];
          const walk = (node, inherited) => {
            const size = Number(node.getAttribute('font-size') || inherited);
            const own = [...node.childNodes].some(child => child.nodeType === 3 && child.textContent.trim());
            if (['text', 'tspan'].includes(node.localName) && own) sizes.push(size);
            for (const child of node.children) walk(child, size);
          };
          walk(svg, 16);
          const minUnits = Math.min(...sizes);
          return { src: image.getAttribute('src'), renderedWidth: image.clientWidth, viewBoxWidth, minUnits,
            textRuns: sizes.length, minLabelPx: Math.round(minUnits * image.clientWidth / viewBoxWidth * 100) / 100 };
        }));
      })()`);
      if (result.methodLabels.length !== 3) result.failures.push(`Expected 3 method panels, found ${result.methodLabels.length}`);
      for (const panel of result.methodLabels) {
        if (!(panel.textRuns > 0) || !(panel.minLabelPx >= MIN_LABEL_PX)) {
          result.failures.push(`Method label below ${MIN_LABEL_PX}px: ${panel.src} ${panel.minLabelPx}px`);
        }
      }
    } catch (error) {
      result.failures.push(`Method labels: ${error.message}`);
    }

    try {
      await checkZoom(send, evaluate, press, result);
    } catch (error) {
      result.failures.push(`Zoom: ${error.message}`);
    }

    try {
      if (width === 390) await evaluate("document.getElementById('menu-toggle').click(); true");
      // The abstract is collapsed on load; open it so its text can be sampled (and audited in Korean).
      await evaluate("document.querySelector('details.abstract').open = true; true");
      try {
        const samples = Object.entries({
          body: 'body', authors: '#authors', affiliation: '#affiliation', venue: '#venue',
          navLink: '#section-nav a', themeToggle: '[data-theme-toggle]', langToggle: '[data-lang-toggle]',
          resourcePrimary: '.paper-links a.primary', resourceLink: '.paper-links a:not(.primary)', title: '#title',
          band: '.band p', abstract: '#abstract-text', ...(lang === 'ko' ? { abstractKo: '#abstract p[lang="ko"]:not(.note)' } : {}),
          panelCaption: '.panel-cap', figureCaption: '#overview figcaption',
          methodCaption: '.method-fig figcaption', disc: '#method .disc', reimplFlag: '.reimpl-flag',
          noteLink: '#reimplementation a', noteText: '#reimplementation p', citation: '#citation',
          copyButton: '#copy', footer: 'footer p', demoHelp: '.pvhv-demo-help', status: '.pvhv-demo-status',
          legend: '.pvhv-demo legend', radioLabel: '.pvhv-demo-modes label', select: '.pvhv-demo select',
          button: '.pvhv-demo button', captionLabel: '.pvhv-demo-label', attribution: '.pvhv-demo-attrib',
          attributionLink: '.pvhv-demo-attrib a', busy: '.pvhv-demo-busy', ...(width === 390 ? { menu: '#menu-toggle', zoomButton: '[data-zoom]' } : {})
        }).map(([name, selector]) => ({ name, selector }));
        samples.push({ name: 'brand', selector: '.t-brand', minimum: 3 }, { name: 'heading', selector: 'h2', minimum: 3 });
        result.contrast = await evaluate(`(${renderedContrast.toString()})(${JSON.stringify(samples)}, ${JSON.stringify({ theme, showBusy: true })})`);
        for (const pair of result.contrast) {
          if (pair.missing) result.failures.push(`Required contrast sample missing or hidden: ${pair.name}`);
          else if (!(pair.ratio >= pair.minimum)) {
            result.failures.push(`Computed contrast ${pair.name} ${pair.ratio}:1 < ${pair.minimum}:1`);
          }
        }
        result.smallTargets = await evaluate(`(${targetSizes.toString()})()`);
        if (result.smallTargets.length) result.failures.push(`Targets below 44 px: ${JSON.stringify(result.smallTargets)}`);
        if (lang === 'ko') {
          result.i18nAudit = await evaluate(`(${i18nAudit.toString()})(${JSON.stringify(I18N_ALLOWED)})`);
          if (result.demo !== 'ready') result.failures.push('Korean audit needs the demo ready');
          if (result.i18nAudit.offenders.length) result.failures.push(`Untranslated visible strings: ${JSON.stringify(result.i18nAudit.offenders)}`);
        }
      } finally {
        if (width === 390) await evaluate("if (document.getElementById('menu-toggle').getAttribute('aria-expanded') === 'true') document.getElementById('menu-toggle').click(); true");
      }
      result.masthead = { ...result.masthead, ...await evaluate(`(${mastheadStyleState.toString()})()`) };
      if (result.masthead.brand !== 'PVHV' || result.masthead.brandColor !== result.masthead.accent || result.masthead.size < 24) {
        result.failures.push('PVHV brand must be large text in the accent colour');
      }
      if (!result.masthead.brandFits) result.failures.push('PVHV brand text exceeds its box or the viewport');
      if (result.masthead.border !== result.masthead.accent || !(result.masthead.borderWidth > 0)) {
        result.failures.push('Masthead must end in an accent border');
      }
      if (result.masthead.venuePrefix !== text.venuePrefix || !result.masthead.prefixHidden) {
        result.failures.push('Venue prefix must be present for screen readers and visually hidden');
      }
      if (result.masthead.venue !== text.venue || !result.masthead.venueFits) {
        result.failures.push('Venue text changed or is clipped');
      }
    } catch (error) {
      result.failures.push(`Contrast/masthead/targets: ${error.message}`);
    }

    try {
      const stops = ['.skip', '.brand', ...(width === 390 ? ['#menu-toggle'] :
        ['#section-nav a:nth-child(1)', '#section-nav a:nth-child(2)', '#section-nav a:nth-child(3)',
          '#section-nav a:nth-child(4)', '#section-nav a:nth-child(5)']), '[data-theme-toggle]', '[data-lang-toggle]',
        '.paper-links a:nth-child(1)', '.paper-links a:nth-child(2)', '.paper-links a:nth-child(3)', '.paper-links a:nth-child(4)',
        '.reimpl-flag a', '#venue'];
      await evaluate(`window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
        document.body.tabIndex = -1; document.body.focus(); document.body.removeAttribute('tabindex'); true`);
      result.firstTabStops = [];
      for (const selector of stops) {
        await key('Tab', 9);
        const stop = await evaluate(`(() => {
          const active = document.activeElement, style = getComputedStyle(active);
          return { expected: ${JSON.stringify(selector)}, matches: active.matches(${JSON.stringify(selector)}),
            tag: active.tagName, label: active.getAttribute('aria-label') || active.textContent.trim(),
            outline: style.outlineStyle, outlineWidth: parseFloat(style.outlineWidth) };
        })()`);
        result.firstTabStops.push(stop);
        if (!stop.matches || stop.outline === 'none' || !(stop.outlineWidth > 0)) {
          result.failures.push(`Tab order or focus ring at ${selector}: ${JSON.stringify(stop)}`);
        }
      }
    } catch (error) {
      result.failures.push(`First Tab stops: ${error.message}`);
    }

    try {
      const navState = `(() => {
        const menu = document.getElementById('menu-toggle'), nav = document.getElementById('section-nav');
        const active = document.activeElement;
        return { menuVisible: Boolean(menu.getClientRects().length), expanded: menu.getAttribute('aria-expanded'),
          linksVisible: [...nav.querySelectorAll('a')].filter(a => a.getClientRects().length).length,
          focus: active === menu ? 'menu' : nav.contains(active) ? 'link:' + active.textContent.trim() : active.tagName.toLowerCase() };
      })()`;
      const steps = [];
      const record = async label => { const state = await evaluate(navState); steps.push({ step: label, ...state }); return state; };
      const initial = await record('load');
      if (width > 859) {
        if (initial.menuVisible || initial.linksVisible !== 5) result.failures.push('Desktop navigation must show 5 links and no Menu button');
      } else {
        const expect = (state, wanted, label) => {
          for (const [field, value] of Object.entries(wanted)) {
            if (state[field] !== value) result.failures.push(`Menu ${label}: ${field} = ${state[field]}, expected ${value}`);
          }
        };
        expect(initial, { menuVisible: true, expanded: 'false', linksVisible: 0 }, 'on load');
        await evaluate("document.getElementById('menu-toggle').focus(); true");
        await press('Enter', 13);
        expect(await record('Enter'), { expanded: 'true', linksVisible: 5, focus: 'menu' }, 'after Enter');
        await press('Escape', 27);
        expect(await record('Escape'), { expanded: 'false', linksVisible: 0, focus: 'menu' }, 'after Escape');
        await press('ArrowDown', 40);
        expect(await record('ArrowDown'), { expanded: 'true', linksVisible: 5, focus: `link:${text.firstLinks[0]}` }, 'after ArrowDown');
        await press('ArrowDown', 40);
        expect(await record('ArrowDown 2'), { focus: `link:${text.firstLinks[1]}` }, 'after second ArrowDown');
        await press('Escape', 27);
        expect(await record('Escape 2'), { expanded: 'false', linksVisible: 0, focus: 'menu' }, 'after Escape from a link');
        await press('Enter', 13);
        await evaluate("document.querySelector('#section-nav a[href=\"#method\"]').click(); true");
        const chosen = await record('select Method');
        expect(chosen, { expanded: 'false', linksVisible: 0 }, 'after choosing a section');
        if (await evaluate('location.hash') !== '#method') result.failures.push('Choosing Method did not navigate to #method');
      }
      result.menu = steps;
    } catch (error) {
      result.failures.push(`Menu: ${error.message}`);
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
            return { mode: 'clipboard', exact: actual === expected && status === ${JSON.stringify(text.copied)}, status };
          } catch {
            return { mode: 'selection', exact: getSelection().toString() === expected && status.startsWith(${JSON.stringify(text.selected)}), status };
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
      await capturePage(send, evaluate, result, 'shot');
    } catch (error) {
      result.failures.push(`Screenshot: ${error.message}`);
    }
    if (lang === 'ko' && width === 1440) {
      try {
        // The second scene in Korean: its attribution, status and every other string, audited again.
        if (result.demo !== 'ready') throw new Error('the demo is not ready');
        await evaluate("PVHVDemo.setScene('blocks').then(() => PVHVDemo.whenIdle()).then(() => true)", 180000);
        result.blocks = await evaluate(`({ scene: PVHVDemo.getState().scene,
          attribution: document.querySelector('.pvhv-demo-attrib').textContent,
          status: document.querySelector('.pvhv-demo-status').textContent,
          select: document.querySelector('.pvhv-demo select').selectedOptions[0].textContent,
          audit: (${i18nAudit.toString()})(${JSON.stringify(I18N_ALLOWED)}) })`);
        if (result.blocks.scene !== 'blocks' || result.blocks.attribution !== text.blocksAttribution ||
            !HANGUL.test(result.blocks.status) || result.blocks.select !== '블록') {
          result.failures.push(`Korean blocks scene attribution or status: ${JSON.stringify(result.blocks)}`);
        }
        if (result.blocks.audit.offenders.length) {
          result.failures.push(`Untranslated visible strings in the blocks scene: ${JSON.stringify(result.blocks.audit.offenders)}`);
        }
      } catch (error) {
        result.failures.push(`Korean blocks scene: ${error.message}`);
      }
    }
    // Protocol round trip drains resource/error events before collection ends.
    try {
      await evaluate('document.readyState');
    } catch (error) {
      result.failures.push(`Resource/error collection: ${error.message}`);
    }
    collecting = false;
    if (result.errors.length) result.failures.push(`${result.errors.length} console/runtime/log errors`);
    if (result.externalRequests.length) result.failures.push(`${result.externalRequests.length} external requests`);
    if (result.failedRequests.length) result.failures.push(`Failed resource requests: ${JSON.stringify(result.failedRequests)}`);
  } catch (error) {
    result.failures.push(error.message);
  } finally {
    collecting = false;
    cdp.listeners.delete(collect);
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
  return result;
}

async function checkVariant(origin, width, height, theme, kind, lang = 'en') {
  const result = { width, height, theme, kind, lang, errors: [], externalRequests: [], failedRequests: [], failures: [] };
  const text = TEXT[lang];
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params = {}, milliseconds) => cdp.send(method, params, sessionId, milliseconds);
  const evaluate = async (expression, milliseconds = 10000) => {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, milliseconds);
    if (value.exceptionDetails) throw new Error(value.exceptionDetails.exception?.description || value.exceptionDetails.text);
    return value.result.value;
  };
  let loaded = false;
  const collect = event => {
    if (event.sessionId !== sessionId) return;
    const { method, params } = event;
    if (method === 'Page.loadEventFired') loaded = true;
    if (method === 'Runtime.consoleAPICalled' && ['error', 'assert'].includes(params.type)) {
      result.errors.push(params.args.map(arg => arg.value ?? arg.description ?? arg.type).join(' '));
    }
    if (method === 'Runtime.exceptionThrown') result.errors.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
    if (method === 'Log.entryAdded' && params.entry.level === 'error') result.errors.push(params.entry.text);
    if (method === 'Network.requestWillBeSent') {
      const url = params.request.url;
      if (!url.startsWith('data:') && !url.startsWith('blob:') && new URL(url).origin !== origin) result.externalRequests.push(url);
    }
    if (method === 'Network.loadingFailed') result.failedRequests.push(params.errorText);
    if (method === 'Network.responseReceived' && params.response.status >= 400) {
      result.failedRequests.push({ url: params.response.url, status: params.response.status });
    }
  };
  cdp.listeners.add(collect);
  try {
    for (const domain of ['Page', 'Runtime', 'Log', 'Network']) await send(`${domain}.enable`);
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: width === 390 ? 2 : 1, mobile: width === 390 });
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' },
      { name: 'prefers-reduced-motion', value: kind === 'reduced' ? 'reduce' : 'no-preference' }] });
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `
      localStorage.setItem('ws-theme', ${JSON.stringify(theme)});
      localStorage.removeItem('ws-lang');
      ${kind === 'fallback' ? `if (window.WebGL2RenderingContext) {
        const original = WebGL2RenderingContext.prototype.getExtension;
        WebGL2RenderingContext.prototype.getExtension = function (name) {
          return name === 'EXT_color_buffer_float' ? null : original.call(this, name);
        };
      }` : ''}` });
    const navigation = await send('Page.navigate', { url: `${origin}/index.html${lang === 'ko' ? '?lang=ko' : ''}` }, 30000);
    if (navigation.errorText) throw new Error(navigation.errorText);
    await waitFor(() => loaded, 30000, `${kind} load at ${width}px in ${theme}`);
    result.theme = await evaluate('document.documentElement.dataset.theme');
    if (result.theme !== theme) result.failures.push(`Expected ${theme}, found ${result.theme}`);
    if (await evaluate('document.documentElement.lang') !== lang) result.failures.push(`Expected html[lang=${lang}]`);
    if (kind === 'masthead') {
      await recordMasthead(evaluate, result);
      const state = result.mastheadLayout = await evaluate(`(${mastheadLayoutState.toString()})()`);
      if (state.buttons !== 4 || state.buttonSpread > 1) result.failures.push(`Resource buttons must share one row: ${state.buttons} buttons, tops spread ${state.buttonSpread}px`);
      if (state.titleLines < 1 || state.titleLines > 3) result.failures.push(`Title must wrap to at most 3 lines, found ${state.titleLines}`);
      if (Math.abs(state.venueLeft - state.titleLeft) > 2) result.failures.push(`Venue left edge ${state.venueLeft}px must match the title block's ${state.titleLeft}px`);
      if (!(state.venueGap > 0 && state.venueGap <= 48)) result.failures.push(`Venue must sit at most 48px below the title block, gap ${state.venueGap}px`);
      if (!state.inside) result.failures.push('Title block and venue must lie inside #top');
      if (state.scrollWidth > state.innerWidth) result.failures.push(`Horizontal overflow: scrollWidth ${state.scrollWidth} > ${state.innerWidth}`);
      const shot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 85, captureBeyondViewport: true,
        clip: { ...state.clip, scale: 1 } });
      result.screenshot = path.join(output, `masthead-${width}-${theme}-${lang}.jpg`);
      await writeFile(result.screenshot, Buffer.from(shot.data, 'base64'));
    } else if (kind === 'fallback') {
      await waitFor(() => evaluate("Boolean(document.querySelector('.pvhv-demo-fallback'))"), 30000, 'Fallback notice');
      result.notice = await evaluate(`(() => {
        const box = document.querySelector('.pvhv-demo-fallback'), bounds = box.getBoundingClientRect();
        return { text: box.innerText, role: box.getAttribute('role'), visible: bounds.width > 0 && bounds.height > 0,
          label: document.querySelector('.pvhv-demo-label')?.innerText,
          attribution: document.querySelector('.pvhv-demo-attrib')?.innerText,
          links: [...document.querySelectorAll('.pvhv-demo-attrib a')].map(a => a.getAttribute('href')) };
      })()`);
      if (!result.notice.visible || result.notice.text !== text.fallback || result.notice.role !== 'status') {
        result.failures.push('Expected visible fallback notice with its exact text and status role');
      }
      if (result.notice.label !== text.label || result.notice.attribution !== text.attribution ||
          JSON.stringify(result.notice.links) !== JSON.stringify(['https://polyhaven.com/a/chess_set', 'https://creativecommons.org/publicdomain/zero/1.0/'])) {
        result.failures.push('Fallback must retain its re-implementation label and linked scene attribution');
      }
      const samples = Object.entries({ fallback: '.pvhv-demo-fallback p', label: '.pvhv-demo-label',
        attribution: '.pvhv-demo-attrib', attributionLink: '.pvhv-demo-attrib a' })
        .map(([name, selector]) => ({ name, selector }));
      result.contrast = await evaluate(`(${renderedContrast.toString()})(${JSON.stringify(samples)})`);
      for (const pair of result.contrast) {
        if (pair.missing || !(pair.ratio >= MIN_TEXT_CONTRAST)) result.failures.push(`Fallback contrast: ${JSON.stringify(pair)}`);
      }
    } else if (kind === 'switch') {
      // Runtime language switch: the demo re-labels itself in place, keeps its state, controls and focus.
      await waitFor(() => evaluate("Boolean(window.PVHVDemo || document.querySelector('.pvhv-demo-fallback'))"), 120000, 'Switch demo mount');
      if (!await evaluate('Boolean(window.PVHVDemo)')) throw new Error('the demo fell back; the language switch needs it running');
      await evaluate('PVHVDemo.ready.then(() => PVHVDemo.whenIdle()).then(() => true)', 120000);
      // Leave the defaults so an unchanged state is meaningful: BDP, highlight on, an off-centre view.
      await evaluate(`(() => { const E = PVHVDemo.getState().E; PVHVDemo.setMode('bdp'); PVHVDemo.setHighlight(true);
        PVHVDemo.setView(E * .3, -E * .2); return PVHVDemo.whenIdle().then(() => true); })()`, 30000);
      const snapshot = `(() => ({ lang: document.documentElement.lang, strings: (${demoStrings.toString()})(),
        state: (({ scene, mode, t, highlight }) => ({ scene, mode, t, highlight }))(PVHVDemo.getState()),
        checked: document.querySelector('.pvhv-demo input[type="radio"]:checked').value,
        sameNodes: window.__pvhvSwitchNodes.every((node, index) => node.isConnected &&
          node === [...document.querySelectorAll('.pvhv-demo canvas, .pvhv-demo input, .pvhv-demo select, .pvhv-demo button')][index]),
        focusKept: document.activeElement === window.__pvhvSwitchNodes.find(node => node.tagName === 'SELECT'),
        nav: document.querySelector('#section-nav a').textContent, alt: document.querySelector('[data-slot="teaser"] img').alt,
        zoom: document.querySelector('[data-zoom]').textContent }))()`;
      await evaluate(`window.__pvhvSwitchNodes = [...document.querySelectorAll('.pvhv-demo canvas, .pvhv-demo input, .pvhv-demo select, .pvhv-demo button')];
        document.querySelector('.pvhv-demo select').focus(); true`);
      const toggle = "document.querySelector('[data-lang-toggle]').click(); PVHVDemo.whenIdle().then(() => true)";
      const english = await evaluate(snapshot);
      await evaluate(toggle, 30000);
      const korean = await evaluate(snapshot);
      await evaluate(toggle, 30000);
      const back = await evaluate(snapshot);
      await evaluate("localStorage.removeItem('ws-lang'); true");
      result.languageSwitch = { english, korean, back };
      const strings = value => [value.legend, ...value.labels, ...value.options, value.button, value.help, value.status,
        value.view, value.label, value.attribution];
      const untranslated = strings(korean.strings).filter(value => !HANGUL.test(value));
      if (english.lang !== 'en' || korean.lang !== 'ko' || back.lang !== 'en') result.failures.push('Language toggle did not flip html[lang]');
      if (english.strings.attribution !== TEXT.en.attribution || english.strings.label !== TEXT.en.label ||
          strings(english.strings).some(value => HANGUL.test(value))) result.failures.push('Demo did not start in English');
      if (untranslated.length) result.failures.push(`Demo strings not Korean after the switch: ${JSON.stringify(untranslated)}`);
      if (korean.strings.attribution !== TEXT.ko.attribution || korean.strings.label !== TEXT.ko.label) {
        result.failures.push('Demo label or attribution prefix not Korean after the switch');
      }
      if (!HANGUL.test(korean.nav) || !HANGUL.test(korean.alt) || !HANGUL.test(korean.zoom) || korean.zoom !== TEXT.ko.zoom) {
        result.failures.push('Page navigation, image alt or zoom label not Korean after the switch');
      }
      for (const [name, value] of [['Korean', korean], ['English again', back]]) {
        if (JSON.stringify(value.state) !== JSON.stringify(english.state) || value.checked !== english.state.mode) {
          result.failures.push(`Demo state changed by the switch to ${name}`);
        }
        if (!value.sameNodes || !value.focusKept) result.failures.push(`Demo controls rebuilt or focus lost by the switch to ${name}`);
      }
      if (JSON.stringify(back.strings) !== JSON.stringify(english.strings) || back.nav !== english.nav ||
          back.alt !== english.alt || back.zoom !== english.zoom) {
        result.failures.push('Switching back did not restore the exact English strings');
      }
    } else {
      await waitFor(() => evaluate("Boolean(window.PVHVDemo || document.querySelector('.pvhv-demo-fallback'))"), 120000, 'Reduced motion demo mount');
      if (await evaluate('Boolean(window.PVHVDemo)')) {
        await evaluate('PVHVDemo.ready.then(() => PVHVDemo.whenIdle()).then(() => true)', 120000);
      } else if (!allowFallback) result.failures.push('Reduced motion demo fell back');
      await evaluate('Promise.all([...document.images].map(image => image.decode())).then(() => true)', 30000);
      result.motion = await evaluate(`(() => {
        const chevron = getComputedStyle(document.querySelector('.abstract summary'), '::after');
        return { animations: document.getAnimations().length, chevronTransition: chevron.transitionDuration,
          scroll: getComputedStyle(document.documentElement).scrollBehavior,
          sections: [...document.querySelectorAll('section')].map(section => ({ id: section.id,
            opacity: getComputedStyle(section).opacity, width: section.getBoundingClientRect().width,
            height: section.getBoundingClientRect().height })),
          placeholderCount: document.querySelectorAll('.slot-box').length,
          mounted: Boolean(document.querySelector('#demo-root .pvhv-demo')),
          images: [...document.images].map(image => ({ src: image.getAttribute('src'), complete: image.complete,
            width: image.naturalWidth })) };
      })()`);
      if (result.motion.animations !== 0 || result.motion.chevronTransition !== '0s' ||
          result.motion.scroll !== 'auto') result.failures.push('Reduced motion must show no animations or transitions and scroll instantly');
      if (!result.motion.sections.length || result.motion.sections.some(section => section.opacity !== '1' || section.width <= 0 || section.height <= 0)) {
        result.failures.push('Reduced motion contains a hidden or empty section');
      }
      if (result.motion.placeholderCount || !result.motion.mounted) result.failures.push('Reduced motion demo did not mount');
      if (result.motion.images.some(image => !image.complete || image.width <= 0)) result.failures.push('Reduced motion images did not decode');
      await capturePage(send, evaluate, result, 'reduced');
    }
    result.layout = await evaluate(`(${pageOverflow.toString()})()`);
    if (result.layout.overflow) result.failures.push(`Horizontal overflow: ${JSON.stringify(result.layout)}`);
    await evaluate('document.readyState');
    if (result.errors.length) result.failures.push(`${result.errors.length} console/runtime/log errors`);
    if (result.externalRequests.length) result.failures.push(`${result.externalRequests.length} external requests`);
    if (result.failedRequests.length) result.failures.push(`Failed resource requests: ${JSON.stringify(result.failedRequests)}`);
  } catch (error) {
    result.failures.push(error.message);
  } finally {
    cdp.listeners.delete(collect);
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
  return result;
}

async function checkMenuHistory(origin) {
  // Phone menu choices must be real navigations: Back returns to the previous section, not off the page.
  const result = { width: 390, steps: [], failures: [] };
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params = {}, milliseconds) => cdp.send(method, params, sessionId, milliseconds);
  const evaluate = async (expression, milliseconds = 10000) => {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, milliseconds);
    if (value.exceptionDetails) throw new Error(value.exceptionDetails.exception?.description || value.exceptionDetails.text);
    return value.result.value;
  };
  const press = async (name, code) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code: name, windowsVirtualKeyCode: code,
      nativeVirtualKeyCode: code, ...(name === 'Enter' ? { text: '\r', unmodifiedText: '\r' } : {}) });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code: name, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
  };
  let loaded = false;
  const listen = event => { if (event.sessionId === sessionId && event.method === 'Page.loadEventFired') loaded = true; };
  cdp.listeners.add(listen);
  const state = `({ hash: location.hash, sameDocument: window.__pvhvHistoryMarker === true,
    expanded: document.getElementById('menu-toggle').getAttribute('aria-expanded'),
    linksVisible: [...document.querySelectorAll('#section-nav a')].filter(a => a.getClientRects().length).length,
    historyLength: history.length })`;
  const record = async step => { const value = { step, ...await evaluate(state) }; result.steps.push(value); return value; };
  try {
    await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await send('Page.addScriptToEvaluateOnNewDocument', { source: "localStorage.setItem('ws-theme', 'light'); localStorage.removeItem('ws-lang');" });
    const navigation = await send('Page.navigate', { url: `${origin}/index.html` }, 30000);
    if (navigation.errorText) throw new Error(navigation.errorText);
    await waitFor(() => loaded, 30000, 'Menu history load');
    await evaluate('window.__pvhvHistoryMarker = true; true');
    for (const target of ['demo', 'bibtex']) {
      await evaluate("document.getElementById('menu-toggle').focus(); true");
      await press('Enter', 13);
      const open = await record(`open menu for #${target}`);
      if (open.expanded !== 'true' || open.linksVisible !== 5) result.failures.push(`Menu did not open before choosing #${target}`);
      await evaluate(`document.querySelector('#section-nav a[href="#${target}"]').focus(); true`);
      await press('Enter', 13);
      await waitFor(() => evaluate(`location.hash === '#${target}'`), 5000, `Navigate to #${target}`);
      const chosen = await record(`choose #${target}`);
      if (!chosen.sameDocument || chosen.expanded !== 'false' || chosen.linksVisible !== 0) {
        result.failures.push(`Choosing #${target} must keep the page and close the menu: ${JSON.stringify(chosen)}`);
      }
    }
    const back = async (expected, label) => {
      loaded = false;
      await evaluate('history.back(); true');
      try {
        await waitFor(() => evaluate(`location.hash === ${JSON.stringify(expected)}`), 5000, label);
      } catch (error) {
        result.failures.push(`${label}: ${error.message}`);
      }
      const value = await record(label);
      if (value.hash !== expected || !value.sameDocument || loaded) {
        result.failures.push(`${label} must stay on the page at ${JSON.stringify(expected)}: ${JSON.stringify(value)}`);
      }
    };
    await back('#demo', 'Back after #bibtex');
    await back('', 'Back after #demo');
  } catch (error) {
    result.failures.push(error.message);
  } finally {
    cdp.listeners.delete(listen);
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
  return result;
}

async function checkNoScript(origin) {
  // Without JavaScript the phone layout must still offer every section link (the Menu stays hidden).
  const result = { width: 390, failures: [] };
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params = {}, milliseconds) => cdp.send(method, params, sessionId, milliseconds);
  let loaded = false;
  const listen = event => { if (event.sessionId === sessionId && event.method === 'Page.loadEventFired') loaded = true; };
  cdp.listeners.add(listen);
  try {
    await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await send('Emulation.setScriptExecutionDisabled', { value: true });
    const navigation = await send('Page.navigate', { url: `${origin}/index.html` }, 30000);
    if (navigation.errorText) throw new Error(navigation.errorText);
    await waitFor(() => loaded, 30000, 'No-JS load');
    const { result: value } = await send('Runtime.evaluate', { returnByValue: true, expression: `(() => {
      const nav = document.getElementById('section-nav');
      return { menuVisible: Boolean(document.getElementById('menu-toggle').getClientRects().length),
        linksVisible: [...nav.querySelectorAll('a')].filter(a => a.getClientRects().length).length,
        controlsVisible: [...document.querySelectorAll('.nav .controls, [data-theme-toggle], [data-lang-toggle]')]
          .filter(element => element.getClientRects().length).length,
        zoomButtonsVisible: [...document.querySelectorAll('[data-zoom]')].filter(button => button.getClientRects().length).length,
        // Every link must be fully on screen and inside the nav's visible box (no scroller clipping).
        navScroll: { scrollWidth: nav.scrollWidth, clientWidth: nav.clientWidth },
        links: [...nav.querySelectorAll('a')].map(a => {
          const box = a.getBoundingClientRect(), frame = nav.getBoundingClientRect();
          return { text: a.textContent.trim(), left: box.left, right: box.right, top: box.top, bottom: box.bottom,
            inside: box.width > 0 && box.height > 0 && box.left >= -1 && box.right <= innerWidth + 1 &&
              box.left >= frame.left - 1 && box.right <= frame.right + 1 && box.top >= frame.top - 1 && box.bottom <= frame.bottom + 1 };
        }),
        notice: [...document.querySelectorAll('.slot-box')].map(box => ({ inDemo: Boolean(box.closest('#demo-root')),
          role: box.getAttribute('role'), text: box.innerText.trim(), ko: box.querySelector('[data-ko]')?.getAttribute('data-ko') || '',
          visible: box.getBoundingClientRect().width > 0 && box.getBoundingClientRect().height > 0 })),
        ...(${pageOverflow.toString()})() };
    })()` });
    Object.assign(result, value.value);
    if (result.menuVisible) result.failures.push('Menu button visible without JavaScript');
    if (result.controlsVisible) result.failures.push('Theme or language toggle visible without JavaScript');
    const [notice] = result.notice;
    if (result.notice.length !== 1 || !notice.inDemo || !notice.visible || notice.role !== 'note' ||
        notice.text !== NO_JS_NOTICE || !HANGUL.test(notice.ko)) {
      result.failures.push(`Expected one visible no-JS demo notice in #demo-root: ${JSON.stringify(result.notice)}`);
    }
    if (result.zoomButtonsVisible) result.failures.push('Zoom controls visible without JavaScript');
    if (result.linksVisible !== 5) result.failures.push(`Expected 5 visible section links without JavaScript, found ${result.linksVisible}`);
    if (result.links.length !== 5 || result.links.some(link => !link.inside) || result.navScroll.scrollWidth > result.navScroll.clientWidth + 1) {
      result.failures.push(`Section links clipped or off screen without JavaScript: ${JSON.stringify({ nav: result.navScroll, links: result.links })}`);
    }
    if (result.overflow) result.failures.push('Horizontal overflow without JavaScript');
  } catch (error) {
    result.failures.push(error.message);
  } finally {
    cdp.listeners.delete(listen);
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
  return result;
}

try {
  await mkdir(output, { recursive: true });
  const origin = await serve();
  await connectChrome();
  for (const [width, height] of [[1440, 900], [390, 844]]) {
    for (const theme of ['light', 'dark']) {
      const result = await checkWidth(origin, width, height, theme);
      result.fallback = await checkVariant(origin, width, height, theme, 'fallback');
      result.reduced = await checkVariant(origin, width, height, theme, 'reduced');
      result.failures.push(...result.fallback.failures.map(message => `Fallback: ${message}`),
        ...result.reduced.failures.map(message => `Reduced motion: ${message}`));
      summary.runs.push(result);
      summary.failures.push(...result.failures.map(message => `${width}px ${theme}: ${message}`));
    }
  }
  // Language matrix: Korean at 1440 light and 390 dark, the Korean fallback and the runtime switch.
  for (const [width, height, theme] of [[1440, 900, 'light'], [390, 844, 'dark']]) {
    const result = await checkWidth(origin, width, height, theme, 'ko');
    summary.runs.push(result);
    summary.failures.push(...result.failures.map(message => `${width}px ${theme} ko: ${message}`));
  }
  summary.koreanFallback = await checkVariant(origin, 1440, 900, 'light', 'fallback', 'ko');
  summary.failures.push(...summary.koreanFallback.failures.map(message => `1440px light ko fallback: ${message}`));
  summary.languageSwitch = await checkVariant(origin, 1440, 900, 'light', 'switch');
  summary.failures.push(...summary.languageSwitch.failures.map(message => `1440px light language switch: ${message}`));
  summary.mastheadRuns = [];
  for (const [width, height] of [[1440, 900], [1024, 768]]) {
    for (const theme of ['light', 'dark']) {
      for (const lang of ['en', 'ko']) {
        const result = await checkVariant(origin, width, height, theme, 'masthead', lang);
        summary.mastheadRuns.push(result);
        summary.failures.push(...result.failures.map(message => `${width}px ${theme} ${lang} masthead: ${message}`));
      }
    }
  }
  summary.menuHistory = await checkMenuHistory(origin);
  summary.failures.push(...summary.menuHistory.failures.map(message => `390px menu history: ${message}`));
  summary.noScript = await checkNoScript(origin);
  summary.failures.push(...summary.noScript.failures.map(message => `390px without JavaScript: ${message}`));
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
summary.i18nAllowed = I18N_ALLOWED;
try {
  await writeFile(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
} catch (error) {
  summary.failures.push(`summary.json: ${error.message}`);
  summary.result = 'FAIL';
}
console.log(JSON.stringify(summary, null, 2));
process.exitCode = summary.failures.length ? 1 : 0;

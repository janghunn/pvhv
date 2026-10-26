#!/usr/bin/env node
// Browser check of the WebGL2 demo (Node 22, Chrome DevTools protocol, no packages).
//   node demo/check_demo.mjs --out <dir> [--gl swiftshader|gpu] [--chrome <path>]
// <dir> must not exist or be empty.  Writes screenshots and summary.json (last) into it,
// prints the summary and exits 1 on any failure (2 on bad arguments / non-empty <dir>).
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import {
  RENDERER_EXPR, Run, launchChrome, openPage, requireEmptyDir, screenshotElement, screenshotPage, serveRepo,
} from './cdp.mjs';

const LABEL = "Re-implementation (2026), not the code used for the paper's experiments";
const NO_WEBGL = 'This interactive demo needs WebGL2 with float render targets (EXT_color_buffer_float), ' +
  'which this browser does not provide.';
const HARNESS = '/demo/harness.html';
const MODES = ['single', 'bdp', 'edp'];

function usage(message) {
  console.error(`${message}\nUsage: node demo/check_demo.mjs --out <dir> [--gl swiftshader|gpu] [--chrome <path>]`);
  process.exit(2);
}

const args = process.argv.slice(2);
let out;
let gl = 'swiftshader';
let chromePath = process.env.CHROME || '/usr/bin/google-chrome';
for (let i = 0; i < args.length; i += 1) {
  if (args[i] === '--out' && args[i + 1]) out = path.resolve(args[++i]);
  else if (args[i] === '--gl' && args[i + 1]) gl = args[++i];
  else if (args[i] === '--chrome' && args[i + 1]) chromePath = args[++i];
  else usage(`Unknown or incomplete argument: ${args[i]}`);
}
if (!out) usage('--out is required');
if (!['swiftshader', 'gpu'].includes(gl)) usage(`--gl must be swiftshader or gpu, not ${gl}`);
if (!(await requireEmptyDir(out))) usage(`${out} exists and is not empty`);
await mkdir(out, { recursive: true });

const run = new Run(600000);
const summary = { ok: false, gl, renderer: null, modes: {}, blocks: {}, interaction: {}, widths: {}, fallback: {},
  labels: {}, failures: [] };
const fail = message => summary.failures.push(message);
const check = (cond, message) => { if (!cond) fail(message); return !!cond; };
const uncaught = error => run.abort(error instanceof Error ? error : new Error(String(error)));
process.once('uncaughtException', uncaught);
process.once('unhandledRejection', uncaught);

async function save(name, png) {
  await writeFile(path.join(out, name), png);
  return name;
}

const key = (page, k, code, vk, extra = {}) => (async () => {
  await page.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk, ...extra });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, ...extra });
})();
const KEYS = {
  Tab: ['Tab', 'Tab', 9], ArrowRight: ['ArrowRight', 'ArrowRight', 39], ArrowLeft: ['ArrowLeft', 'ArrowLeft', 37],
  ArrowUp: ['ArrowUp', 'ArrowUp', 38], ArrowDown: ['ArrowDown', 'ArrowDown', 40], Home: ['Home', 'Home', 36],
};
const press = (page, name) => key(page, ...KEYS[name]);

const state = page => page.evaluate('PVHVDemo.getState()');
const idle = page => page.evaluate('PVHVDemo.whenIdle()', 120000);

async function waitReady(page) {
  await run.waitFor(() => page.evaluate('!!(window.PVHVDemo && PVHVDemo.ready) || !!document.querySelector(".pvhv-demo-fallback")'),
    60000, 'Demo boot');
  const r = await page.evaluate('window.PVHVDemo ? PVHVDemo.ready.then(() => "ok", e => "failed: " + e.message) : ' +
    '"fallback: " + document.querySelector(".pvhv-demo-fallback").innerText', 180000);
  if (r !== 'ok') throw new Error(`PVHVDemo.ready: ${r}`);
}

async function labels(page, scene) {
  const text = await page.evaluate("document.getElementById('demo-root').innerText");
  const res = { label: text.includes(LABEL) };
  check(res.label, `${scene}: label line missing from #demo-root`);
  if (scene === 'chess') {
    res.rileyQueen = text.includes('Riley Queen');
    res.cc0 = text.includes('CC0');
    check(res.rileyQueen && res.cc0, 'chess: attribution (Riley Queen, CC0) missing');
  } else {
    res.procedural = text.includes('Procedural');
    check(res.procedural, 'blocks: attribution (Procedural) missing');
  }
  summary.labels[scene] = res;
}

async function modeShots(page, prefix, record) {
  const s = await state(page);
  const pixels = s.W * s.H;
  await page.evaluate(`PVHVDemo.setView(${0.8 * s.E}, ${0.2 * s.E})`);
  for (const mode of MODES) {
    await page.evaluate(`PVHVDemo.setMode(${JSON.stringify(mode)})`);
    await idle(page);
    const stats = await page.evaluate('PVHVDemo.readCanvasStats()', 60000);
    const shot = await save(`${prefix}${mode}.png`, await screenshotElement(page, '.pvhv-demo'));
    record[mode] = { ...stats, screenshot: shot };
    check(stats.nonBackground >= 0.01 * pixels, `${s.scene}/${mode}: only ${stats.nonBackground} non-background pixels`);
    check(stats.distinctColors >= 16, `${s.scene}/${mode}: only ${stats.distinctColors} distinct colours`);
  }
  check(record.single.holes > record.bdp.holes,
    `${s.scene}: holes(single) ${record.single.holes} not > holes(bdp) ${record.bdp.holes}`);
  record.counts = s.counts;
  record.converged = s.converged;
}

async function widthCheck(page, width) {
  const w = await page.evaluate(`(() => {
    const scrollWidth = document.documentElement.scrollWidth;
    const vw = innerWidth;
    let widest = null;
    if (scrollWidth > vw) {
      const off = [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect();
        return r.right > vw + 1 || r.left < -1;
      }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
      if (off) widest = off.tagName.toLowerCase() + [...off.classList].map(c => '.' + c).join('');
    }
    return { scrollWidth, innerWidth: vw, scrollX, widest };
  })()`);
  summary.widths[width] = w;
  check(w.scrollWidth <= w.innerWidth, `${width}px: horizontal overflow ${w.scrollWidth} > ${w.innerWidth} (${w.widest})`);
  return w;
}

async function canvasCenter(page) {
  return page.evaluate(`(() => {
    const c = document.querySelector('.pvhv-demo-view');
    c.scrollIntoView({ block: 'center' });
    const r = c.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width };
  })()`);
}

async function mouseDrag(page, from, dx, dy, steps = 10) {
  const m = (type, x, y, extra = {}) => page.send('Input.dispatchMouseEvent',
    { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, ...extra });
  await m('mousePressed', from.x, from.y, { clickCount: 1 });
  for (let k = 1; k <= steps; k += 1) await m('mouseMoved', from.x + dx * k / steps, from.y + dy * k / steps);
  await m('mouseReleased', from.x + dx, from.y + dy, { clickCount: 1 });
  await idle(page);
}

async function interaction(page) {
  const r = summary.interaction;
  const s0 = await state(page);
  const E = s0.E;
  const near = (a, b) => Math.abs(a - b) <= 1e-9;
  await page.evaluate("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); true");
  r.tabsToView = null;
  for (let n = 1; n <= 40; n += 1) {
    await press(page, 'Tab');
    if (await page.evaluate("document.activeElement.classList.contains('pvhv-demo-view')")) {
      r.tabsToView = n;
      break;
    }
  }
  if (!check(r.tabsToView, 'view surface not reached within 40 Tab presses')) return;
  let before = (await state(page)).t;
  await press(page, 'ArrowRight');
  await idle(page);
  let after = (await state(page)).t;
  r.arrowRight = { before, after };
  check(near(after[0] - before[0], E / 20) && near(after[1], before[1]), `ArrowRight: t ${before} -> ${after}`);
  for (let n = 0; n < 60; n += 1) await press(page, 'ArrowRight');
  await idle(page);
  after = (await state(page)).t;
  const len = Math.hypot(after[0], after[1]);
  r.arrowRight60 = { t: after, len, E };
  check(len <= E * (1 + 1e-9) && len >= 0.999 * E, `60x ArrowRight: |t| = ${len}, E = ${E}`);
  await press(page, 'Home');
  await idle(page);
  after = (await state(page)).t;
  r.home = after;
  check(near(after[0], 0.8 * E) && near(after[1], 0.2 * E), `Home: t = ${after}`);
  before = after;
  await press(page, 'ArrowUp');
  await idle(page);
  after = (await state(page)).t;
  r.arrowUp = { before, after };
  check(near(after[1] - before[1], E / 20) && near(after[0], before[0]), `ArrowUp: t ${before} -> ${after}`);

  let c = await canvasCenter(page);
  before = (await state(page)).t;
  await mouseDrag(page, c, -3 * c.width, 0);
  after = (await state(page)).t;
  r.dragLeft = { before, after, len: Math.hypot(after[0], after[1]) };
  check(after[0] < before[0] && r.dragLeft.len <= E * (1 + 1e-9), `horizontal drag: t ${before} -> ${after}`);
  check(await page.evaluate("document.activeElement.classList.contains('pvhv-demo-view')"), 'drag did not focus the view');
  await press(page, 'Home');
  await idle(page);
  c = await canvasCenter(page);
  before = (await state(page)).t;
  await mouseDrag(page, c, 0, -0.1 * c.width);
  after = (await state(page)).t;
  r.dragUp = { before, after };
  check(after[1] > before[1], `vertical drag upwards: t ${before} -> ${after}`);

  r.modeRadio = null;
  for (let n = 1; n <= 40; n += 1) {
    await press(page, 'Tab');
    if (await page.evaluate("document.activeElement.matches('.pvhv-demo input[type=radio]')")) {
      r.modeRadio = n;
      break;
    }
  }
  if (!check(r.modeRadio, 'mode radio not reached by Tab')) return;
  const m0 = await state(page);
  await press(page, 'ArrowDown');
  await idle(page);
  const m1 = await state(page);
  r.modeKey = { before: m0.mode, after: m1.mode, framesBefore: m0.frames, framesAfter: m1.frames };
  check(m1.mode !== m0.mode && m1.frames > m0.frames, `mode radio ArrowDown: ${m0.mode} -> ${m1.mode}, frames ${m0.frames} -> ${m1.frames}`);
}

async function mobile(cdp, origin) {
  const page = await openPage(run, cdp, origin, HARNESS, { width: 390, height: 844, dsf: 2, mobile: true, touch: true });
  try {
    await waitReady(page);
    const c = await canvasCenter(page);
    const before = (await state(page)).t;
    const touch = (type, x, y) => page.send('Input.dispatchTouchEvent', { type,
      touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] });
    await touch('touchStart', c.x, c.y);
    for (let k = 1; k <= 10; k += 1) await touch('touchMove', c.x - 0.4 * c.width * k / 10, c.y - 0.1 * c.width * k / 10);
    await touch('touchEnd');
    await idle(page);
    const after = (await state(page)).t;
    const w = await widthCheck(page, 390);
    summary.interaction.touch = { before, after, scrollX: w.scrollX };
    check(after[0] !== before[0] || after[1] !== before[1], `390px touch drag did not change t (${before})`);
    check(w.scrollX === 0, `390px: page scrolled horizontally (scrollX ${w.scrollX})`);
    await page.evaluate('document.activeElement && document.activeElement.blur(); true');
    summary.widths[390].screenshot = await save('page-390.png', await screenshotPage(page));
    summary.pages = summary.pages || {};
    summary.pages[390] = { errors: page.errors, external: page.external };
    check(!page.errors.length, `390px: ${page.errors.length} console/runtime errors: ${page.errors.join(' | ')}`);
    check(!page.external.length, `390px: external requests ${page.external.join(', ')}`);
  } finally {
    await page.close();
  }
}

async function fallbackCheck(cdp, origin, url, name, record) {
  const page = await openPage(run, cdp, origin, url);
  try {
    const box = await run.waitFor(() => page.evaluate(`(() => {
      const f = document.querySelector('.pvhv-demo-fallback');
      if (!f) return null;
      const r = f.getBoundingClientRect();
      return { visible: r.width > 0 && r.height > 0, text: f.innerText, role: f.getAttribute('role') };
    })()`), 60000, `${name} fallback`);
    // settle: let any module/script errors surface before the error count is taken
    await page.evaluate('new Promise(r => setTimeout(() => r(true), 500))');
    const rootText = await page.evaluate("document.getElementById('demo-root').innerText");
    Object.assign(record, box, { label: rootText.includes(LABEL), errors: page.errors, warnings: page.warnings,
      api: await page.evaluate('typeof window.PVHVDemo') });
    check(box.visible && box.text.includes(NO_WEBGL), `${name}: fallback message not shown (${box.text})`);
    check(box.role === 'status', `${name}: fallback has no role=status`);
    check(record.label, `${name}: label missing in fallback`);
    check(!page.errors.length, `${name}: ${page.errors.length} console/runtime errors: ${page.errors.join(' | ')}`);
    check(!page.external.length, `${name}: external requests ${page.external.join(', ')}`);
    record.screenshot = await save(`${name}.png`, await screenshotElement(page, '#demo-root'));
  } finally {
    await page.close();
  }
}

try {
  const origin = await serveRepo(run);
  const { cdp, stderr } = await launchChrome(run, { chromePath, gl });
  summary.chromeStderr = stderr;
  const page = await openPage(run, cdp, origin, HARNESS, { width: 1440, height: 900 });
  try {
    summary.renderer = await page.evaluate(RENDERER_EXPR);
    console.error(`WebGL renderer: ${summary.renderer}`);
    await waitReady(page);
    await modeShots(page, 'mode-', summary.modes);
    console.error(`chess holes: ${MODES.map(m => `${m} ${summary.modes[m].holes}`).join(', ')}`);
    await labels(page, 'chess');
    await page.evaluate("PVHVDemo.setScene('blocks')", 180000);
    await modeShots(page, 'blocks-mode-', summary.blocks);
    console.error(`blocks holes: ${MODES.map(m => `${m} ${summary.blocks[m].holes}`).join(', ')}`);
    await labels(page, 'blocks');
    await page.evaluate("PVHVDemo.setScene('chess')", 180000);
    await interaction(page);
    await widthCheck(page, 1440);
    await page.evaluate('document.activeElement && document.activeElement.blur(); true');
    summary.widths[1440].screenshot = await save('page-1440.png', await screenshotPage(page));
    summary.pages = { 1440: { errors: page.errors, external: page.external } };
    check(!page.errors.length, `1440px: ${page.errors.length} console/runtime errors: ${page.errors.join(' | ')}`);
    check(!page.external.length, `1440px: external requests ${page.external.join(', ')}`);
  } finally {
    await page.close();
  }
  await mobile(cdp, origin);
  summary.fallback.nofloat = {};
  await fallbackCheck(cdp, origin, `${HARNESS}?simulate=no-float-rt`, 'fallback-nofloat', summary.fallback.nofloat);

  const run2 = await launchChrome(run, { chromePath, gl: null, extraFlags: ['--disable-3d-apis'] });
  summary.fallback.nowebgl = {};
  await fallbackCheck(run2.cdp, origin, HARNESS, 'fallback-nowebgl', summary.fallback.nowebgl);
} catch (error) {
  fail(`aborted: ${error.message}`);
} finally {
  for (const e of await run.finish()) fail(`cleanup: ${e}`);
  process.removeListener('uncaughtException', uncaught);
  process.removeListener('unhandledRejection', uncaught);
}
const stderrTail = typeof summary.chromeStderr === 'function' ? summary.chromeStderr() : '';
delete summary.chromeStderr;
if (summary.failures.length && stderrTail) summary.chromeStderrTail = stderrTail.slice(-4000);
summary.ok = summary.failures.length === 0;
await writeFile(path.join(out, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
console.log(JSON.stringify(summary, null, 2));
process.exitCode = summary.ok ? 0 : 1;

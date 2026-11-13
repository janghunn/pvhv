// Interactive demo: UI, view state, scene loading and the window.PVHVDemo test API.
// Booted by ../demo.js through window.__PVHV_DEMO_BOOT__ = {root, container, base, ...}.
import { DemoGL, DemoError, Superseded } from './gl.js';
import { clampT, dragStep, initialT, keyStep, toBase64 } from './math.js';

const SCENES = ['chess', 'blocks'];
const MODES = ['single', 'bdp', 'edp'];
const STRINGS = {
  en: {
    chess: 'Chess set', blocks: 'Blocks',
    single: 'Single layer', bdp: 'Full depth peeling (BDP)', edp: 'EDP (PVHV culling)',
    legend: 'Warping input', scene: 'Scene', reset: 'Reset view', highlight: 'Highlight fragments from hidden layers',
    help: 'Drag the image or use the arrow keys to move the novel viewpoint within the bound; ' +
      'magenta marks disocclusion holes.',
    view: 'Novel view. Drag, or use the arrow keys (Shift for larger steps), to move the viewpoint ' +
      'within the bound; Home or 0 resets the view.',
    loading: () => 'Loading…',
    loadingScene: id => `Loading ${STRINGS.en[id]}…`,
    peeling: (id, method, h) => `Peeling the source view of ${STRINGS.en[id]}: ${method.toUpperCase()} layer ${fmt(h)}…`,
    statistics: (n, L, m, p, holes) => `Source view: BDP keeps ${fmt(n)} hidden fragments in ${fmt(L)} layers; ` +
      `EDP keeps ${fmt(m)} (−${fmt(p)}%). This view: ${fmt(holes)} hole pixels (magenta).`
  },
  ko: {
    chess: '체스 세트', blocks: '블록',
    single: '단일 레이어', bdp: '전체 깊이 필링 (BDP)', edp: 'EDP (PVHV 컬링)',
    legend: '워핑 입력', scene: '장면', reset: '시점 초기화', highlight: '숨은 레이어에서 온 프래그먼트 강조',
    help: '영상을 드래그하거나 화살표 키를 사용해 새 시점 범위 안에서 시점을 이동하세요. 마젠타는 가림 해제로 생긴 구멍을 나타냅니다.',
    view: '새 시점입니다. 드래그하거나 화살표 키(Shift를 누르면 더 큰 간격)로 범위 안에서 시점을 이동하세요. Home 또는 0을 누르면 시점이 초기화됩니다.',
    loading: () => '불러오는 중…',
    loadingScene: id => `${STRINGS.ko[id]} 불러오는 중…`,
    peeling: (id, method, h) => `${STRINGS.ko[id]}의 원본 시점 필링 중: ${method.toUpperCase()} 레이어 ${fmt(h)}…`,
    statistics: (n, L, m, p, holes) => `원본 시점: BDP는 숨은 프래그먼트 ${fmt(n)}개를 ${fmt(L)}개 레이어에 유지하고, ` +
      `EDP는 ${fmt(m)}개를 유지합니다 (−${fmt(p)}%). 이 시점: 구멍 픽셀 ${fmt(holes)}개 (마젠타).`
  }
};
const lang = () => document.documentElement.lang === 'ko' ? 'ko' : 'en';
const fmt = n => n.toLocaleString(lang() === 'ko' ? 'ko-KR' : 'en-US');

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'text') e.textContent = v;
    else if (k === 'className') e.className = v;
    else e.setAttribute(k, v);
  }
  for (const c of children || []) e.appendChild(c);
  return e;
}

// Bound indicator: unit circle = |t| = E, dot = current t (y up).  Static markup, parsed as SVG.
const BOUND_SVG = '<svg class="pvhv-demo-bound" viewBox="-1.25 -1.25 2.5 2.5" aria-hidden="true" focusable="false">' +
  '<line x1="-1" y1="0" x2="1" y2="0"></line><line x1="0" y1="-1" x2="0" y2="1"></line>' +
  '<circle class="ring" cx="0" cy="0" r="1"></circle><circle class="dot" cx="0" cy="0" r="0.16"></circle></svg>';

class Demo {
  constructor(boot) {
    this.boot = boot;
    this.base = boot.base;
    this.state = { scene: null, mode: 'edp', t: [0, 0], highlight: false };
    this.sc = null;
    this.gen = 0;
    this.frames = 0;
    this.dead = false;
    this.pending = 0;
    this.idleWaiters = [];
    this.readyWaiters = [];
    this.sceneGen = 0;
    this.readyPromise = this.waitReady(1);
    this.stats = null;
    this.busyMessage = { key: 'loading', args: [] };
    this.statusMessage = this.busyMessage;
    this.buildUi();
    document.addEventListener('ws:lang', () => this.translate());
  }

  // ---- UI -----------------------------------------------------------------------------

  buildUi() {
    const stage = this.boot.container.querySelector('.pvhv-demo-stage');
    while (stage.firstChild) stage.removeChild(stage.firstChild);
    this.canvas = el('canvas', { className: 'pvhv-demo-view', width: '640', height: '360', tabindex: '0',
      role: 'img', 'aria-label': STRINGS[lang()].view });
    this.busy = el('div', { className: 'pvhv-demo-busy', text: STRINGS[lang()].loading() });
    const wrap = el('div', { className: 'pvhv-demo-viewwrap' }, [this.canvas]);
    wrap.insertAdjacentHTML('beforeend', BOUND_SVG);
    wrap.appendChild(this.busy);
    this.dot = wrap.querySelector('circle.dot');

    this.legend = el('legend');
    const fieldset = el('fieldset', { className: 'pvhv-demo-modes' }, [this.legend]);
    const name = `pvhv-demo-mode-${Math.random().toString(36).slice(2, 8)}`;
    this.radios = {};
    this.modeLabels = {};
    for (const id of MODES) {
      const input = el('input', { type: 'radio', name, value: id });
      input.checked = id === this.state.mode;
      input.addEventListener('change', () => { if (input.checked) this.setMode(id); });
      this.radios[id] = input;
      this.modeLabels[id] = document.createTextNode('');
      fieldset.appendChild(el('label', {}, [input, this.modeLabels[id]]));
    }
    this.select = el('select', {});
    for (const id of SCENES) this.select.appendChild(el('option', { value: id }));
    this.select.addEventListener('change', () => { this.setScene(this.select.value).catch(() => {}); });
    this.reset = el('button', { type: 'button' });
    this.reset.addEventListener('click', () => this.resetView());
    this.check = el('input', { type: 'checkbox' });
    this.check.addEventListener('change', () => this.setHighlight(this.check.checked));
    this.sceneLabel = document.createTextNode('');
    this.highlightLabel = document.createTextNode('');
    const controls = el('div', { className: 'pvhv-demo-controls' }, [
      fieldset,
      el('label', {}, [this.sceneLabel, this.select]),
      this.reset,
      el('label', {}, [this.check, this.highlightLabel]),
    ]);
    this.status = el('p', { className: 'pvhv-demo-status', 'aria-live': 'polite', text: STRINGS[lang()].loading() });
    stage.appendChild(wrap);
    this.help = el('p', { className: 'pvhv-demo-help' });
    stage.appendChild(this.help);
    stage.appendChild(controls);
    stage.appendChild(this.status);
    this.translate();
    this.bindInput();
  }

  bindInput() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('pointerdown', e => {
      if (e.button !== 0 || !this.sc) return;
      drag = { id: e.pointerId, x: e.clientX, y: e.clientY };
      c.setPointerCapture(e.pointerId);
      c.classList.add('pvhv-demo-dragging');
      c.focus({ preventScroll: true });
      e.preventDefault();
    });
    c.addEventListener('pointermove', e => {
      if (!drag || e.pointerId !== drag.id || !this.sc) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      drag.x = e.clientX;
      drag.y = e.clientY;
      const w = c.getBoundingClientRect().width || 1;
      this.applyView(dragStep(this.state.t, dx, dy, w, this.sc.data.E));
    });
    const end = e => {
      if (!drag || e.pointerId !== drag.id) return;
      drag = null;
      c.classList.remove('pvhv-demo-dragging');
      if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId);
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('keydown', e => {
      if (!this.sc || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === 'Home' || e.key === '0') {
        e.preventDefault();
        this.resetView();
        return;
      }
      const t = keyStep(this.state.t, e.key, e.shiftKey, this.sc.data.E);
      if (!t) return;
      e.preventDefault();
      this.applyView(t);
    });
    c.addEventListener('webglcontextlost', e => {
      e.preventDefault();
      this.fail(new DemoError('the WebGL context was lost'));
    });
  }

  renderMessage(message) {
    return STRINGS[lang()][message.key](...message.args);
  }

  translate() {
    const text = STRINGS[lang()];
    this.legend.textContent = text.legend;
    for (const id of MODES) this.modeLabels[id].textContent = text[id];
    for (const option of this.select.options) option.textContent = text[option.value];
    this.sceneLabel.textContent = text.scene;
    this.reset.textContent = text.reset;
    this.highlightLabel.textContent = text.highlight;
    this.help.textContent = text.help;
    this.canvas.setAttribute('aria-label', text.view);
    if (this.busyMessage) this.busy.textContent = this.renderMessage(this.busyMessage);
    if (this.statusMessage) this.status.textContent = this.renderMessage(this.statusMessage);
  }

  setBusy(key, ...args) {
    this.busyMessage = key ? { key, args } : null;
    this.busy.hidden = !key;
    if (key) {
      this.statusMessage = this.busyMessage;
      this.busy.textContent = this.renderMessage(this.busyMessage);
      this.status.textContent = this.busy.textContent;
    }
  }

  updateDot() {
    const E = this.sc ? this.sc.data.E : 1;
    this.dot.setAttribute('cx', String(this.state.t[0] / E));
    this.dot.setAttribute('cy', String(-this.state.t[1] / E));
  }

  updateStatus() {
    const sc = this.sc;
    if (!sc || !this.stats || !this.busy.hidden) return;
    const sum = a => a.reduce((s, l) => s + l.count, 0);
    const n = sum(sc.layers.bdp);
    const m = sum(sc.layers.edp);
    const p = n > 0 ? Math.round(100 * (1 - m / n)) : 0;
    this.statusMessage = { key: 'statistics', args: [n, sc.layers.bdp.length, m, p, this.stats.holes] };
    this.status.textContent = this.renderMessage(this.statusMessage);
  }

  // ---- state ----------------------------------------------------------------------------

  applyView(t) {
    if (!this.sc) return this.state.t.slice();
    this.state.t = clampT(t, this.sc.data.E);
    this.updateDot();
    this.requestRender();
    return this.state.t.slice();
  }

  resetView() {
    if (this.sc) this.applyView(initialT(this.sc.data.E));
  }

  setMode(m) {
    if (!MODES.includes(m)) throw new Error(`unknown mode ${m}`);
    this.state.mode = m;
    if (!this.radios[m].checked) this.radios[m].checked = true;
    this.requestRender();
    return m;
  }

  setHighlight(on) {
    this.state.highlight = !!on;
    this.check.checked = this.state.highlight;
    this.requestRender();
    return this.state.highlight;
  }

  requestRender() {
    if (this.pending || this.dead || !this.sc) return;
    this.pending = requestAnimationFrame(() => {
      this.pending = 0;
      this.renderNow();
    });
  }

  renderNow() {
    if (this.dead || !this.sc) return;
    if (this.gpu.gl.isContextLost()) {         // lost before its webglcontextlost event: no frame
      this.fail(new DemoError('the WebGL context was lost'));
      return;
    }
    try {
      const s = this.state;
      this.gpu.frame(this.sc, s.mode, s.t, s.highlight);
      this.stats = this.gpu.displayStats(this.sc);
    } catch (error) {
      this.fail(error);
      return;
    }
    this.frames += 1;
    this.updateStatus();
    this.readyWaiters = this.readyWaiters.filter(w => {
      if (w.gen > this.sceneGen) return true;
      w.resolve(true);
      return false;
    });
    if (!this.pending) for (const w of this.idleWaiters.splice(0)) w.resolve(this.frames);
  }

  whenIdle() {
    if (this.dead) return Promise.reject(new Error('the demo stopped'));
    if (!this.pending) return Promise.resolve(this.frames);
    return new Promise((resolve, reject) => this.idleWaiters.push({ resolve, reject }));
  }

  fail(error) {
    if (this.dead) return;
    this.dead = true;
    if (this.pending) cancelAnimationFrame(this.pending);
    this.pending = 0;
    const reason = error instanceof DemoError ? error.message : `unexpected error (${error && error.message})`;
    for (const w of this.readyWaiters.splice(0)) w.reject(new Error(reason));
    for (const w of this.idleWaiters.splice(0)) w.reject(new Error(reason));
    try {
      if (this.gpu && !this.gpu.gl.isContextLost()) {
        this.gpu.disposeScene(this.sc);
        this.gpu.dispose();
      }
    } catch (e) {
      // the context is unusable; nothing left to free
    }
    this.sc = null;
    this.boot.fallback(reason);
  }

  /** Promise resolved by the first frame of the scene requested as generation `gen` (or a later one). */
  waitReady(gen) {
    const p = new Promise((resolve, reject) => this.readyWaiters.push({ gen, resolve, reject }));
    p.catch(() => {});
    return p;
  }

  // ---- scenes ---------------------------------------------------------------------------

  async fetchOk(url, kind) {
    const res = await fetch(url);
    if (!res.ok) throw new DemoError(`could not load ${url.pathname.split('/').slice(-2).join('/')} (${res.status})`);
    return kind === 'json' ? res.json() : res.arrayBuffer();
  }

  async loadImage(url) {
    const img = new Image();
    img.decoding = 'async';
    img.src = url.href;
    try {
      await img.decode();
    } catch (e) {
      throw new DemoError(`could not decode ${url.pathname.split('/').pop()}`);
    }
    return img;
  }

  async loadScene(id, cancelled) {
    const dir = new URL(`../demo/${id}/`, this.base);
    this.setBusy('loadingScene', id);
    const data = await this.fetchOk(new URL('scene.json', dir), 'json');
    if (cancelled()) throw new Superseded();
    const [geometry, images] = await Promise.all([
      this.fetchOk(new URL('geometry.bin', dir), 'bin'),
      Promise.all(data.textures.map(t => this.loadImage(new URL(t.file, dir)))),
    ]);
    if (cancelled()) throw new Superseded();
    if (geometry.byteLength !== data.geometry_bytes) throw new DemoError(`${id}/geometry.bin has the wrong size`);
    const byFile = {};
    data.textures.forEach((t, k) => { byFile[t.file] = images[k]; });
    const sc = this.gpu.createScene(data, geometry, byFile);
    try {
      await this.gpu.peelScene(sc, cancelled, (method, h) => {
        if (!cancelled()) this.setBusy('peeling', id, method, h);
      });
      if (cancelled()) throw new Superseded();
    } catch (error) {
      this.gpu.disposeScene(sc);
      throw error;
    }
    return sc;
  }

  /** Load scene `id`; a later call supersedes an earlier one.  Resolves after its first frame. */
  setScene(id) {
    if (!SCENES.includes(id)) return Promise.reject(new Error(`unknown scene ${id}`));
    if (this.dead) return Promise.reject(new Error('the demo stopped'));
    const gen = ++this.gen;
    const done = gen === 1 ? this.readyPromise : this.waitReady(gen);
    this.readyPromise = done;
    const cancelled = () => gen !== this.gen || this.dead;
    this.loadScene(id, cancelled).then(sc => {
      if (cancelled()) {
        this.gpu.disposeScene(sc);
        return;
      }
      const old = this.sc;
      this.sc = sc;
      this.sceneGen = gen;
      this.state.scene = id;
      this.state.t = initialT(sc.data.E);
      this.select.value = id;
      this.boot.setAttribution(sc.data);
      this.gpu.disposeScene(old);
      this.setBusy(null);
      this.updateDot();
      this.requestRender();
    }, error => {
      if (error instanceof Superseded || cancelled()) return;
      this.fail(error);
    });
    return done;
  }

  // ---- test API ---------------------------------------------------------------------------

  requireScene() {
    if (!this.sc) throw new Error('no scene is loaded');
    return this.sc;
  }

  getState() {
    const sc = this.sc;
    const d = sc ? sc.data : {};
    const counts = m => (sc ? [sc.layer0.count, ...sc.layers[m].map(l => l.count)] : []);
    return {
      scene: this.state.scene, mode: this.state.mode, t: this.state.t.slice(), E: d.E, W: d.W, H: d.H,
      Wg: d.Wg, Hg: d.Hg, counts: { bdp: counts('bdp'), edp: counts('edp') },
      converged: sc ? { ...sc.converged } : { bdp: false, edp: false },
      highlight: this.state.highlight, frames: this.frames, loading: !this.busy.hidden, failed: this.dead,
    };
  }

  readLayers(method) {
    const sc = this.requireScene();
    if (method !== 'bdp' && method !== 'edp') throw new Error(`unknown method ${method}`);
    const { Wg, Hg } = sc.data;
    return this.gpu.modeLayers(sc, method).map(l => ({
      count: l.count,
      item: toBase64(this.gpu.readTexture(l.item, Wg, Hg, 'u32')),
      depth: toBase64(this.gpu.readTexture(l.depth, Wg, Hg, 'f32')),
      color: toBase64(this.gpu.readTexture(l.color, Wg, Hg, 'rgba8')),
    }));
  }

  readWarp(mode, tx, ty) {
    const sc = this.requireScene();
    if (!MODES.includes(mode)) throw new Error(`unknown mode ${mode}`);
    const { W, H } = sc.data;
    this.gpu.warp(sc, mode, [tx, ty]);
    const tex = sc.warp.tex;
    return {
      item: toBase64(this.gpu.readTexture(tex[2], W, H, 'u32')),
      depth: toBase64(this.gpu.readTexture(tex[1], W, H, 'f32')),
      layer: toBase64(this.gpu.readTexture(tex[3], W, H, 'u32')),
    };
  }

  readCanvasStats() {
    const s = this.gpu.displayStats(this.requireScene());
    return { nonBackground: s.nonBackground, holes: s.holes, distinctColors: s.distinctColors };
  }

  start() {
    const gl = this.canvas.getContext('webgl2', { antialias: false, depth: false, stencil: false, alpha: false,
      premultipliedAlpha: false, preserveDrawingBuffer: false });
    if (!gl) throw new DemoError('WebGL2 is not available');
    this.gpu = new DemoGL(gl);
    return this.setScene('chess');
  }
}

function boot() {
  const b = window.__PVHV_DEMO_BOOT__;
  if (!b || !b.container) return;
  const demo = new Demo(b);
  window.PVHVDemo = {
    get ready() { return demo.readyPromise; },
    getState: () => demo.getState(),
    setMode: m => demo.setMode(m),
    setView: (tx, ty) => demo.applyView([Number(tx), Number(ty)]),
    setScene: id => demo.setScene(id),
    setHighlight: on => demo.setHighlight(on),
    whenIdle: () => demo.whenIdle(),
    readLayers: method => demo.readLayers(method),
    readWarp: (mode, tx, ty) => demo.readWarp(mode, Number(tx), Number(ty)),
    readCanvasStats: () => demo.readCanvasStats(),
  };
  try {
    demo.start();
  } catch (error) {
    demo.fail(error);
  }
}

boot();

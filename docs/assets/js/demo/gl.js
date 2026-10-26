// WebGL2 port of the offline renderer's passes (renderer/pvhv/peel.py, warp.py):
// geometry/peeling passes into MRT targets, forward warping of the kept layers, the
// direct reference render and the demo's display composite.  Arrays read back from
// the GPU are bottom row first, like the offline renderer's.
import * as SH from './shaders.js';
import { viewMatrix } from './math.js';

export const MODE_NONE = 0;
export const MODE_BDP = 1;
export const MODE_UDP = 2;
export const MODE_EDP = 3;
export const MAX_HIDDEN = 32;           // hidden layers kept per method (probe at 33)
const N_PATTERN = 64;
const MAX_EXHAUSTIVE_RADIUS = 96.0;
const EDP_LAYERS = 2;                   // paper configuration: EDP on hidden layers 1-2, UDP after

export class DemoError extends Error {}
export class Superseded extends Error {}

/** Records every GPU object it creates so a scene can be deleted as a whole. */
class Tracker {
  constructor(gl) {
    this.gl = gl;
    this.items = [];
  }
  add(kind, obj) {
    if (!obj) throw new DemoError(`could not create ${kind}`);
    this.items.push([kind, obj]);
    return obj;
  }
  remove(obj) {
    const k = this.items.findIndex(e => e[1] === obj);
    if (k >= 0) {
      this.free(this.items[k]);
      this.items.splice(k, 1);
    }
  }
  free([kind, obj]) {
    const gl = this.gl;
    if (kind === 'buffer') gl.deleteBuffer(obj);
    else if (kind === 'vao') gl.deleteVertexArray(obj);
    else if (kind === 'texture') gl.deleteTexture(obj);
    else if (kind === 'framebuffer') gl.deleteFramebuffer(obj);
    else if (kind === 'renderbuffer') gl.deleteRenderbuffer(obj);
  }
  dispose() {
    for (const e of this.items.reverse()) this.free(e);
    this.items = [];
  }
}

function compile(gl, type, source, name) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS) && !gl.isContextLost()) {
    const log = gl.getShaderInfoLog(s);
    gl.deleteShader(s);
    throw new DemoError(`shader ${name} does not compile: ${log}`);
  }
  return s;
}

function program(gl, vs, fs, name) {
  const p = gl.createProgram();
  const v = compile(gl, gl.VERTEX_SHADER, vs, `${name}.vert`);
  const f = compile(gl, gl.FRAGMENT_SHADER, fs, `${name}.frag`);
  gl.attachShader(p, v);
  gl.attachShader(p, f);
  gl.linkProgram(p);
  gl.deleteShader(v);
  gl.deleteShader(f);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS) && !gl.isContextLost()) {
    throw new DemoError(`program ${name} does not link: ${gl.getProgramInfoLog(p)}`);
  }
  const loc = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS) || 0;
  for (let i = 0; i < n; i += 1) {
    const info = gl.getActiveUniform(p, i);
    const key = info.name.replace(/\[0\]$/, '');
    loc[key] = gl.getUniformLocation(p, info.name);
  }
  return { p, loc };
}

const nextTask = () => new Promise(resolve => setTimeout(resolve, 0));

export class DemoGL {
  /** Programs and shared objects are created once per context. */
  constructor(gl) {
    this.gl = gl;
    if (!gl.getExtension('EXT_color_buffer_float')) {
      throw new DemoError('float render targets (EXT_color_buffer_float) are not available');
    }
    this.aniso = gl.getExtension('EXT_texture_filter_anisotropic');
    this.geo = program(gl, SH.GEOMETRY_VERT, SH.PEEL_FRAG, 'geometry/peel');
    this.warpProg = program(gl, SH.WARP_VERT, SH.WARP_FRAG, 'warp');
    this.comp = program(gl, SH.COMPOSITE_VERT, SH.COMPOSITE_FRAG, 'composite');
    this.shared = new Tracker(gl);
    this.emptyVao = this.shared.add('vao', gl.createVertexArray());
    this.dummy = {
      f: this.texture(this.shared, 1, 1, gl.R32F),
      u: this.texture(this.shared, 1, 1, gl.R32UI),
      rgba: this.texture(this.shared, 1, 1, gl.RGBA8),
    };
    this.readFbo = this.shared.add('framebuffer', gl.createFramebuffer());
  }

  dispose() {
    const gl = this.gl;
    this.shared.dispose();
    for (const pr of [this.geo, this.warpProg, this.comp]) gl.deleteProgram(pr.p);
  }

  /** Immutable single-level texture with NEAREST sampling (render targets, dummies). */
  texture(track, w, h, internalFormat) {
    const gl = this.gl;
    const t = track.add('texture', gl.createTexture());
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texStorage2D(gl.TEXTURE_2D, 1, internalFormat, w, h);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindTexture(gl.TEXTURE_2D, null);
    return t;
  }

  depthBuffer(track, w, h) {
    const gl = this.gl;
    const rb = track.add('renderbuffer', gl.createRenderbuffer());
    gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT32F, w, h);
    gl.bindRenderbuffer(gl.RENDERBUFFER, null);
    return rb;
  }

  /** Attach colour textures (+ optional depth renderbuffer) to fbo and check completeness. */
  attach(fbo, colors, depthRb) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    colors.forEach((t, i) => gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + i, gl.TEXTURE_2D, t, 0));
    if (depthRb !== undefined) gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depthRb);
    gl.drawBuffers(colors.map((_, i) => gl.COLOR_ATTACHMENT0 + i));
    const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    if (status !== gl.FRAMEBUFFER_COMPLETE && !gl.isContextLost()) {
      throw new DemoError(`framebuffer incomplete (0x${status.toString(16)})`);
    }
  }

  /** MRT target like peel.py's _Target / warp.py's output: RGBA8, R32F, R32UI, R32UI + DEPTH32F. */
  mrtTarget(track, w, h) {
    const gl = this.gl;
    const tex = [this.texture(track, w, h, gl.RGBA8), this.texture(track, w, h, gl.R32F),
      this.texture(track, w, h, gl.R32UI), this.texture(track, w, h, gl.R32UI)];
    const zbuf = this.depthBuffer(track, w, h);
    const fbo = track.add('framebuffer', gl.createFramebuffer());
    this.attach(fbo, tex, zbuf);
    return { w, h, fbo, tex, zbuf };
  }

  clearMrt(n) {
    const gl = this.gl;
    gl.clearBufferfv(gl.COLOR, 0, [0, 0, 0, 0]);
    gl.clearBufferfv(gl.COLOR, 1, [0, 0, 0, 0]);
    gl.clearBufferuiv(gl.COLOR, 2, [0, 0, 0, 0]);
    if (n > 3) gl.clearBufferuiv(gl.COLOR, 3, [0, 0, 0, 0]);
    gl.clearBufferfv(gl.DEPTH, 0, [1]);
  }

  // ---- scene upload ----------------------------------------------------------------

  /** Upload one exported scene: geometry (ArrayBuffer), decoded texture images by file name. */
  createScene(data, geometry, images) {
    const gl = this.gl;
    const track = new Tracker(gl);
    try {
      const textures = {};
      for (const t of data.textures) {
        const tex = track.add('texture', gl.createTexture());
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);                 // top row first -> GL bottom row first
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.SRGB8_ALPHA8, gl.RGBA, gl.UNSIGNED_BYTE, images[t.file]);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        if (this.aniso) {
          const max = gl.getParameter(this.aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT);
          gl.texParameterf(gl.TEXTURE_2D, this.aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, max));
        }
        textures[t.file] = tex;
      }
      gl.bindTexture(gl.TEXTURE_2D, null);
      const batches = data.batches.map(b => {
        const vao = track.add('vao', gl.createVertexArray());
        gl.bindVertexArray(vao);
        const attrs = [['positions', 3], ['normals', 3], ['uvs', 2]];
        attrs.forEach(([key, size], loc) => {
          const buf = track.add('buffer', gl.createBuffer());
          gl.bindBuffer(gl.ARRAY_BUFFER, buf);
          gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(geometry, b.offsets[key], b.vertex_count * size), gl.STATIC_DRAW);
          gl.enableVertexAttribArray(loc);
          gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
        });
        const ibo = track.add('buffer', gl.createBuffer());
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
        const Ctor = b.index_type === 'uint16' ? Uint16Array : Uint32Array;
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Ctor(geometry, b.offsets.indices, b.index_count), gl.STATIC_DRAW);
        gl.bindVertexArray(null);
        // in_item (location 3) stays a disabled array: every vertex of a batch has the same
        // item, supplied as the constant generic attribute value at draw time.
        return {
          vao, item: b.item, count: b.index_count,
          type: b.index_type === 'uint16' ? gl.UNSIGNED_SHORT : gl.UNSIGNED_INT,
          model: new Float32Array(b.model), normalMatrix: new Float32Array(b.normal_matrix),
          baseColor: new Float32Array(b.base_color), texture: b.texture ? textures[b.texture] : null,
          alphaTest: !!b.alpha_test,
        };
      });
      gl.bindBuffer(gl.ARRAY_BUFFER, null);
      const probes = new Float32Array(2 * N_PATTERN);
      probes.set(data.probes);
      const sc = {
        data, track, batches, probes,
        view0: new Float32Array(data.view0),
        projSrc: new Float32Array(data.proj_src),
        projRef: new Float32Array(data.proj_ref),
        layer0: null, layers: { bdp: [], edp: [] }, converged: { bdp: false, edp: false },
      };
      sc.warp = this.mrtTarget(track, data.W, data.H);
      sc.ref = this.mrtTarget(track, data.W, data.H);
      const display = this.texture(track, data.W, data.H, gl.RGBA8);
      const dfbo = track.add('framebuffer', gl.createFramebuffer());
      this.attach(dfbo, [display]);
      sc.display = { w: data.W, h: data.H, fbo: dfbo, tex: [display] };
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      return sc;
    } catch (error) {
      track.dispose();
      throw error;
    }
  }

  disposeScene(sc) {
    if (sc) sc.track.dispose();
  }

  // ---- geometry / peeling pass (peel.py render_pass) -------------------------------

  geometryPass(sc, fbo, w, h, view, proj, mode, prev) {
    const gl = this.gl;
    const d = sc.data;
    const { p, loc } = this.geo;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, w, h);
    gl.disable(gl.BLEND);
    gl.depthMask(true);
    this.clearMrt(4);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LESS);
    if (d.cull_backfaces) {
      gl.enable(gl.CULL_FACE);
      gl.frontFace(gl.CCW);
      gl.cullFace(gl.BACK);
    } else {
      gl.disable(gl.CULL_FACE);
    }
    gl.useProgram(p);
    gl.uniformMatrix4fv(loc.view, false, view);
    gl.uniformMatrix4fv(loc.proj, false, proj);
    gl.uniform1i(loc.mode, mode);
    // texture units: 0 base, 1 alpha, 2 prevDepth, 3 prevItem
    gl.uniform1i(loc.baseTex, 0);
    gl.uniform1i(loc.alphaTex, 1);
    gl.uniform1i(loc.prevDepth, 2);
    gl.uniform1i(loc.prevItem, 3);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, prev ? prev.depth : this.dummy.f);
    gl.activeTexture(gl.TEXTURE3);
    gl.bindTexture(gl.TEXTURE_2D, prev ? prev.item : this.dummy.u);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.dummy.rgba);
    gl.uniform2i(loc.srcSize, w, h);
    gl.uniform1f(loc.eps, d.eps);
    gl.uniform1f(loc.delta, d.delta);
    gl.uniform1f(loc.E, d.E);
    gl.uniform1f(loc.F, d.F);
    gl.uniform1f(loc.near, d.near);
    gl.uniform1f(loc.far, d.far);
    gl.uniform1f(loc.margin, d.margin);
    gl.uniform1i(loc.nSamples, d.nSamples);
    gl.uniform1i(loc.searchMode, 0);
    gl.uniform1f(loc.maxExhaustiveRadius, MAX_EXHAUSTIVE_RADIUS);
    if (loc.samples) gl.uniform2fv(loc.samples, sc.probes);
    gl.activeTexture(gl.TEXTURE0);
    for (const b of sc.batches) {
      gl.uniformMatrix4fv(loc.model, false, b.model);
      if (loc.normalMatrix) gl.uniformMatrix3fv(loc.normalMatrix, false, b.normalMatrix);
      gl.uniform3fv(loc.baseColor, b.baseColor);
      gl.bindTexture(gl.TEXTURE_2D, b.texture || this.dummy.rgba);
      gl.uniform1i(loc.hasTexture, b.texture ? 1 : 0);
      gl.uniform1i(loc.hasAlphaTex, 0);
      gl.uniform1i(loc.alphaTest, b.alphaTest ? 1 : 0);
      gl.vertexAttribI4ui(3, b.item, 0, 0, 0);
      gl.bindVertexArray(b.vao);
      gl.drawElements(gl.TRIANGLES, b.count, b.type, 0);
    }
    gl.bindVertexArray(null);
    for (const unit of [3, 2, 1, 0]) {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, null);
    }
    gl.disable(gl.CULL_FACE);
    gl.disable(gl.DEPTH_TEST);
  }

  /** Read one channel of a texture: kind 'u32' (R32UI), 'f32' (R32F) or 'rgba8'. Bottom row first. */
  readTexture(tex, w, h, kind) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.readFbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.readBuffer(gl.COLOR_ATTACHMENT0);
    let out;
    if (kind === 'rgba8') {
      out = new Uint8Array(4 * w * h);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, out);
    } else {
      const u = kind === 'u32';
      const buf = u ? new Uint32Array(4 * w * h) : new Float32Array(4 * w * h);
      gl.readPixels(0, 0, w, h, u ? gl.RGBA_INTEGER : gl.RGBA, u ? gl.UNSIGNED_INT : gl.FLOAT, buf);
      out = u ? new Uint32Array(w * h) : new Float32Array(w * h);
      for (let i = 0; i < out.length; i += 1) out[i] = buf[4 * i];
    }
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, null, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    return out;
  }

  /** Render one source-view layer into fresh textures; returns {color, depth, item, count}. */
  peelLayer(sc, peel, mode, prev) {
    const gl = this.gl;
    const { Wg, Hg } = sc.data;
    const layer = {
      color: this.texture(sc.track, Wg, Hg, gl.RGBA8),
      depth: this.texture(sc.track, Wg, Hg, gl.R32F),
      item: this.texture(sc.track, Wg, Hg, gl.R32UI),
    };
    this.attach(peel.fbo, [layer.color, layer.depth, layer.item, peel.flag], peel.zbuf);
    this.geometryPass(sc, peel.fbo, Wg, Hg, sc.view0, sc.projSrc, mode, prev);
    const item = this.readTexture(layer.item, Wg, Hg, 'u32');
    let count = 0;
    for (let i = 0; i < item.length; i += 1) if (item[i] !== 0) count += 1;
    layer.count = count;
    return layer;
  }

  /** Peel layer 0, BDP to convergence and EDP (paper configuration); yields between passes. */
  async peelScene(sc, isCancelled, progress) {
    const gl = this.gl;
    const { Wg, Hg } = sc.data;
    const peel = {
      fbo: sc.track.add('framebuffer', gl.createFramebuffer()),
      flag: this.texture(sc.track, Wg, Hg, gl.R32UI),
      zbuf: this.depthBuffer(sc.track, Wg, Hg),
    };
    const check = async () => {
      await nextTask();
      if (isCancelled()) throw new Superseded();
      if (gl.isContextLost()) throw new DemoError('the WebGL context was lost');
    };
    sc.layer0 = this.peelLayer(sc, peel, MODE_NONE, null);
    for (const method of ['bdp', 'edp']) {
      let prev = sc.layer0;
      let converged = false;
      for (let h = 1; h <= MAX_HIDDEN + 1; h += 1) {
        await check();
        if (progress) progress(method, h);
        const mode = method === 'bdp' ? MODE_BDP : (h <= EDP_LAYERS ? MODE_EDP : MODE_UDP);
        const layer = this.peelLayer(sc, peel, mode, prev);
        if (layer.count === 0 || h === MAX_HIDDEN + 1) {
          converged = layer.count === 0;
          for (const t of [layer.color, layer.depth, layer.item]) sc.track.remove(t);
          break;
        }
        sc.layers[method].push(layer);
        prev = layer;
      }
      sc.converged[method] = converged;
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, peel.fbo);
    for (let i = 0; i < 4; i += 1) gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + i, gl.TEXTURE_2D, null, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    sc.track.remove(peel.fbo);
    sc.track.remove(peel.flag);
    sc.track.remove(peel.zbuf);
  }

  /** Layers warped by mode: single = layer 0; bdp/edp = layer 0 + that method's hidden layers. */
  modeLayers(sc, mode) {
    if (mode === 'single') return [sc.layer0];
    return [sc.layer0, ...sc.layers[mode]];
  }

  // ---- forward warping (warp.py) ----------------------------------------------------

  warp(sc, mode, t) {
    const gl = this.gl;
    const d = sc.data;
    const { p, loc } = this.warpProg;
    const tgt = sc.warp;
    gl.bindFramebuffer(gl.FRAMEBUFFER, tgt.fbo);
    gl.viewport(0, 0, tgt.w, tgt.h);
    gl.disable(gl.BLEND);
    gl.disable(gl.CULL_FACE);
    gl.depthMask(true);
    this.clearMrt(4);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LESS);
    gl.useProgram(p);
    gl.uniform1i(loc.layerColor, 0);
    gl.uniform1i(loc.layerDepth, 1);
    gl.uniform1i(loc.layerItem, 2);
    gl.uniform2i(loc.srcSize, d.Wg, d.Hg);
    gl.uniform2f(loc.dstSize, d.W, d.H);
    gl.uniform2f(loc.t, t[0], t[1]);
    gl.uniform1f(loc.F, d.F);
    gl.uniform1f(loc.near, d.near);
    gl.uniform1f(loc.far, d.far);
    gl.bindVertexArray(this.emptyVao);
    this.modeLayers(sc, mode).forEach((layer, h) => {
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, layer.color);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, layer.depth);
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, layer.item);
      gl.uniform1ui(loc.layerIndex, h);
      gl.drawArraysInstanced(gl.POINTS, 0, d.Wg * d.Hg, 4);
    });
    gl.bindVertexArray(null);
    for (const unit of [2, 1, 0]) {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, null);
    }
    gl.disable(gl.DEPTH_TEST);
  }

  /** Direct render of the novel view at t (W x H, no guard band), mode 0. */
  reference(sc, t) {
    const d = sc.data;
    this.geometryPass(sc, sc.ref.fbo, d.W, d.H, viewMatrix(d.basis, d.eye, t), sc.projRef, MODE_NONE, null);
  }

  /** Display pass into the RGBA8 display framebuffer, then a blit to the canvas. */
  composite(sc, highlight) {
    const gl = this.gl;
    const d = sc.data;
    const { p, loc } = this.comp;
    gl.bindFramebuffer(gl.FRAMEBUFFER, sc.display.fbo);
    gl.viewport(0, 0, d.W, d.H);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.BLEND);
    gl.useProgram(p);
    gl.uniform1i(loc.warpColor, 0);
    gl.uniform1i(loc.warpLayer, 1);
    gl.uniform1i(loc.refItem, 2);
    gl.uniform3f(loc.background, d.background_srgb8[0] / 255, d.background_srgb8[1] / 255, d.background_srgb8[2] / 255);
    gl.uniform1i(loc.highlight, highlight ? 1 : 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, sc.warp.tex[0]);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, sc.warp.tex[3]);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, sc.ref.tex[2]);
    gl.bindVertexArray(this.emptyVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindVertexArray(null);
    for (const unit of [2, 1, 0]) {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, null);
    }
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, sc.display.fbo);
    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, null);
    gl.blitFramebuffer(0, 0, d.W, d.H, 0, 0, d.W, d.H, gl.COLOR_BUFFER_BIT, gl.NEAREST);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  /** Full frame: warp the mode's layers at t, reference render at t, composite + present. */
  frame(sc, mode, t, highlight) {
    this.warp(sc, mode, t);
    this.reference(sc, t);
    this.composite(sc, highlight);
  }

  /** {nonBackground, holes, distinctColors} of the display framebuffer. */
  displayStats(sc) {
    const d = sc.data;
    const px = this.readTexture(sc.display.tex[0], d.W, d.H, 'rgba8');
    const [br, bg, bb] = d.background_srgb8;
    let nonBackground = 0;
    let holes = 0;
    const colors = new Set();
    for (let i = 0; i < px.length; i += 4) {
      const r = px[i], g = px[i + 1], b = px[i + 2];
      if (r !== br || g !== bg || b !== bb) nonBackground += 1;
      if (r === 255 && g === 0 && b === 255) holes += 1;
      colors.add((r << 16) | (g << 8) | b);
    }
    return { nonBackground, holes, distinctColors: colors.size, pixels: d.W * d.H };
  }
}

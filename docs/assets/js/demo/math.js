// Camera, bound and hash math of the demo (no DOM; importable by Node for tests).
// Conventions follow renderer/pvhv/camera.py: rows of the view matrix are the camera
// axes right/up/back, the novel eye is eye + tx*right + ty*up, matrices are computed
// in float64 and stored column-major as float32.

/** View matrix of the novel view translated by t = [tx, ty]; Float32Array(16), column-major. */
export function viewMatrix(basis, eye, t) {
  const rows = [basis.right, basis.up, basis.back];
  const e = [0, 1, 2].map(k => eye[k] + t[0] * basis.right[k] + t[1] * basis.up[k]);
  const m = new Float32Array(16);
  for (let r = 0; r < 3; r += 1) {
    const a = rows[r];
    m[r] = a[0];
    m[4 + r] = a[1];
    m[8 + r] = a[2];
    m[12 + r] = -a[0] * e[0] + -a[1] * e[1] + -a[2] * e[2];   // camera.py: (-R) @ eye
  }
  m[15] = 1;
  return m;
}

/** Clamp t to the bound disk |t| <= E (scaled to radius E when outside, identity inside). */
export function clampT(t, E) {
  const len = Math.hypot(t[0], t[1]);
  if (len > E) return [t[0] * E / len, t[1] * E / len];
  return [t[0], t[1]];
}

/** Pointer drag of (dx, dy) CSS pixels on a view of cssWidth: right -> tx grows, up -> ty grows. */
export function dragStep(t, dx, dy, cssWidth, E) {
  return clampT([t[0] + 2 * E * dx / cssWidth, t[1] - 2 * E * dy / cssWidth], E);
}

/** Arrow-key step (E/20, or E/5 with Shift); null for keys the view does not handle. */
export function keyStep(t, key, shift, E) {
  const s = shift ? E / 5 : E / 20;
  const d = { ArrowRight: [s, 0], ArrowLeft: [-s, 0], ArrowUp: [0, s], ArrowDown: [0, -s] }[key];
  if (!d) return null;
  return clampT([t[0] + d[0], t[1] + d[1]], E);
}

/** Initial view of the demo: (0.8E, 0.2E). */
export function initialT(E) {
  return [0.8 * E, 0.2 * E];
}

/** The section-E pixel hash (uint32 arithmetic) as float32 in [0, 1), like peel.frag. */
export function pixelHash01(x, y) {
  let h = (Math.imul(x >>> 0, 1973) + Math.imul(y >>> 0, 9277) + 26699) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  h = Math.imul(h, 0x7feb352d) >>> 0;
  h = (h ^ (h >>> 15)) >>> 0;
  h = Math.imul(h, 0x846ca68b) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  return Math.fround((h >>> 8) / 16777216);
}

/** Base64 of the bytes of a typed array (chunked, no DOM). */
export function toBase64(view) {
  const bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

// node --test demo/tests/ : pure math of docs/assets/js/demo/math.js against the exported scenes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { clampT, dragStep, initialT, keyStep, pixelHash01, toBase64, viewMatrix } from '../../docs/assets/js/demo/math.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const scene = name => JSON.parse(readFileSync(path.join(REPO, 'docs', 'assets', 'demo', name, 'scene.json'), 'utf8'));

for (const name of ['chess', 'blocks']) {
  test(`${name}: view matrix at t=0 equals the exported view0 (float32)`, () => {
    const d = scene(name);
    assert.deepEqual([...viewMatrix(d.basis, d.eye, [0, 0])], [...new Float32Array(d.view0)]);
  });

  test(`${name}: view matrix at t=(E,0) equals camera.py's view_matrix((E,0)) (exported view_tE)`, () => {
    const d = scene(name);
    assert.deepEqual([...viewMatrix(d.basis, d.eye, [d.E, 0])], [...new Float32Array(d.view_tE)]);
    // the translation actually moved: the matrix differs from t=0
    assert.notDeepEqual([...viewMatrix(d.basis, d.eye, [d.E, 0])], [...new Float32Array(d.view0)]);
  });
}

test('clampT keeps |t| <= E and is the identity inside the bound', () => {
  const E = 0.08;
  assert.deepEqual(clampT([0.01, -0.02], E), [0.01, -0.02]);
  assert.deepEqual(clampT([E, 0], E), [E, 0]);
  const c = clampT([0.3, 0.4], E);
  assert.ok(Math.abs(Math.hypot(c[0], c[1]) - E) < 1e-15);
  assert.ok(Math.abs(c[0] / c[1] - 0.75) < 1e-12);
  for (let k = 0; k < 100; k += 1) {
    const t = clampT([Math.cos(k) * k, Math.sin(k) * k * 0.01], E);
    assert.ok(Math.hypot(t[0], t[1]) <= E * (1 + 1e-12));
  }
});

test('drag mapping: right -> tx grows, up -> ty grows, 2E per view width', () => {
  const E = 0.5;
  assert.deepEqual(dragStep([0, 0], 100, 0, 1000, E), [0.1, 0]);
  assert.deepEqual(dragStep([0, 0], 0, -100, 1000, E), [0, 0.1]);
  const far = dragStep([0, 0], -3000, 0, 1000, E);
  assert.ok(Math.abs(far[0] + E) < 1e-15 && far[1] === 0);
});

test('key mapping: arrows step E/20, Shift E/5, other keys unhandled', () => {
  const E = 0.08;
  assert.deepEqual(keyStep([0, 0], 'ArrowRight', false, E), [E / 20, 0]);
  assert.deepEqual(keyStep([0, 0], 'ArrowLeft', false, E), [-E / 20, 0]);
  assert.deepEqual(keyStep([0, 0], 'ArrowUp', true, E), [0, E / 5]);
  assert.deepEqual(keyStep([0, 0], 'ArrowDown', false, E), [0, -E / 20]);
  assert.equal(keyStep([0, 0], 'a', false, E), null);
  let t = initialT(E);
  for (let k = 0; k < 60; k += 1) t = keyStep(t, 'ArrowRight', false, E);
  assert.ok(Math.hypot(t[0], t[1]) <= E * (1 + 1e-12) && Math.hypot(t[0], t[1]) >= 0.999 * E);
  assert.deepEqual(initialT(E), [0.8 * E, 0.2 * E]);
});

test('pixelHash01 equals the T01 table (renderer/tests/test_sampling.py)', () => {
  const expected = [[0, 0, 0.651081383228302], [1, 0, 0.6722803711891174], [0, 1, 0.9346001744270325],
    [1234, 567, 0.3125461935997009]];
  for (const [x, y, v] of expected) assert.equal(pixelHash01(x, y), v);
});

test('toBase64 encodes typed-array bytes (little-endian)', () => {
  assert.equal(toBase64(new Uint32Array([1, 0x01020304])), Buffer.from([1, 0, 0, 0, 4, 3, 2, 1]).toString('base64'));
  const big = new Uint8Array(100000).map((_, i) => i & 255);
  assert.equal(toBase64(big), Buffer.from(big).toString('base64'));
});

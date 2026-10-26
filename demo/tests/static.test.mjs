// node --test demo/tests/ : static checks of the shipped demo JavaScript.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import * as SH from '../../docs/assets/js/demo/shaders.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const JS = path.join(REPO, 'docs', 'assets', 'js');
const LABEL = "Re-implementation (2026), not the code used for the paper's experiments";
const ALLOWED_URLS = new Set(['https://polyhaven.com/a/chess_set', 'https://creativecommons.org/publicdomain/zero/1.0/']);

const shipped = [path.join(JS, 'demo.js'),
  ...readdirSync(path.join(JS, 'demo')).filter(f => f.endsWith('.js')).sort().map(f => path.join(JS, 'demo', f))];
const read = f => readFileSync(f, 'utf8');

test('shipped JS contains no URL except the attribution/licence links', () => {
  for (const f of shipped) {
    for (const m of read(f).matchAll(/https?:\/\/[^\s'"`)]+/g)) {
      assert.ok(ALLOWED_URLS.has(m[0]), `${path.relative(REPO, f)}: ${m[0]}`);
    }
    assert.doesNotMatch(read(f), /import[^;]*from\s*['"]https?:/, path.relative(REPO, f));
    assert.doesNotMatch(read(f), /import\s*\(\s*['"]https?:/, path.relative(REPO, f));
  }
});

test('demo.js is a classic script (no import/export statements)', () => {
  const src = read(path.join(JS, 'demo.js'));
  assert.doesNotMatch(src, /^\s*(import|export)\b/m);
  assert.doesNotMatch(src, /\bimport\s*\(/);
});

test('the label line appears verbatim in the shipped JS', () => {
  assert.ok(read(path.join(JS, 'demo.js')).includes(LABEL.replace("'", "\\'")) ||
    read(path.join(JS, 'demo.js')).includes(LABEL));
});

test('shaders are GLSL ES 3.00 and the pipeline bodies equal T01\'s shaders', () => {
  const all = Object.values(SH);
  for (const s of all) {
    assert.ok(s.startsWith('#version 300 es\n'), 'ES 3.00 header first');
    assert.ok(!s.includes('#version 430'));
  }
  const header = SH.WARP_FRAG.slice(0, SH.WARP_FRAG.indexOf('// Forward warping fragment shader'));
  for (const [name, file] of [['GEOMETRY_VERT', 'geometry.vert'], ['PEEL_FRAG', 'peel.frag'],
    ['WARP_VERT', 'warp.vert'], ['WARP_FRAG', 'warp.frag']]) {
    const original = read(path.join(REPO, 'renderer', 'pvhv', 'shaders', file));
    assert.ok(original.startsWith('#version 430 core\n'));
    assert.equal(SH[name], header + original.slice('#version 430 core\n'.length), file);
  }
  for (const p of ['precision highp float;', 'precision highp int;', 'precision highp sampler2D;',
    'precision highp usampler2D;']) assert.ok(header.includes(p));
});

test('shipped JS does not log to the console', () => {
  for (const f of shipped) assert.doesNotMatch(read(f), /console\./, path.relative(REPO, f));
});

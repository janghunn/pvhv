#!/usr/bin/env node
// Browser side of the cross-check: dump the demo's peeled layers and warps as raw
// little-endian arrays for demo/crosscheck.py.
//   node demo/crosscheck.mjs --out <dir> [--gl swiftshader|gpu] [--chrome <path>] [--scenes chess,blocks]
// <dir> must not exist or be empty.  Per scene: <dir>/<scene>/*.bin and meta.json (written
// after the arrays); <dir>/COMPLETE is written last.  Exit 1 on any page error.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { REPO, RENDERER_EXPR, Run, launchChrome, openPage, requireEmptyDir, serveRepo } from './cdp.mjs';

const MODES = ['single', 'bdp', 'edp'];
// views as multiples of E (t = 0 is the internal identity check)
export const VIEWS = [[0, 0], [1, 0], [0, 1], [-0.6, -0.6], [0.5, -0.3]];

function usage(message) {
  console.error(`${message}\nUsage: node demo/crosscheck.mjs --out <dir> [--gl swiftshader|gpu] [--chrome <path>] ` +
    '[--scenes chess,blocks]');
  process.exit(2);
}

const args = process.argv.slice(2);
let out;
let gl = 'swiftshader';
let chromePath = process.env.CHROME || '/usr/bin/google-chrome';
let scenes = ['chess', 'blocks'];
for (let i = 0; i < args.length; i += 1) {
  if (args[i] === '--out' && args[i + 1]) out = path.resolve(args[++i]);
  else if (args[i] === '--gl' && args[i + 1]) gl = args[++i];
  else if (args[i] === '--chrome' && args[i + 1]) chromePath = args[++i];
  else if (args[i] === '--scenes' && args[i + 1]) scenes = args[++i].split(',').filter(Boolean);
  else usage(`Unknown or incomplete argument: ${args[i]}`);
}
if (!out) usage('--out is required');
if (!['swiftshader', 'gpu'].includes(gl)) usage(`--gl must be swiftshader or gpu, not ${gl}`);
if (!scenes.length || scenes.some(s => !['chess', 'blocks'].includes(s))) usage(`bad --scenes ${scenes}`);
if (!(await requireEmptyDir(out))) usage(`${out} exists and is not empty`);
await mkdir(out, { recursive: true });

const sha256 = buf => createHash('sha256').update(buf).digest('hex');
const run = new Run(900000);
const failures = [];
const uncaught = error => run.abort(error instanceof Error ? error : new Error(String(error)));
process.once('uncaughtException', uncaught);
process.once('unhandledRejection', uncaught);

let head = null;
try {
  head = execFileSync('git', ['-C', REPO, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
} catch {
  head = null;
}
const manifestSha = sha256(await readFile(path.join(REPO, 'docs', 'assets', 'demo', 'manifest.json')));

try {
  const origin = await serveRepo(run);
  const { cdp } = await launchChrome(run, { chromePath, gl });
  const page = await openPage(run, cdp, origin, '/demo/harness.html');
  try {
    const renderer = await page.evaluate(RENDERER_EXPR);
    console.error(`WebGL renderer: ${renderer}`);
    await run.waitFor(() => page.evaluate('!!(window.PVHVDemo && PVHVDemo.ready)'), 60000, 'Demo boot');
    const r = await page.evaluate('PVHVDemo.ready.then(() => "ok", e => "failed: " + e.message)', 180000);
    if (r !== 'ok') throw new Error(`PVHVDemo.ready: ${r}`);
    for (const scene of scenes) {
      const dir = path.join(out, scene);
      await mkdir(dir, { recursive: true });
      const done = await page.evaluate(`PVHVDemo.setScene(${JSON.stringify(scene)}).then(() => "ok", e => "failed: " + e.message)`, 180000);
      if (done !== 'ok') throw new Error(`setScene(${scene}): ${done}`);
      const st = await page.evaluate('PVHVDemo.getState()');
      const files = [];
      const dump = async (name, b64) => {
        const buf = Buffer.from(b64, 'base64');
        await writeFile(path.join(dir, name), buf);
        files.push({ name, bytes: buf.length, sha256: sha256(buf) });
      };
      const layers = {};
      for (const method of ['bdp', 'edp']) {
        const n = await page.evaluate(`(window.__pvhvXC = PVHVDemo.readLayers(${JSON.stringify(method)})).length`, 120000);
        layers[method] = [];
        for (let h = 0; h < n; h += 1) {
          // one array per protocol message (a whole layer object can exceed the message limit)
          const field = f => page.evaluate(`window.__pvhvXC[${h}].${f}`, 60000);
          await dump(`layer-${method}-${h}-item.bin`, await field('item'));
          await dump(`layer-${method}-${h}-depth.bin`, await field('depth'));
          if (h === 0 && method === 'bdp') await dump('layer0-color.bin', await field('color'));
          layers[method].push(await field('count'));
        }
        await page.evaluate('delete window.__pvhvXC; true');
      }
      const views = VIEWS.map(([a, b], k) => ({ name: `v${k}`, multiple: [a, b], t: [a * st.E, b * st.E] }));
      for (const v of views) {
        for (const mode of MODES) {
          await page.evaluate(`window.__pvhvXC = PVHVDemo.readWarp(${JSON.stringify(mode)}, ${v.t[0]}, ${v.t[1]}); true`, 120000);
          for (const k of ['item', 'depth', 'layer']) {
            await dump(`warp-${mode}-${v.name}-${k}.bin`, await page.evaluate(`window.__pvhvXC.${k}`, 60000));
          }
        }
      }
      const meta = { scene, renderer, gl, git_head: head, manifest_sha256: manifestSha, W: st.W, H: st.H, Wg: st.Wg,
        Hg: st.Hg, E: st.E, counts: layers, state_counts: st.counts, converged: st.converged, views, modes: MODES, files };
      await writeFile(path.join(dir, 'meta.json'), `${JSON.stringify(meta, null, 1)}\n`);
      console.error(`${scene}: bdp ${JSON.stringify(layers.bdp)} edp ${JSON.stringify(layers.edp)} ` +
        `converged ${JSON.stringify(st.converged)}`);
    }
    if (page.errors.length) failures.push(...page.errors);
    if (page.external.length) failures.push(...page.external.map(u => `external request ${u}`));
  } finally {
    await page.close();
  }
} catch (error) {
  failures.push(`aborted: ${error.message}`);
} finally {
  for (const e of await run.finish()) failures.push(`cleanup: ${e}`);
  process.removeListener('uncaughtException', uncaught);
  process.removeListener('unhandledRejection', uncaught);
}
if (failures.length) {
  console.error(`crosscheck dump FAILED:\n  ${failures.join('\n  ')}`);
  process.exitCode = 1;
} else {
  await writeFile(path.join(out, 'COMPLETE'), `${JSON.stringify({ scenes, gl, git_head: head })}\n`);
  console.error(`crosscheck dump complete: ${out}`);
}

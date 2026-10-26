# Interactive WebGL2 demo

**Re-implementation (2026), not the code used for the paper's experiments**

A WebGL2 port of the offline renderer in `renderer/pvhv/`. It runs in the visitor's browser. When a scene loads,
the demo peels the source view once: layer 0 (z-buffer), full depth peeling (BDP) to convergence, and EDP in the
paper configuration (EDP rule on hidden layers 1–2, UDP after that). Each kept layer stays on the GPU. Whenever the
view changes, the layers of the selected mode are forward-warped to the novel viewpoint t = (tx, ty), with |t| ≤ E.

| mode | warped layers |
|---|---|
| Single layer | layer 0 only |
| Full depth peeling (BDP) | layer 0 + all BDP hidden layers |
| EDP (PVHV culling) | layer 0 + all EDP hidden layers |

Pixels that no layer covers are shown in magenta when a direct render of the novel view has geometry there
(disocclusion holes). Pixels with no geometry at all show the scene background.

The four pipeline shaders are T01's `geometry.vert`, `peel.frag`, `warp.vert` and `warp.frag`. Their bodies are
unchanged; only the `#version 430 core` line is replaced by a GLSL ES 3.00 header, and `static.test.mjs` checks
this. The scene arrays and uniform values are exported from the renderer's own loader, so both implementations
draw the same float32 data.

## Files

| path | what |
|---|---|
| `docs/assets/js/demo.js` | Classic loader (`defer`). Mounts into `#demo-root` in place of its `.slot-box`, injects the CSS, runs feature detection, shows the fallback, then loads `demo/app.js`. |
| `docs/assets/js/demo/app.js` | UI, view state, scene loading, `window.PVHVDemo` |
| `docs/assets/js/demo/gl.js` | WebGL2 passes: peeling, warping, reference render, composite |
| `docs/assets/js/demo/shaders.js` | GLSL ES 3.00 sources |
| `docs/assets/js/demo/math.js` | View matrix, bound clamp, drag/key steps, pixel hash (no DOM) |
| `docs/assets/demo/` | Exporter output: `manifest.json`, `ATTRIBUTION.md`, `<scene>/scene.json`, `<scene>/geometry.bin`, chess JPEGs |
| `demo/export_scene.py` | Exporter |
| `demo/harness.html` | Standalone test page that imitates the project page's demo slot |
| `demo/check_demo.mjs` | Browser check (screens, errors, interaction, widths, fallback, label) |
| `demo/crosscheck.mjs`, `demo/crosscheck.py` | Cross-check against the offline renderer |
| `demo/cdp.mjs` | Shared Chrome/DevTools helpers of the two `.mjs` scripts |
| `demo/payload_size.py` | Shipped payload size (limit 10,000,000 bytes) |
| `demo/tests/` | `pytest` (exporter) and `node --test` (math, static checks) |

## Export

```
PVHV_MODELS_CACHE=<model cache with chess/> python demo/export_scene.py [--out docs/assets/demo] [--scenes chess,blocks]
PVHV_MODELS_CACHE=<model cache with chess/> python demo/export_scene.py --check     # byte-compare a fresh export with --out
```

For every scene (loaded with `load_scene(name, 640, 360)`), the exporter writes the following:

- **Geometry.** Each batch's vertices are de-duplicated on their exact float32 bytes and stored with an index
  list. Expanding the indices reproduces the renderer's arrays bit-exactly.
- **Matrices and constants.** The float32 matrices (column-major, as `peel.py` uploads them), the probe pattern
  and the per-pass constants.
- **Textures.** The diffuse JPEGs are copied unchanged, but only after checking that they decode to exactly the
  renderer's texture pixels.

The batches of one item are written in a canonical order, sorted by a hash of their vertex data; items keep the
scene order. The glTF loader's own order for the primitives of a multi-primitive node depends on Python's
string-hash seed. Since batches of one item share the item ID, this order only decides depth ties inside an item.
`crosscheck.py` applies the same order before it renders.

Each file is written atomically, and `manifest.json` (the authoritative file list) is written last. After that,
the exporter removes only the files that the *previous* `manifest.json` listed and the new export no longer
produces, plus directories that this leaves empty. Every old path is validated first: it must be relative, contain
no `..`, and stay inside the output directory. Files the exporter never published are left alone. A non-empty
output directory without `manifest.json` is refused (exit 2, nothing written). An output directory that is itself a symbolic link, or contains one anywhere, is refused the same way, so every path the exporter writes or removes stays lexically inside it.

The exporter is a single-writer manual build tool and is not safe against concurrent runs. Its outputs are
committed, so git is the recovery mechanism if an export is interrupted.

## Serve and try

Serve the repository root with any static server, for example `python -m http.server`, and open
`/demo/harness.html`. On the project page, the demo needs only `<script src="assets/js/demo.js" defer></script>`
and an element `#demo-root`. Neither the shipped code nor its data makes any request to another origin.

Controls:

- Drag the image, or focus it and use the arrow keys (Shift for steps of E/5 instead of E/20).
- Home or `0` resets the view.
- The other controls are the mode radios, the scene select, "Reset view" and "Highlight fragments from hidden
  layers" (blends hidden-layer winners 50 % with orange).

Without WebGL2, `EXT_color_buffer_float` or 4 draw buffers, the demo shows a static message
(`.pvhv-demo-fallback`, `role="status"`), while the label and attribution stay visible. A runtime failure (fetch,
shader, framebuffer, lost context) ends in the same element with a short reason.

### Test API

`window.PVHVDemo` is always defined once `app.js` has loaded, and does nothing unless a method is called.

| member | behaviour |
|---|---|
| `ready` | Promise that resolves after the first frame of the current scene |
| `getState()` | `{scene, mode, t:[tx,ty], E, W, H, Wg, Hg, counts:{bdp:[...], edp:[...]}, converged:{bdp,edp}, highlight, frames, loading, failed}`. `counts` includes layer 0. |
| `setMode(m)` | `'single'`, `'bdp'` or `'edp'` |
| `setView(tx, ty)` | Clamps to the bound and returns the applied t |
| `setScene(id)` | `'chess'` or `'blocks'`. Returns a Promise that resolves after that scene's first frame. A later call supersedes an earlier one. |
| `setHighlight(on)` | Highlight option |
| `whenIdle()` | Promise that resolves once no frame is pending (after the next frame if one is scheduled) |
| `readLayers(method)` | `[{count, item, depth, color}]`: base64 of Uint32 / Float32 / RGBA8 arrays of Wg·Hg, bottom row first |
| `readWarp(mode, tx, ty)` | `{item, depth, layer}`: base64 arrays of W·H. Does not change the displayed frame. |
| `readCanvasStats()` | `{nonBackground, holes, distinctColors}` of the displayed frame (read from the display framebuffer) |

## Checks

```
python -m pytest -q -p no:cacheprovider demo/tests                 # exporter (chess tests need PVHV_MODELS_CACHE)
node --test demo/tests/                                            # math + static checks
python demo/payload_size.py                                        # shipped payload, limit 10,000,000 bytes
node demo/check_demo.mjs --out <empty dir> [--gl swiftshader|gpu] [--chrome <path>]
node demo/crosscheck.mjs --out <empty dir> [--gl swiftshader|gpu] --scenes chess,blocks
PVHV_MODELS_CACHE=... PVHV_EGL_DEVICE=0 python demo/crosscheck.py --browser <dir> [--json <file>]
```

`check_demo.mjs` serves the repository root on 127.0.0.1 and drives headless Chrome over the DevTools protocol.
Two backends are available: `swiftshader` (`--use-angle=swiftshader --enable-unsafe-swiftshader`) and `gpu`
(`--use-gl=angle --use-angle=gl-egl`). It covers these checks:

- **Modes.** Per-mode frame statistics and screenshots for both scenes. Holes(single) must be greater than
  holes(BDP).
- **Keyboard and pointer.** Tab reaches the view; arrow-key steps are checked, including clamping to the bound.
  Home resets the view. Mouse drags work horizontally and vertically, and a mode radio can be operated with the
  keyboard.
- **Mobile.** A touch drag at 390×844.
- **Layout.** No horizontal scroll at 390 and 1440 px.
- **Label and attribution.** Checked for each scene.
- **Fallbacks.** Both fallbacks are shown: `--disable-3d-apis` and `?simulate=no-float-rt`. The latter is a
  harness-only switch.
- **Errors and requests.** Any console error, exception, error-level log entry or non-local request is a failure.

The script writes `summary.json` last.

`crosscheck.mjs` dumps the following for each scene, followed by `meta.json` and, last, a `COMPLETE` marker:

- the BDP and EDP layers (item and depth for every layer, colour for layer 0);
- the warps of every mode at t = (0,0), (E,0), (0,E), (−0.6E,−0.6E) and (0.5E,−0.3E).

`crosscheck.py` first validates the dump (marker, per-file length and sha256, scenes/views/modes, and the manifest
hash against the working tree; exit 2 if any check fails). It then peels and warps the same scenes and views with
the offline renderer and applies the gates below.

### Tolerances

"Agreement" is the fraction of the union of occupied pixels (item ≠ 0 on either side) whose item is equal.

| gate | requirement (per scene) |
|---|---|
| X1 layer 0 | item agreement ≥ 0.999; \|Δcount\| ≤ 0.1 % of the offline count |
| X2 hidden layers (per method, h ≥ 1) | \|Δcount\| ≤ max(32, 1 % of the offline count); item agreement ≥ 0.99 for layers with ≥ 1000 offline fragments; total hidden \|Δ\| ≤ 1 %; layer count equal ±1; both converged |
| X3 depth | on pixels with equal non-zero item (layer 0 and every warp), \|Δz\|/(far−near) ≤ 1e-4 for ≥ 99.9 % of them |
| X4 warps | item agreement ≥ 0.99 for every mode at the 4 non-zero views; at t = 0 the browser warp equals the central crop of the browser layer 0 exactly (item and depth bits) |
| X5 | EDP hidden total < BDP hidden total, in the browser and offline (both printed) |
| X6 colour | layer-0 pixels with equal item: mean \|ΔRGB\| ≤ 6/255 and 99th percentile ≤ 32/255 per channel (sRGB bytes) |

**Why these values.** Both sides run the same float32 algorithm on the same exported vertex data and uniforms. The
remaining differences come from rasterizer and ALU differences:

- ANGLE→SwiftShader/Vulkan, or GL ES versus desktop GL;
- edge pixels whose centres lie within a few ulps of a triangle edge;
- last-ulp errors in reciprocal/sin/cos that move an EDP probe across a texel border, or a depth across the eps/Δ
  thresholds.

Such a flip can cascade into later layers. These effects reach only a small fraction of edge pixels. Every real
port bug we can think of moves the numbers much further:

- On chess, EDP and BDP differ by 21 % at layer 1, and EDP and UDP by 19 %.
- A half-pixel camera offset or a flipped row order breaks layer-0 item agreement massively.
- Texture orientation does not affect items, which is why X6 exists. A flipped texture or a wrong sRGB path gives
  tens of levels of mean colour error.

So 0.1 % and 1 % separate "same algorithm, other GPU" from "different algorithm". The depth limit of 1e-4
(normalized) is 5× below eps = 5e-4, the smallest threshold the algorithm uses.

**Which backend is gated.** The gates are evaluated on the hardware backend, `--gl gpu` (ANGLE on the GPU's
GL ES driver); that run is required to pass. The SwiftShader cross-check is informational. `check_demo.mjs`
(screens, interaction, fallback) is run on both backends. `crosscheck.py` prints the browser renderer on its
verdict line.

**Measured (Chrome 156, 2026-10-26).**

- **`--gl gpu` (ANGLE on the NVIDIA GL ES driver): `CROSSCHECK PASS`, 113/113 gates.** All counts, items, depth
  bits and colours are identical to the offline renderer, for both scenes and all views.
- **`--gl swiftshader` (CPU software rasterizer), informational: `CROSSCHECK FAIL`, 80/113 gates.**
  - **Holding.** Counts are within a few fragments; for example chess BDP is [149250, 39284, 5669, 344, 6] against
    [149252, 39291, 5667, 345, 7] offline. Layer-0 item agreement is 0.99943 (chess) and 0.99979 (blocks).
    X2 counts and totals, X4 item agreement and the t = 0 identity, X5 and X6 also hold.
  - **Failing: X2 item agreement on chess layer 2.** 0.98858 (BDP) and 0.98438 (EDP), against the 0.99 gate.
  - **Failing: X3 depth on blocks layer 0.** Only 0.97287 of pixels are within 1e-4 (max 4.0e-4). Almost all of
    these pixels are on the ground, a single large quad that the near plane clips. There, SwiftShader's
    interpolated depth differs by a median of 2.9e-5 (normalized).
  - **Failing: X3 depth on all warps.** 0.95–0.999 of pixels are within 1e-4.
  - **Control experiment.** Warping the browser's *own* layers with the offline warper (identical inputs) already
    matches the browser's warp depth exactly on only about 97 % of pixels. The cause is how SwiftShader
    rasterizes points, so the remaining differences belong to SwiftShader, not to the port.

## Attribution

- Chess scene: [Chess Set](https://polyhaven.com/a/chess_set) by Riley Queen, Poly Haven,
  [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). The geometry is re-exported and the three diffuse
  JPEGs are copied unchanged.
- Blocks scene: procedural, part of this re-implementation (MIT).
- No model of the paper's own scenes (including the elephant) is used. See `docs/assets/demo/ATTRIBUTION.md`.

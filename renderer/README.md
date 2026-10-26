# PVHV / EDP renderer

Re-implementation (2026), not the code used for the paper's experiments

An offline GPU renderer for effective depth peeling (EDP) with potentially visible hidden volumes (PVHV), the
depth-peeling baselines it is compared with, and forward warping of the peeled layers to novel views.

Paper: Janghun Kim and Sungkil Lee. Potentially Visible Hidden-Volume Rendering for Multi-View Warping. ACM Transactions on Graphics 42(4) (Proc. SIGGRAPH 2023). https://doi.org/10.1145/3592108

## What is implemented

Applications of the method, such as depth-of-field rendering, are described in the paper
(https://doi.org/10.1145/3592108) and are not implemented in this re-implementation.

| `--method` | layers | rule for hidden layer h >= 1 (layer 0 is a plain z-buffer image) |
|---|---|---|
| `bdp` | full depth peeling | nearest fragment with `zn > zn(h-1) + eps`, eps = 0.0005 |
| `udp` | umbra-culling depth peeling | as BDP, and behind the umbra of the previous fragment's pixel footprint under the lens |
| `edp` | EDP, paper configuration | layers 1..`--edp-layers` (default 2): BDP rule and the PVHV test (Algorithm 1, sampled search with the scene's `samples` probes, default 14, Δ = 0.002); deeper layers: UDP rule. `--edp-layers all` uses the PVHV test on every layer |

- Peeling is GPU rasterisation (OpenGL 4.3 core over headless EGL, via moderngl). The PVHV test runs per fragment in
  the fragment shader (`pvhv/shaders/peel.frag`).
- Warping (`pvhv/shaders/warp.vert`) splats each fragment as four subpixel points and resolves visibility with a
  depth test on normalized linear depth; layers are drawn front to back.
- `zn = (z - near)/(far - near)` with fixed per-scene `near`/`far` (`pvhv/scenes/*.json`). The novel-view bound is a
  disk of radius `E` (scene units) in the source lens plane; the source image has a 1.2x guard band.
- The peel and warp shaders use only constructs that also exist in GLSL ES 3.00 (no compute shaders, no SSBOs).
- Deviations from the paper (margin of 1.5 px on the search radius, Halton probe pattern scaled so that its
  outermost point lies on the search circle and rotated per pixel, probes outside the image count as edges,
  point-splat warping instead of atomics, alpha-tested instead of blended transparency, Lambert shading) are marked
  in the shader and module comments.

## Setup

```
conda env create -f environment.yml        # conda env "pvhv", Python 3.11, pip -r requirements.txt
conda activate pvhv
```
or, in an existing Python 3.11: `pip install -r requirements.txt`.

Requirements: an NVIDIA GPU and driver with EGL (OpenGL 4.3). The textured scenes need the models:
`python ../models/fetch_models.py` (see [`../models/README.md`](../models/README.md)). The scene `blocks` is
procedural and needs no download.

## GPU selection

The EGL device is chosen with `--egl-device N` (before the command) or the environment variable
`PVHV_EGL_DEVICE` (default 0). The EGL device order need not match `nvidia-smi`'s order: on the development
machine EGL device 0 was `nvidia-smi` GPU 1. `python -m pvhv info` prints the GL renderer and the device index;
to see which physical GPU it is, look for the python process in `nvidia-smi` while a command runs.

## Commands (run from `renderer/`)

```
python -m pvhv info
python -m pvhv render --scene blocks --method edp [--edp-layers 2|all] [--search sampled|exhaustive] [--max-layers 128] --out out/blocks_edp [--force]
python -m pvhv warp   --scene blocks --method edp --t 0.4619 0.1913 [--layers N] --out out/blocks_edp_warp.png
python -m pvhv verify --scenes blocks,sanmiguel,bistro,chess [--views 16] [--json out/verify.json]
```

- `render` peels until a layer is empty (or `--max-layers`) and writes `layer_000.png`, `layer_001.png`, ...
  (orange `#F2A65A` = empty pixel) and `counts.json` (per-layer fragment counts, `converged`). It refuses a
  non-empty `--out` unless `--force`, which replaces only `layer_*.png` and `counts.json`.
- `warp` writes the novel view at lens offset `t = (TX, TY)` (scene units, source camera axes); holes are
  magenta `#FF00FF`. `--layers 1` gives the single-layer warp.
- `verify` peels BDP, UDP and EDP to convergence, warps BDP and EDP to 16 lens positions (8 on `|t| = E`, 8 on
  `|t| = E/2`) and checks, per scene:
  - G1: EDP keeps strictly fewer hidden fragments than BDP; every method converged;
  - G2: EDP loses (almost) nothing that full peeling gets right: `max L_v <= 5e-4` and `mean L_v <= 1e-4`, where
    `L_v` = fraction of pixels at which the BDP warp agrees with a direct render of the novel view (same item and
    `|Δzn| <= 0.01`, or a hole where the render is empty) and the EDP warp does not. The symmetric count `L_rev`,
    the raw warp difference `D_v` and an exhaustive-search EDP row are printed for information;
  - G3: hidden layers fill at least `1e-3` of the pixels on average over the boundary views (non-vacuity).
  It also prints false-negative fractions against a directly rendered reference (single-layer, BDP, EDP), and
  prints `VERIFY PASS` / `VERIFY FAIL` (exit status 0 / 1).

Outputs go to `renderer/out/`, which is git-ignored.

## Figures

```
python -m pvhv figures --out ../figures [--only keep_cull,novel_view,layers]
python -m pvhv figures --check ../figures [--only NAME]
```
- `figures` renders `keep_cull`, `novel_view` (San Miguel, Bistro) and `layers` (chess set) into the
  directory. Each image is a PNG, or a JPEG (quality 92, 4:4:4) when the PNG would exceed 1 MiB, and carries the
  label line as a footer. Descriptions, scenes and model attribution: [`../figures/README.md`](../figures/README.md).
- `--check DIR` regenerates into a temporary directory (never into DIR) and compares with the files in DIR: a figure
  passes when its decoded RGB differs by more than 2/255 in at most 0.1 % of the pixels. Byte identity is printed
  per figure. Missing or extra figures, or any mismatch, give exit status 1.
- The figures need the fetched models and all their textures (a figure refuses a scene with unresolved textures).

## Tests

```
python -m pytest -q -m "not gpu"                       # numpy reference, geometry and figure-README tests, no GPU
PVHV_REQUIRE_GPU=1 PVHV_EGL_DEVICE=0 python -m pytest -q -m gpu
```
GPU tests are skipped when no NVIDIA EGL context can be created, unless `PVHV_REQUIRE_GPU=1`, which turns that into
an error. The GPU tests check layer ordering, the PVHV rule on every EDP layer, bit-agreement of the GPU classifier
with the numpy Algorithm 1 reference (`pvhv/pvhv_cpu.py`), warp geometry against direct renders, and the gates on
`blocks`.

## Provenance

This is a clean-room implementation written from the paper's text only. No code, slides or videos from the
paper's authors or from other implementations were consulted. Scene models are third-party assets downloaded
from their publishers and are not part of this repository; see [`../models/README.md`](../models/README.md) for
their licences and attribution.

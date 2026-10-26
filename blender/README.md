# Teaser image (Blender Cycles + hidden layer)

`out/teaser.webp` (1920×1080, 16:9, ~0.47 MB) is the project-page teaser. It is a single frame of the
San Miguel courtyard, seen from the **exact source camera of the 2026 re-implementation**
(`renderer/pvhv/scenes/sanmiguel.json`), split by a slanted seam:

- **left of the seam**: a path-traced render of the source view in Blender Cycles (sun + sky, global illumination);
- **right of the seam**: the first hidden layer (h = 1) behind the same pixels, computed by the re-implementation
  (`classify_layer1`: BDP layer 1 with the per-fragment PVHV test), coloured by the result of the test.

Both halves come from the same camera and are pixel-aligned, so the seam reads as peeling off the visible surface:
what lies behind it, and which of those hidden fragments the PVHV test keeps (the hidden surfaces it finds
potentially visible from novel views within the bound E) and which it culls (the rest).

## Requirements

- **Blender 5.2.2 LTS**, official portable Linux x64 build, installed outside the repository:
  - URL: https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz
  - sha256: `84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168`
  - `blender/install_blender.sh` downloads it into `${BLENDER_PREFIX:-$HOME/.local/opt}`, verifies the sha256
    before extracting, checks that the binary reports `Blender 5.2.2` and prints its path.
    No Blender files are committed.
- The fetched models: `python models/fetch_models.py` (San Miguel low-poly OBJ + textures in the model cache).
- The renderer's conda environment `pvhv` (`renderer/environment.yml`; numpy, Pillow, moderngl).
- An NVIDIA GPU with EGL (renderer) and OptiX or CUDA (Cycles).

## Regenerate

One command, from any directory:

```
conda activate pvhv && blender/make_teaser.sh
```

(or, without activating: `PVHV_PYTHON=<conda envs dir>/pvhv/bin/python blender/make_teaser.sh`).
It runs three stages and replaces `out/teaser.webp`; intermediates go to `blender/build/` (git-ignored):

1. `export_layers.py` (renderer env, EGL) → `build/layer1_1920x1080.npz`: central 1920×1080 crop of the
   renderer's `classify_layer1` output (h = 1 colour, fragment mask, PVHV keep flag; layer-0 depth for the
   alignment check). Fails on unresolved textures; reports whether the parsed-OBJ cache was a hit.
2. `scene_teaser.py` (`blender -b --factory-startup --python-exit-code 1 -P ...`) → `build/beauty.png`
   (16-bit PNG, AgX).
3. `composite.py` → `build/teaser_full.png` (lossless) and `out/teaser.webp` (lossy, written via a temp file
   and renamed).

Check that a fresh regeneration matches the committed image (writes only into `build/check/`):

```
conda activate pvhv && blender/make_teaser.sh --check
```

The exit status is non-zero if any stage fails or the comparison is out of tolerance.

### Environment variables

| variable | default | meaning |
|---|---|---|
| `BLENDER` | `~/.local/opt/blender-5.2.2-linux-x64/blender` | Blender binary; must report 5.2.2 |
| `ALLOW_OTHER_BLENDER` | unset | `1` runs with another Blender version (the output will not match) |
| `PVHV_PYTHON` | `python` | python of the `pvhv` env |
| `PVHV_MODELS_CACHE` | `<repo>/models/cache` | model cache root (as for the renderer) |
| `PVHV_EGL_DEVICE` | `0` | EGL device of the renderer (stage 1) |
| `CYCLES_DEVICE_INDEX` | `0` | Cycles GPU: index among the visible OptiX (else CUDA) GPUs in PCI bus order, parsed from the Cycles device id (with a warning, device-list order if an id has no PCI address) (stage 2) |
| `CUDA_VISIBLE_DEVICES` | unset | honoured by Cycles; the script sets `CUDA_DEVICE_ORDER=PCI_BUS_ID` |

EGL and CUDA number GPUs differently. On the reference machine (2× RTX PRO 6000), `PVHV_EGL_DEVICE=0` and
`CYCLES_DEVICE_INDEX=1` are the same physical GPU (PCI 61:00, `nvidia-smi` GPU 1). The choice of GPU affects
only speed: renders on either GPU agree within the tolerance below.

### Tolerance

`--check` decodes the fresh and the committed WebP and passes iff

- mean |Δ| over all pixels and channels ≤ **1.5** (8-bit units), and
- the fraction of pixels whose largest channel |Δ| exceeds 8/255 is ≤ **0.01**.

Measured on the reference machine, each time an independent full regeneration against an earlier one:
0.6376 / 0.00286 (same GPU), 0.6174 / 0.00275 (other GPU), 0.6271 / 0.00281, 0.5966 / 0.00256, and for the
committed file 0.6772 / 0.00284 (mean |Δ| / fraction > 8).
The hidden-layer arrays were bit-identical between runs and the 16-bit Cycles renders differed by at most
1/255 in about 150 pixels (8-bit). The lossy WebP encoder amplifies those 1-LSB differences into small
differences spread over the image (identical input encodes byte-identically). For comparison, brightening
the Cycles render by 3 % gives 1.69 / 0.0248 and moving the seam by 0.01·W gives 2.22 / 0.0242; both fail.
A different font (see below), Blender version or driver may exceed the tolerance.

## Render settings

- **Camera** (from `sanmiguel.json`, no copied constants): eye (22, 1.7, 13), target (8, 2, 2), up +Y,
  vertical FOV 50°, near 0.5, far 40. The OBJ is imported with forward −Z / up Y, so renderer world (x, y, z)
  is Blender (x, −z, y); the camera's `matrix_world` columns are C·right, C·up, C·back, C·eye with
  C = [[1,0,0],[0,0,−1],[0,1,0]]; `sensor_fit` vertical, sensor 24 mm, lens 24/(2·tan 25°) ≈ 25.734 mm,
  no shift, no defocus blur, 1920×1080 at 100 %. This matches the renderer's central W×H crop of its guard-band
  source view.
- **Alignment check** (one-time, not part of the pipeline; run after a regeneration):
  ```
  $BLENDER -b --factory-startup --python-exit-code 1 -P blender/scene_teaser.py -- \
      --models-cache "$PVHV_MODELS_CACHE" --depth-out blender/build/blender_depth.npy
  python blender/composite.py --align-check blender/build/blender_depth.npy
  ```
  compares Blender's camera-axis depth (one centred sample per pixel, alpha-tested foliage) with the renderer's
  layer-0 linear depth from stage 1:
  median |Δz| 5.7e-6 m, 95th percentile 7.0e-5 m (relative 7.1e-7 / 8.3e-6), best integer shift (0, 0);
  94.8 % of the renderer's depth edges coincide exactly with Blender's, 99.8 % within 1 px, 99.9 % within 2 px.
  Edge overlay: `build/align_edges.png`.
- **Materials**: the imported Principled BSDFs, with the diffuse texture's alpha thresholded at 0.5 into Alpha
  (alpha-tested foliage, as in the renderer; 265 materials); the 55 imported normal/bump maps removed (the
  bundled `license.txt` says they are mis-mapped); the 2 imported specular maps (`map_Ks` → Specular IOR Level)
  unlinked, as is any texture on Roughness/Metallic (none in this OBJ), so for every material roughness ≥ 0.55,
  specular IOR level ≤ 0.35 (the surfaces are too glossy as shipped) and metallic 0; constant alpha 1 (the
  renderer treats `d = 0.5` as opaque). After these fixes only Base Color and Alpha are linked into the BSDF.
  The script fails if any image texture of any material does not load.
- **Light**: sun at 50° elevation, azimuth 40° (Blender world, 0 = +Y), 10 W/m², colour (1.0, 0.66, 0.38),
  1.2° disc; world = Multiple-Scattering sky (sun disc off) mixed 60 % toward warm grey (0.32, 0.27, 0.22),
  strength 0.6. Lower sun elevations leave the courtyard floor in the shadow of the arcades and trees.
- **Cycles**: OptiX (CUDA fallback), one GPU, 384 samples, adaptive sampling off, seed 0, max bounces 6
  (diffuse 4, glossy 2, transmission 4, transparent 24), indirect clamp 8, caustics off; OpenImageDenoise on
  the CPU (albedo + normal, accurate prefilter).
- **Colour**: AgX view transform, look "AgX - Base Contrast", exposure +1.7, sRGB display; 16-bit PNG.
- **Composite**: seam from (0.62·W, 0) to (0.50·W, H) with analytic anti-aliasing and a 2.5 px light line
  (246, 240, 228). Hidden layer h = 1: kept = renderer colour lifted 18 % toward white; culled = 8 % of the
  renderer colour + 15 % of the page orange `#F2A65A` (dark orange-brown, kept dim so the kept fragments read
  as the subject); empty = dark slate `#151A22`. Legend: "kept (own colour)", "culled", "empty".
  Tags: Lato Semibold/Bold 23 px from `/usr/share/fonts/truetype/lato/`, else DejaVu Sans, else Pillow's
  default font. The tags are drawn into the image, so a different font changes those pixels.
- **WebP**: Pillow `quality=86, method=6` (the first of 86/82/78/74/70 that fits in 1 MB).

### Timing (reference machine, one RTX PRO 6000)

stage 1 5.7 s, stage 2 20.8 s (OBJ import 5.2 s, render + denoise ≈ 14.5 s), stage 3 1.7 s: **28.3 s** total.

## Caption

> Left: the San Miguel courtyard path-traced in Blender Cycles 5.2.2 from the source camera of our
> re-implementation. Right of the seam: the first hidden layer (h = 1) behind the same pixels, computed by the
> re-implementation's PVHV test: fragments it keeps are shown in their own colour, fragments it culls in dark
> orange-brown, and pixels with no hidden surface in dark slate.
> Re-implementation (2026), not the code used for the paper's experiments.
> San Miguel 2.0 © Guillermo M. Leal Llaguno (Evolución Visual); 2017 version by Morgan McGuire, Guedis Cardenas, Michael Mara and Nicholas Hull; from Morgan McGuire's Computer Graphics Archive (https://casual-effects.com/data); licensed CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/)

For this view, the re-implementation keeps 52.7 % of the hidden-layer pixels (44.8 % of the frame kept, 40.1 %
culled, 15.1 % empty). These fractions were measured by the re-implementation for this view at 1920×1080 and are
not results from the paper.

## Model attribution

San Miguel 2.0 © Guillermo M. Leal Llaguno (Evolución Visual); 2017 version by Morgan McGuire, Guedis Cardenas, Michael Mara and Nicholas Hull; from Morgan McGuire's Computer Graphics Archive (https://casual-effects.com/data); licensed CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/)

The model is not redistributed here; `models/fetch_models.py` fetches it (the bundled `license.txt` states
research and educational use with attribution).

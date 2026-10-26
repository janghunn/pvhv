"""Stage 1 of the teaser: export the re-implementation's hidden layer h = 1 for San Miguel.

Runs in the renderer environment (conda env ``pvhv``) on an EGL GPU.  Calls the
renderer's own ``classify_layer1`` (BDP layer 1 with the per-fragment PVHV test) at
the teaser resolution, crops the guard band to the central W x H region and stores
top-row-first arrays in an .npz:

    color    (H, W, 3) uint8   sRGB-encoded textured Lambert colour of the h = 1 fragment
    mask     (H, W)    bool    an h = 1 fragment exists
    in_pvhv  (H, W)    bool    kept by the PVHV test (meaningful where mask)
    depth0   (H, W)    float32 layer-0 linear camera depth (0 = empty; alignment check only)
    item0    (H, W)    uint32  layer-0 item id (0 = empty; alignment check only)

Nothing under renderer/ or the model cache is modified (the parsed-OBJ cache must be a hit
for a read-only cache; a miss is reported, and the renderer then writes it as usual).
"""
import argparse
import glob
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "renderer"))
sys.dont_write_bytecode = True          # leave renderer/ untouched (no __pycache__)

from pvhv import gl  # noqa: E402
from pvhv.peel import SceneRenderer  # noqa: E402
from pvhv.scene import load_config, load_scene, models_cache_dir  # noqa: E402

SCENE = "sanmiguel"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--out", default=os.path.join(HERE, "build", "layer1_1920x1080.npz"))
    p.add_argument("--egl-device", type=int, default=None,
                   help="EGL device index (default: PVHV_EGL_DEVICE, then 0)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    t0 = time.perf_counter()
    cfg = load_config(SCENE)
    obj_rel = cfg["objects"][0]["obj"]
    obj_path = os.path.join(models_cache_dir(), obj_rel)
    if not os.path.isfile(obj_path):
        sys.exit(f"error: {obj_path} not found - run models/fetch_models.py (or set PVHV_MODELS_CACHE)")
    stem = os.path.splitext(os.path.basename(obj_rel))[0]
    hit = glob.glob(os.path.join(models_cache_dir(), cfg["objects"][0]["model"], f"{stem}.v*.npz"))
    print(f"parsed-OBJ cache: {'hit ' + os.path.basename(hit[0]) if hit else 'miss (renderer will write it)'}")

    scene = load_scene(SCENE, width=args.width, height=args.height)
    if scene.unresolved_textures:
        sys.exit(f"error: {len(scene.unresolved_textures)} unresolved texture(s): "
                 + ", ".join(sorted(set(scene.unresolved_textures))[:10]))
    cam, c = scene.camera, cfg["camera"]
    assert tuple(cam.eye) == tuple(c["eye"]) and tuple(cam.target) == tuple(c["target"]), "camera mismatch"
    assert tuple(cam.up) == tuple(c["up"]) and cam.fovy_deg == float(c["fovy"]), "camera mismatch"
    assert (cam.W, cam.H) == (args.width, args.height) and cam.near == cfg["near"] and cam.far == cfg["far"]
    t_load = time.perf_counter()

    ctx = gl.create_context(args.egl_device)
    print(f"EGL device {ctx.extra['device_index']}: {gl.renderer_string(ctx)}")
    r = SceneRenderer(scene, ctx)
    try:
        d = r.classify_layer1()
    finally:
        r.release()
    t_gpu = time.perf_counter()

    def crop(a):  # central W x H, then bottom-row-first -> top-row-first
        return np.ascontiguousarray(a[cam.oy:cam.oy + cam.H, cam.ox:cam.ox + cam.W][::-1])

    mask = crop(d["mask"])
    in_pvhv = crop(d["in_pvhv"]) & mask
    out = {"color": crop(d["color"])[..., :3], "mask": mask, "in_pvhv": in_pvhv,
           "depth0": crop(d["prev_depth"]).astype(np.float32), "item0": crop(d["prev_item"]).astype(np.uint32)}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = args.out + ".tmp.npz"
    np.savez_compressed(tmp, **out)
    os.replace(tmp, args.out)

    n = mask.size
    kept, culled = int(np.count_nonzero(in_pvhv)), int(np.count_nonzero(mask & ~in_pvhv))
    print(f"h=1 pixels of the {cam.W}x{cam.H} crop: kept {100.0 * kept / n:.1f}%  "
          f"culled {100.0 * culled / n:.1f}%  empty {100.0 * (n - kept - culled) / n:.1f}%  "
          f"(kept share of hidden {100.0 * kept / max(kept + culled, 1):.1f}%)")
    print(f"wrote {args.out}  [load {t_load - t0:.1f}s, gpu {t_gpu - t_load:.1f}s, "
          f"total {time.perf_counter() - t0:.1f}s]")


if __name__ == "__main__":
    main()

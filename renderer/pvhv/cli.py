"""Command line interface: ``python -m pvhv {info,render,warp,verify,figures}`` (run from renderer/)."""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

EMPTY_RGB = (0xF2, 0xA6, 0x5A)     # orange: empty pixel of a layer
HOLE_RGB = (0xFF, 0x00, 0xFF)      # magenta: hole of a warp


def _edp_layers(v):
    return "all" if v == "all" else int(v)


def _context(args):
    from . import gl
    ctx = gl.create_context(args.egl_device)
    return ctx


def _save_png(path, rgb_gl):
    """Save an (H, W, 3) uint8 image stored in GL orientation (row 0 = bottom)."""
    from PIL import Image
    Image.fromarray(np.ascontiguousarray(rgb_gl[::-1])).save(path, optimize=False)


def _atomic_write_png(path, rgb_gl):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".png")
    os.close(fd)
    try:
        _save_png(tmp, rgb_gl)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def layer_rgb(color, item):
    rgb = color[..., :3].copy()
    rgb[item == 0] = EMPTY_RGB
    return rgb


def warp_rgb(w):
    rgb = w.color[..., :3].copy()
    rgb[w.layer == 0] = HOLE_RGB
    return rgb


def cmd_info(args):
    from . import gl
    ctx = _context(args)
    print(f"GL renderer: {ctx.info['GL_RENDERER']}")
    print(f"GL version: {ctx.info['GL_VERSION']}")
    print(f"EGL device index: {gl.device_index_from_env(args.egl_device)}")
    return 0


def _render_out_busy(out):
    print(f"error: {out} is not empty (use --force to replace its layer_*.png/counts.json)", file=sys.stderr)
    return 2


def _publish_render(tmp, out, force):
    """Move the finished render in ``tmp`` to ``out``; the emptiness rule is re-checked here,
    at publication time (another render may have filled ``out`` meanwhile).

    New ``out``: atomic rename.  Existing, non-empty ``out`` without ``force``: left untouched,
    exit status 2.  With ``force``: old layer_*.png/counts.json are removed, then replaced.
    ``tmp`` is removed in every case.  Returns the exit status."""
    try:
        if not os.path.exists(out):
            try:
                os.rename(tmp, out)
                return 0
            except OSError:
                pass                       # created concurrently: apply the existing-directory rule
        if os.listdir(out) and not force:
            return _render_out_busy(out)
        if force:
            for f in os.listdir(out):
                if (f.startswith("layer_") and f.endswith(".png")) or f == "counts.json":
                    os.unlink(os.path.join(out, f))
        for f in sorted(os.listdir(tmp)):
            os.replace(os.path.join(tmp, f), os.path.join(out, f))
        return 0
    finally:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)


def cmd_render(args):
    from . import gl
    from .peel import get_renderer
    from .scene import load_scene

    out = os.path.abspath(args.out)
    if os.path.isdir(out) and os.listdir(out) and not args.force:      # early exit; re-checked when publishing
        return _render_out_busy(out)
    ctx = _context(args)
    print(f"GL renderer: {gl.renderer_string(ctx)} (EGL device {gl.device_index_from_env(args.egl_device)})")
    scene = load_scene(args.scene, verbose=True)
    print(f"scene {scene.name}: {scene.n_triangles} triangles, {scene.n_items} items, "
          f"unresolved textures: {len(set(scene.unresolved_textures))}")
    r = get_renderer(scene, ctx)
    layers = r.peel(args.method, edp_layers=args.edp_layers, max_layers=args.max_layers, search=args.search)
    summary = layers.summary()
    summary["renderer"] = gl.renderer_string(ctx)
    summary["unresolved_textures"] = len(set(scene.unresolved_textures))
    parent = os.path.dirname(out)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=parent, prefix=".tmp-render-")
    try:
        for h in range(layers.n_layers):
            _save_png(os.path.join(tmp, f"layer_{h:03d}.png"), layer_rgb(layers.color[h], layers.item[h]))
        with open(os.path.join(tmp, "counts.json"), "w") as fh:
            json.dump(summary, fh, indent=1)
        rc = _publish_render(tmp, out, args.force)
    finally:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
    if rc:
        return rc
    print(f"{args.method.upper()}: {layers.n_layers} layers, converged={layers.converged}, "
          f"hidden fragments {layers.hidden_count} ({layers.hidden_count / layers.pixels:.4f}), "
          f"counts {layers.counts}, {layers.seconds:.2f}s")
    print(f"wrote {layers.n_layers} layer PNGs and counts.json to {out}")
    return 0


def cmd_warp(args):
    from . import gl
    from .peel import get_renderer
    from .scene import load_scene
    from .warp import warp

    ctx = _context(args)
    print(f"GL renderer: {gl.renderer_string(ctx)} (EGL device {gl.device_index_from_env(args.egl_device)})")
    scene = load_scene(args.scene, verbose=True)
    r = get_renderer(scene, ctx)
    layers = r.peel(args.method, edp_layers=args.edp_layers, max_layers=args.max_layers, search=args.search)
    t = (args.t[0], args.t[1])
    w = warp(layers, t, n_layers=args.layers, ctx=ctx)
    _atomic_write_png(args.out, warp_rgb(w))
    used = layers.n_layers if args.layers is None else min(args.layers, layers.n_layers)
    print(f"{args.method.upper()} warp of {used}/{layers.n_layers} layers to t=({t[0]:g}, {t[1]:g}) "
          f"(|t|/E = {np.hypot(*t) / scene.E:.3f}): holes {np.count_nonzero(w.layer == 0) / w.layer.size:.4f}, "
          f"hidden-layer fill {np.count_nonzero(w.layer >= 2) / w.layer.size:.4f}")
    print(f"wrote {os.path.abspath(args.out)}")
    return 0


def cmd_verify(args):
    from . import gl
    from .metrics import verify_scene
    from .peel import SceneRenderer
    from .scene import load_scene

    ctx = _context(args)
    print(f"GL renderer: {gl.renderer_string(ctx)} (EGL device {gl.device_index_from_env(args.egl_device)})")
    results, ok = [], True
    for name in [s for s in args.scenes.split(",") if s]:
        t0 = time.perf_counter()
        scene = load_scene(name, verbose=True)
        t_load = time.perf_counter() - t0
        r = SceneRenderer(scene, ctx)
        res = verify_scene(scene, n_views=args.views, max_layers=args.max_layers, renderer=r)
        res["seconds_load"] = round(t_load, 2)
        print(f"   load {t_load:.1f}s; scene {name}: {'PASS' if res['passed'] else 'FAIL'}")
        ok &= res["passed"]
        results.append(res)
        r.release()
    if args.json:
        d = os.path.dirname(os.path.abspath(args.json))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"renderer": gl.renderer_string(ctx), "scenes": results}, fh, indent=1)
        os.replace(tmp, args.json)
    print("VERIFY PASS" if ok else "VERIFY FAIL")
    return 0 if ok else 1


def cmd_figures(args):
    from . import gl
    from .figures import FIGURES, compare, generate

    names = [n for n in (args.only.split(",") if args.only else FIGURES) if n]
    ctx = _context(args)
    print(f"GL renderer: {gl.renderer_string(ctx)} (EGL device {gl.device_index_from_env(args.egl_device)})")
    if args.check:
        ref_dir = os.path.abspath(args.check)
        tmp = tempfile.mkdtemp(prefix="pvhv-figures-check-")        # never writes into the checked dir
        try:
            paths = generate(tmp, names, ctx)
            ok = True
            committed = {os.path.splitext(f)[0]: f for f in os.listdir(ref_dir)
                         if f.lower().endswith((".png", ".jpg", ".jpeg"))}
            for n in names:
                new = paths[n]
                old = os.path.join(ref_dir, committed[n]) if n in committed else None
                if old is None or os.path.basename(old) != os.path.basename(new):
                    print(f"{n}: MISSING in {ref_dir} (expected {os.path.basename(new)})")
                    ok = False
                    continue
                passed, identical, frac = compare(new, old)
                print(f"{n}: {'OK' if passed else 'MISMATCH'}; byte-identical: {'yes' if identical else 'no'}; "
                      f"pixels differing by > 2/255: {frac:.2e}")
                ok &= passed
            if not args.only:
                extra = sorted(set(committed) - set(names))
                for n in extra:
                    print(f"{n}: EXTRA figure in {ref_dir} ({committed[n]})")
                ok &= not extra
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print("FIGURES CHECK PASS" if ok else "FIGURES CHECK FAIL")
        return 0 if ok else 1
    if not args.out:
        print("error: --out or --check is required", file=sys.stderr)
        return 2
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=out, prefix=".tmp-figures-")
    try:
        paths = generate(tmp, names, ctx)
        for n, p in paths.items():
            for ext in (".png", ".jpg"):                                # drop a stale variant of the same figure
                stale = os.path.join(out, n + ext)
                if ext != os.path.splitext(p)[1] and os.path.exists(stale):
                    os.unlink(stale)
            os.replace(p, os.path.join(out, os.path.basename(p)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {len(paths)} figure(s) to {out}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="python -m pvhv", description=__doc__)
    p.add_argument("--egl-device", type=int, default=None,
                   help="EGL device index (default: env PVHV_EGL_DEVICE, else 0)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="print the GL renderer/version and the EGL device index")

    def peel_opts(q):
        q.add_argument("--scene", required=True)
        q.add_argument("--method", choices=("bdp", "udp", "edp"), required=True)
        q.add_argument("--edp-layers", type=_edp_layers, default=2,
                       help="hidden layers peeled with the EDP rule (int or 'all'; default 2, deeper: UDP)")
        q.add_argument("--search", choices=("sampled", "exhaustive"), default="sampled")
        q.add_argument("--max-layers", type=int, default=128)

    q = sub.add_parser("render", help="peel a scene and save per-layer PNGs and counts.json")
    peel_opts(q)
    q.add_argument("--out", required=True)
    q.add_argument("--force", action="store_true", help="replace layer_*.png/counts.json in a non-empty --out")

    q = sub.add_parser("warp", help="warp peeled layers to a novel view and save a PNG")
    peel_opts(q)
    q.add_argument("--t", type=float, nargs=2, required=True, metavar=("TX", "TY"),
                   help="lens offset in world units (source camera space)")
    q.add_argument("--layers", type=int, default=None, help="warp layers 0..N-1 only (1 = single-layer warp)")
    q.add_argument("--out", required=True)

    q = sub.add_parser("verify", help="run the verification gates")
    q.add_argument("--scenes", default="blocks,sanmiguel,bistro,chess")
    q.add_argument("--views", type=int, default=16)
    q.add_argument("--max-layers", type=int, default=128)
    q.add_argument("--json", default=None)

    q = sub.add_parser("figures", help="render the qualitative figures, or check committed ones")
    q.add_argument("--out", default=None)
    q.add_argument("--only", default=None, help="comma-separated figure names")
    q.add_argument("--check", default=None, metavar="DIR",
                   help="regenerate into a temp dir and compare with the figures in DIR (exit 1 on mismatch)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {"info": cmd_info, "render": cmd_render, "warp": cmd_warp, "verify": cmd_verify,
            "figures": cmd_figures}[args.cmd](args)

#!/usr/bin/env python
"""Compare the browser demo's dumps (demo/crosscheck.mjs) with the offline renderer.

    python demo/crosscheck.py --browser DIR [--json FILE]

Needs the pvhv environment, PVHV_MODELS_CACHE (chess) and an EGL GPU
(PVHV_EGL_DEVICE).  Validates DIR first (COMPLETE marker, per-file length and
sha256, expected scenes/views/modes, manifest identity with the working tree;
exit 2 when invalid), then peels/warps every scene offline and applies the gates
X1-X6 (demo/README.md).  Prints CROSSCHECK PASS / CROSSCHECK FAIL; exit 0 only on PASS.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "renderer"))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

MODES = ("single", "bdp", "edp")
VIEWS = [(0, 0), (1, 0), (0, 1), (-0.6, -0.6), (0.5, -0.3)]      # multiples of E (same as crosscheck.mjs)
MANIFEST = os.path.join(REPO, "docs", "assets", "demo", "manifest.json")

# gates (see demo/README.md for the justification)
X1_AGREE, X1_COUNT = 0.999, 0.001
X2_ABS, X2_REL, X2_AGREE, X2_MIN_FRAGMENTS, X2_TOTAL = 32, 0.01, 0.99, 1000, 0.01
X3_DZ, X3_FRACTION = 1e-4, 0.999
X4_AGREE = 0.99
X6_MEAN, X6_P99 = 6.0, 32.0


class Invalid(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate(root, scenes_arg):
    """Check the dump directory; returns {scene: meta}.  Raises Invalid."""
    marker = os.path.join(root, "COMPLETE")
    if not os.path.isfile(marker):
        raise Invalid(f"{marker} missing (incomplete dump)")
    with open(marker) as fh:
        complete = json.load(fh)
    scenes = scenes_arg or complete.get("scenes") or []
    if not scenes:
        raise Invalid("no scenes listed")
    with open(MANIFEST, "rb") as fh:
        manifest_sha = hashlib.sha256(fh.read()).hexdigest()
    metas = {}
    for scene in scenes:
        mpath = os.path.join(root, scene, "meta.json")
        if not os.path.isfile(mpath):
            raise Invalid(f"{mpath} missing")
        with open(mpath) as fh:
            meta = json.load(fh)
        if meta.get("manifest_sha256") != manifest_sha:
            raise Invalid(f"{scene}: dump was made from another docs/assets/demo/manifest.json "
                          f"({meta.get('manifest_sha256')} != {manifest_sha})")
        listed = {}
        for f in meta["files"]:
            p = os.path.join(root, scene, f["name"])
            if not os.path.isfile(p) or os.path.getsize(p) != f["bytes"] or sha256_file(p) != f["sha256"]:
                raise Invalid(f"{scene}/{f['name']}: missing or length/sha256 mismatch")
            listed[f["name"]] = f
        if [tuple(v["multiple"]) for v in meta["views"]] != [tuple(float(x) for x in v) for v in VIEWS]:
            raise Invalid(f"{scene}: unexpected views {meta['views']}")
        if list(meta["modes"]) != list(MODES):
            raise Invalid(f"{scene}: unexpected modes {meta['modes']}")
        W, H, Wg, Hg = meta["W"], meta["H"], meta["Wg"], meta["Hg"]
        need = {"layer0-color.bin": 4 * Wg * Hg}
        for method in ("bdp", "edp"):
            for h in range(len(meta["counts"][method])):
                need[f"layer-{method}-{h}-item.bin"] = 4 * Wg * Hg
                need[f"layer-{method}-{h}-depth.bin"] = 4 * Wg * Hg
        for v in meta["views"]:
            for mode in MODES:
                for k in ("item", "depth", "layer"):
                    need[f"warp-{mode}-{v['name']}-{k}.bin"] = 4 * W * H
        for name, size in need.items():
            if name not in listed or listed[name]["bytes"] != size:
                raise Invalid(f"{scene}/{name}: missing or wrong size")
        metas[scene] = meta
    return metas


def load(root, scene, name, dtype, shape):
    return np.fromfile(os.path.join(root, scene, name), dtype=np.dtype(dtype).newbyteorder("<")).reshape(shape)


def agreement(a, b):
    """Fraction of the union of occupied pixels (either item != 0) with equal item."""
    union = (a != 0) | (b != 0)
    n = int(union.sum())
    return (float(((a == b) & union).sum()) / n if n else 1.0), n


def depth_fraction(item_a, z_a, item_b, z_b, span):
    """Fraction of pixels with equal non-zero item whose |dz|/(far-near) <= X3_DZ, and the max error."""
    m = (item_a == item_b) & (item_a != 0)
    n = int(m.sum())
    if not n:
        return 1.0, 0.0, 0
    dz = np.abs(z_a[m].astype(np.float64) - z_b[m].astype(np.float64)) / span
    return float((dz <= X3_DZ).mean()), float(dz.max()), n


def compare_scene(root, scene, meta, ctx, gates):
    from export_scene import canonical_batches
    from pvhv.peel import SceneRenderer
    from pvhv.scene import load_scene
    from pvhv.warp import LayerWarper

    sc = canonical_batches(load_scene(scene, width=meta["W"], height=meta["H"]))
    cam = sc.camera
    if (cam.W, cam.H, cam.Wg, cam.Hg) != (meta["W"], meta["H"], meta["Wg"], meta["Hg"]):
        raise Invalid(f"{scene}: camera sizes differ from the dump")
    W, H, Wg, Hg = cam.W, cam.H, cam.Wg, cam.Hg
    span = float(cam.far - cam.near)
    gate = lambda name, ok, detail: gates.append({"scene": scene, "gate": name, "ok": bool(ok), "detail": detail})
    report = {"scene": scene, "renderer_browser": meta["renderer"], "layers": {}, "warps": {}}
    r = SceneRenderer(sc, ctx)
    try:
        py = {m: r.peel(m, edp_layers=2, max_layers=128, search="sampled") for m in ("bdp", "edp")}
        br = {}
        for m in ("bdp", "edp"):
            br[m] = [(load(root, scene, f"layer-{m}-{h}-item.bin", np.uint32, (Hg, Wg)),
                      load(root, scene, f"layer-{m}-{h}-depth.bin", np.float32, (Hg, Wg)))
                     for h in range(len(meta["counts"][m]))]
        print(f"\n== {scene}: browser '{meta['renderer']}' vs offline")
        print(f"{'method':6} {'h':>2} {'python':>8} {'browser':>8} {'delta':>6} {'agree':>8}")

        # X1 layer 0
        bi0, bz0 = br["bdp"][0]
        pi0, pz0 = py["bdp"].item[0], py["bdp"].depth[0]
        a0, _ = agreement(pi0, bi0)
        c_p, c_b = int(np.count_nonzero(pi0)), int(np.count_nonzero(bi0))
        print(f"{'both':6} {0:>2} {c_p:>8} {c_b:>8} {c_b - c_p:>+6} {a0:>8.5f}")
        gate("X1 layer0 item agreement", a0 >= X1_AGREE, f"{a0:.6f} (>= {X1_AGREE})")
        gate("X1 layer0 count", abs(c_b - c_p) <= X1_COUNT * c_p, f"python {c_p} browser {c_b}")
        same0 = np.array_equal(br["edp"][0][0], bi0) and br["edp"][0][1].tobytes() == bz0.tobytes()
        gate("X1 browser layer0 shared by bdp/edp", same0, "identical arrays")
        report["layers"]["layer0"] = {"python": c_p, "browser": c_b, "agreement": a0}

        # X2 hidden layers
        hidden = {}
        for m in ("bdp", "edp"):
            npl, nbl = py[m].n_layers, len(br[m])
            rows = []
            for h in range(1, max(npl, nbl)):
                cp = int(py[m].counts[h]) if h < npl else 0
                cb = int(np.count_nonzero(br[m][h][0])) if h < nbl else 0
                ag = agreement(py[m].item[h], br[m][h][0])[0] if (h < npl and h < nbl) else None
                print(f"{m:6} {h:>2} {cp:>8} {cb:>8} {cb - cp:>+6} " + (f"{ag:>8.5f}" if ag is not None else f"{'-':>8}"))
                rows.append({"h": h, "python": cp, "browser": cb, "agreement": ag})
                gate(f"X2 {m} layer {h} count", abs(cb - cp) <= max(X2_ABS, X2_REL * cp), f"python {cp} browser {cb}")
                if cp >= X2_MIN_FRAGMENTS:
                    gate(f"X2 {m} layer {h} item agreement", ag is not None and ag >= X2_AGREE,
                         f"{ag} (>= {X2_AGREE})")
            tp, tb = sum(r_["python"] for r_ in rows), sum(r_["browser"] for r_ in rows)
            hidden[m] = (tp, tb)
            gate(f"X2 {m} total hidden", abs(tb - tp) <= X2_TOTAL * tp, f"python {tp} browser {tb}")
            gate(f"X2 {m} layer count", abs(npl - nbl) <= 1, f"python {npl} browser {nbl}")
            conv_b = bool(meta["converged"][m])
            gate(f"X2 {m} converged", py[m].converged and conv_b, f"python {py[m].converged} browser {conv_b}")
            report["layers"][m] = {"rows": rows, "python_layers": npl, "browser_layers": nbl,
                                   "python_converged": py[m].converged, "browser_converged": conv_b}

        # X5
        print(f"hidden fragments: python BDP {hidden['bdp'][0]} EDP {hidden['edp'][0]}; "
              f"browser BDP {hidden['bdp'][1]} EDP {hidden['edp'][1]}")
        gate("X5 EDP < BDP hidden (python)", hidden["edp"][0] < hidden["bdp"][0], f"{hidden['edp'][0]} < {hidden['bdp'][0]}")
        gate("X5 EDP < BDP hidden (browser)", hidden["edp"][1] < hidden["bdp"][1], f"{hidden['edp'][1]} < {hidden['bdp'][1]}")

        # X3 layer-0 depth
        fr, mx, n = depth_fraction(pi0, pz0, bi0, bz0, span)
        gate("X3 layer0 depth", fr >= X3_FRACTION, f"{fr:.6f} of {n} px within {X3_DZ} (max {mx:.2e})")

        # X6 colour of layer 0
        bc0 = load(root, scene, "layer0-color.bin", np.uint8, (Hg, Wg, 4))
        m = (pi0 == bi0) & (pi0 != 0)
        d = np.abs(py["bdp"].color[0][m][:, :3].astype(np.int16) - bc0[m][:, :3].astype(np.int16))
        mean = d.mean(axis=0)
        p99 = np.percentile(d, 99, axis=0)
        print(f"layer0 colour |dRGB| mean {np.round(mean, 3).tolist()} p99 {p99.tolist()} (sRGB levels)")
        gate("X6 layer0 colour mean", bool(np.all(mean <= X6_MEAN)), f"{np.round(mean, 3).tolist()} (<= {X6_MEAN})")
        gate("X6 layer0 colour p99", bool(np.all(p99 <= X6_P99)), f"{p99.tolist()} (<= {X6_P99})")
        report["colour"] = {"mean": mean.tolist(), "p99": p99.tolist()}

        # X4 warps (+ X3 on warps)
        warpers = {m: LayerWarper(py[m], ctx) for m in ("bdp", "edp")}
        try:
            print(f"{'mode':6} {'view':4} {'t':>24} {'item':>8} {'layer':>8} {'depth ok':>9} {'max dz':>9}")
            for v in meta["views"]:
                t = (float(v["t"][0]), float(v["t"][1]))
                for mode in MODES:
                    wb_item = load(root, scene, f"warp-{mode}-{v['name']}-item.bin", np.uint32, (H, W))
                    wb_depth = load(root, scene, f"warp-{mode}-{v['name']}-depth.bin", np.float32, (H, W))
                    wb_layer = load(root, scene, f"warp-{mode}-{v['name']}-layer.bin", np.uint32, (H, W))
                    wp = (warpers["bdp"].warp(t, n_layers=1) if mode == "single"
                          else warpers[mode].warp(t))
                    ai, _ = agreement(wp.item, wb_item)
                    union = (wp.layer != 0) | (wb_layer != 0)
                    al = float(((wp.layer == wb_layer) & union).sum()) / max(1, int(union.sum()))
                    fr, mx, n = depth_fraction(wp.item, wp.depth, wb_item, wb_depth, span)
                    print(f"{mode:6} {v['name']:4} {str(tuple(round(x, 5) for x in t)):>24} {ai:>8.5f} {al:>8.5f} "
                          f"{fr:>9.6f} {mx:>9.2e}")
                    report["warps"][f"{mode}/{v['name']}"] = {"t": t, "item_agreement": ai, "layer_agreement": al,
                                                             "depth_fraction": fr, "max_dz": mx}
                    gate(f"X3 warp {mode}/{v['name']} depth", fr >= X3_FRACTION, f"{fr:.6f} (max {mx:.2e})")
                    if v["multiple"] == [0, 0]:
                        oy, ox = cam.oy, cam.ox
                        ok = (np.array_equal(wb_item, bi0[oy:oy + H, ox:ox + W])
                              and wb_depth.tobytes() == np.ascontiguousarray(bz0[oy:oy + H, ox:ox + W]).tobytes())
                        gate(f"X4 {mode} t=0 identity (browser warp = crop of browser layer 0)", ok, "item and depth bits")
                    else:
                        gate(f"X4 warp {mode}/{v['name']} item agreement", ai >= X4_AGREE, f"{ai:.6f} (>= {X4_AGREE})")
        finally:
            for w in warpers.values():
                w.release()
    finally:
        r.release()
    return report


def write_json_atomic(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".crosscheck-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--browser", required=True, help="directory written by demo/crosscheck.mjs")
    ap.add_argument("--json", help="write the full result as JSON (atomically)")
    ap.add_argument("--scenes", help="comma-separated scenes (default: the scenes in COMPLETE)")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.browser)
    try:
        metas = validate(root, [s for s in (args.scenes or "").split(",") if s])
    except (Invalid, OSError, ValueError, KeyError) as exc:
        print(f"crosscheck: invalid browser dump: {exc}")
        return 2
    from pvhv import gl
    ctx = gl.create_context()
    print(f"offline renderer: {gl.renderer_string(ctx)}")
    gates, reports = [], []
    try:
        for scene, meta in metas.items():
            reports.append(compare_scene(root, scene, meta, ctx, gates))
    except Invalid as exc:
        print(f"crosscheck: invalid browser dump: {exc}")
        return 2
    failed = [g for g in gates if not g["ok"]]
    print("\ngates:")
    for g in gates:
        if not g["ok"] or g["gate"].startswith(("X1", "X5", "X6")) or "total" in g["gate"]:
            print(f"  {'ok  ' if g['ok'] else 'FAIL'} {g['scene']:6} {g['gate']}: {g['detail']}")
    print(f"  ({len(gates) - len(failed)}/{len(gates)} gates passed)")
    verdict = "CROSSCHECK PASS" if not failed else "CROSSCHECK FAIL"
    if args.json:
        write_json_atomic(args.json, {"verdict": verdict, "offline_renderer": gl.renderer_string(ctx),
                                      "gates": gates, "scenes": reports})
    browsers = ", ".join(sorted({m["renderer"] or "unknown" for m in metas.values()}))
    print(f"{verdict} (browser: {browsers})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

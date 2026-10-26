"""Fragment counts, warp comparisons, false-negative fractions and the verify gates
(plan section H and *Verification*)."""
import time

import numpy as np

G2_MAX = 5e-4
G2_MEAN = 1e-4
G3_MIN = 1e-3
FN_DEPTH_TOL = 0.01
CORRECT_DEPTH_TOL = 0.01


def verify_views(E, n=16):
    """Deterministic lens positions: n/2 on the bound |t| = E at angles 2*pi*k/(n/2) + pi/(n/2),
    then n/2 at |t| = E/2 at angles 2*pi*k/(n/2).  For n = 16 this is the plan's view set."""
    nb = n // 2
    ni = n - nb
    views = [(E * np.cos(2 * np.pi * k / nb + np.pi / nb), E * np.sin(2 * np.pi * k / nb + np.pi / nb))
             for k in range(nb)]
    views += [(0.5 * E * np.cos(2 * np.pi * k / ni), 0.5 * E * np.sin(2 * np.pi * k / ni)) for k in range(ni)]
    return [(float(a), float(b)) for a, b in views], nb


def warp_difference(a, b):
    """D_v: fraction of pixels where two warps differ in item, depth bits or color."""
    diff = (a.item != b.item) | (a.depth.view(np.uint32) != b.depth.view(np.uint32)) | \
        np.any(a.color != b.color, axis=-1)
    return float(np.count_nonzero(diff)) / diff.size


def warp_correct(w, ref, camera):
    """Per-pixel correctness of a warp w.r.t. the direct reference render: same item and
    |zn_warp - zn_ref| <= 0.01 where the reference has geometry, a hole where it has none."""
    geo = ref["item"] != 0
    dz = np.abs(camera.normalize_depth(w.depth) - camera.normalize_depth(ref["depth"]))
    return np.where(geo, (w.item == ref["item"]) & (dz <= CORRECT_DEPTH_TOL), w.layer == 0)


def warp_loss(a, b, ref, camera):
    """L_v: fraction of pixels where warp ``a`` (BDP) is correct and warp ``b`` (EDP) is not.
    Swap the arguments for the symmetric count L_rev."""
    lost = warp_correct(a, ref, camera) & ~warp_correct(b, ref, camera)
    return float(np.count_nonzero(lost)) / lost.size


def hidden_fill(w):
    """Hf_v: fraction of pixels whose winner comes from a hidden layer (1-based layer >= 2)."""
    return float(np.count_nonzero(w.layer >= 2)) / w.layer.size


def fn_fraction(ref, layers, t):
    """Sec. 5.1.5 false negatives: reference pixels (item != 0) that no layer fragment
    (same item, |zn diff| <= 0.01, in the 2x2 source pixels around the reprojected
    point) explains.  Returns (fraction of W*H pixels, count)."""
    cam = layers.camera
    H, W = ref["item"].shape
    jj, ii = np.nonzero(ref["item"])
    if not ii.size:
        return 0.0, 0
    z = ref["depth"][jj, ii].astype(np.float64)
    item = ref["item"][jj, ii]
    u, v = cam.unwarp_pixels(ii + 0.5, jj + 0.5, z, t)
    i0 = np.floor(u - 0.5).astype(np.int64)
    j0 = np.floor(v - 0.5).astype(np.int64)
    zn_ref = cam.normalize_depth(z.astype(np.float32))
    Wg, Hg = cam.source_size
    todo = np.arange(ii.size)
    for li in range(layers.n_layers):
        if not todo.size:
            break
        L_item, L_depth = layers.item[li], layers.depth[li]
        hit = np.zeros(todo.size, bool)
        for di in (0, 1):
            for dj in (0, 1):
                x, y = i0[todo] + di, j0[todo] + dj
                ok = (x >= 0) & (y >= 0) & (x < Wg) & (y < Hg)
                xc, yc = np.clip(x, 0, Wg - 1), np.clip(y, 0, Hg - 1)
                zn = cam.normalize_depth(L_depth[yc, xc])
                hit |= ok & (L_item[yc, xc] == item[todo]) & (np.abs(zn - zn_ref[todo]) <= FN_DEPTH_TOL)
        todo = todo[~hit]
    return float(todo.size) / (W * H), int(todo.size)


def fragments_absent(a, b):
    """Number of hidden fragments of ``a`` (layers >= 1) with no fragment of ``b`` at the
    same pixel with the same item and bit-identical depth."""
    absent = 0
    for h in range(1, a.n_layers):
        m = a.item[h] != 0
        found = np.zeros_like(m)
        for k in range(b.n_layers):
            found |= (b.item[k] == a.item[h]) & (b.depth[k].view(np.uint32) == a.depth[h].view(np.uint32))
        absent += int(np.count_nonzero(m & ~found))
    return absent


def boundary_parallax(layers, E):
    """Screen-space parallax F*E*(1/z_near - 1/z_far) across 4-neighbour depth/item
    discontinuities of layer 0 (both sides non-empty).  Returns (max, p99) in pixels."""
    cam = layers.camera
    d, it = layers.depth[0].astype(np.float64), layers.item[0]
    vals = []
    for a, b, ia, ib in ((d[:, :-1], d[:, 1:], it[:, :-1], it[:, 1:]), (d[:-1], d[1:], it[:-1], it[1:])):
        m = (ia != 0) & (ib != 0) & ((ia != ib) | (np.abs(cam.normalize_depth(a) - cam.normalize_depth(b)) > 0.002))
        zn, zf = np.minimum(a[m], b[m]), np.maximum(a[m], b[m])
        vals.append(cam.F * E * (1.0 / zn - 1.0 / zf))
    v = np.concatenate(vals)
    if not v.size:
        return 0.0, 0.0
    return float(v.max()), float(np.percentile(v, 99))


def verify_scene(scene, n_views=16, max_layers=128, renderer=None, log=print):
    """Run the verify gates on one loaded scene; returns a result dict (``passed`` key)."""
    from .peel import get_renderer
    from .warp import LayerWarper

    t0 = time.perf_counter()
    r = renderer or get_renderer(scene)
    cam = scene.camera
    px = cam.Wg * cam.Hg
    peeled = {}
    for m in ("bdp", "udp", "edp"):
        peeled[m] = r.peel(m, edp_layers=2, max_layers=max_layers, search="sampled")
    t_peel = time.perf_counter() - t0
    bdp, edp = peeled["bdp"], peeled["edp"]
    edp_x = r.peel("edp", edp_layers=2, max_layers=max_layers, search="exhaustive")   # informational
    wb, we, wx = LayerWarper(bdp, r.ctx), LayerWarper(edp, r.ctx), LayerWarper(edp_x, r.ctx)
    views, nb = verify_views(scene.E, n_views)
    rows = []
    for k, t in enumerate(views):
        a, b, x = wb.warp(t), we.warp(t), wx.warp(t)
        ref = r.reference(t)
        rows.append({"t": t, "boundary": k < nb, "L": warp_loss(a, b, ref, cam), "L_rev": warp_loss(b, a, ref, cam),
                     "D": warp_difference(b, a), "L_exh": warp_loss(a, x, ref, cam), "D_exh": warp_difference(x, a),
                     "Hf": hidden_fill(a),
                     "holes_bdp": float(np.count_nonzero(a.layer == 0)) / a.layer.size,
                     "FN_single": fn_fraction(ref, bdp.head(1), t)[0],
                     "FN_bdp": fn_fraction(ref, bdp, t)[0], "FN_edp": fn_fraction(ref, edp, t)[0]})
    for w in (wb, we, wx):
        w.release()
    col = {k: np.array([row[k] for row in rows]) for k in ("L", "L_rev", "D", "L_exh", "D_exh")}
    L, D = col["L"], col["D"]
    Hf_b = np.array([row["Hf"] for row in rows if row["boundary"]])
    g1 = edp.hidden_count < bdp.hidden_count
    g2 = bool(L.max() <= G2_MAX and L.mean() <= G2_MEAN)
    g3 = bool(Hf_b.mean() >= G3_MIN)
    conv = all(p.converged for p in peeled.values())
    pmax, p99 = boundary_parallax(bdp, scene.E)
    res = {
        "scene": scene.name, "W": cam.W, "H": cam.H, "Wg": cam.Wg, "Hg": cam.Hg, "E": scene.E,
        "near": cam.near, "far": cam.far, "samples": scene.samples, "triangles": scene.n_triangles,
        "items": scene.n_items, "unresolved_textures": len(set(scene.unresolved_textures)),
        "boundary_parallax_px_max": pmax, "boundary_parallax_px_p99": p99,
        "methods": {m: p.summary() for m, p in peeled.items()},
        "edp_exhaustive": edp_x.summary(),
        "edp_fragments_absent_from_bdp": fragments_absent(edp, bdp),
        "views": rows,
        "L_max": float(L.max()), "L_mean": float(L.mean()),
        "L_rev_max": float(col["L_rev"].max()), "L_rev_mean": float(col["L_rev"].mean()),
        "D_max": float(D.max()), "D_mean": float(D.mean()),
        "exhaustive": {"L_max": float(col["L_exh"].max()), "L_mean": float(col["L_exh"].mean()),
                       "D_mean": float(col["D_exh"].mean())},
        "Hf_boundary_mean": float(Hf_b.mean()),
        "Hf_mean": float(np.mean([row["Hf"] for row in rows])),
        "FN_mean": {k: float(np.mean([row[k] for row in rows])) for k in ("FN_single", "FN_bdp", "FN_edp")},
        "FN_max": {k: float(np.max([row[k] for row in rows])) for k in ("FN_single", "FN_bdp", "FN_edp")},
        "gates": {"G1": bool(g1), "G2": g2, "G3": g3, "converged": conv},
        "seconds_peel": round(t_peel, 2), "seconds_total": round(time.perf_counter() - t0, 2),
    }
    res["passed"] = bool(g1 and g2 and g3 and conv)
    if log:
        log(format_scene(res, px))
    return res


def format_scene(res, px=None):
    px = px or res["Wg"] * res["Hg"]
    out = [f"== scene {res['scene']}: {res['W']}x{res['H']} (source {res['Wg']}x{res['Hg']}), E={res['E']}, "
           f"near={res['near']}, far={res['far']}, |Omega|={res['samples']}, {res['triangles']} triangles, "
           f"{res['items']} items, unresolved textures: {res['unresolved_textures']}",
           f"   boundary parallax at |t|=E: max {res['boundary_parallax_px_max']:.1f} px, "
           f"p99 {res['boundary_parallax_px_p99']:.1f} px",
           f"   {'method':<6} {'layers':>6} {'converged':>9} {'hidden frags':>13} {'fraction':>9}  per-layer counts"]
    for m in ("bdp", "udp", "edp"):
        s = res["methods"][m]
        counts = s["counts"]
        cs = ", ".join(str(c) for c in counts[:8]) + (", ..." if len(counts) > 8 else "")
        out.append(f"   {m.upper():<6} {s['layers']:>6} {str(s['converged']):>9} {s['hidden_fragments']:>13} "
                   f"{s['hidden_fraction']:>9.4f}  [{cs}]  ({s['seconds']:.2f}s)")
    out.append(f"   EDP hidden fragments absent from BDP: {res['edp_fragments_absent_from_bdp']}")
    out.append(f"   {'view':>4} {'tx':>8} {'ty':>8} {'L_v':>10} {'L_rev':>10} {'D_v':>10} {'Hf_v':>8} {'holes':>8} "
               f"{'FN single':>10} {'FN BDP':>10} {'FN EDP':>10}")
    for k, row in enumerate(res["views"]):
        out.append(f"   {k:>4} {row['t'][0]:>8.4f} {row['t'][1]:>8.4f} {row['L']:>10.2e} {row['L_rev']:>10.2e} "
                   f"{row['D']:>10.2e} {row['Hf']:>8.4f} "
                   f"{row['holes_bdp']:>8.4f} {row['FN_single']:>10.2e} {row['FN_bdp']:>10.2e} "
                   f"{row['FN_edp']:>10.2e}")
    g = res["gates"]
    x = res["exhaustive"]
    out.append(f"   G2 metric L_v (BDP warp correct, EDP warp not): max {res['L_max']:.2e} (<= {G2_MAX:g}), "
               f"mean {res['L_mean']:.2e} (<= {G2_MEAN:g}); L_rev (EDP correct, BDP not): max "
               f"{res['L_rev_max']:.2e}, mean {res['L_rev_mean']:.2e}")
    out.append(f"   informational: D_v max {res['D_max']:.2e}, mean {res['D_mean']:.2e}; "
               f"Hf mean over boundary views {res['Hf_boundary_mean']:.4f} (>= {G3_MIN:g}), all views "
               f"{res['Hf_mean']:.4f}")
    out.append(f"   exactness (EDP paper config, EXHAUSTIVE search, {res['edp_exhaustive']['hidden_fragments']} hidden "
               f"fragments; not gated): L_v max {x['L_max']:.2e}, mean {x['L_mean']:.2e}; D_v mean {x['D_mean']:.2e}")
    out.append(f"   FN mean: single {res['FN_mean']['FN_single']:.2e}, BDP {res['FN_mean']['FN_bdp']:.2e}, "
               f"EDP {res['FN_mean']['FN_edp']:.2e}")
    out.append(f"   gates: G1 {'PASS' if g['G1'] else 'FAIL'}, G2 {'PASS' if g['G2'] else 'FAIL'}, "
               f"G3 {'PASS' if g['G3'] else 'FAIL'}, converged {'PASS' if g['converged'] else 'FAIL'}; "
               f"time peel {res['seconds_peel']}s, total {res['seconds_total']}s")
    return "\n".join(out)

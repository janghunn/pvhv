"""Qualitative figures (``python -m pvhv figures``).

Every figure is a grid of panels with a small title above each panel and a footer
line carrying the re-implementation label.  Panels are downscaled from the scene
resolution with Lanczos filtering.  Numbers drawn into panels are measured by this
re-implementation for that view.  Module-level imports stay GPU-free so the README
test can import the figure list.
"""
import os

import numpy as np

LABEL = "Re-implementation (2026), not the code used for the paper's experiments"
CITATION = ("Janghun Kim and Sungkil Lee. Potentially Visible Hidden-Volume Rendering for Multi-View Warping. "
            "ACM Transactions on Graphics 42(4) (Proc. SIGGRAPH 2023). https://doi.org/10.1145/3592108")

# figure name -> scenes it renders (in order); the README test checks attribution against this
FIGURE_SCENES = {
    "keep_cull": ["sanmiguel", "bistro"],
    "novel_view": ["sanmiguel", "bistro"],
    "layers": ["chess"],
}
FIGURES = tuple(FIGURE_SCENES)
SCENE_TITLES = {"sanmiguel": "San Miguel", "bistro": "Bistro", "chess": "Chess set", "blocks": "Blocks"}

EMPTY = np.array([0xF2, 0xA6, 0x5A], np.uint8)    # orange: empty / culled
HOLE = np.array([0xFF, 0x00, 0xFF], np.uint8)     # magenta: warp hole
CULL_TINT = 0.6                                   # culled fragments: mix toward orange by this weight

GRID_PANEL = (480, 270)
MAX_BYTES = 1_048_576
TITLE_SIZE = 15
FOOTER_SIZE = 13
PAD = 6


def _font(size):
    from PIL import ImageFont
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------------------------------
# panel helpers (inputs are GL-orientation arrays: row 0 = bottom)

def _crop(cam, a):
    """Central W x H region of a source (guard-band) array."""
    return a[cam.oy:cam.oy + cam.H, cam.ox:cam.ox + cam.W]


def _to_image(rgb_gl, size):
    from PIL import Image
    im = Image.fromarray(np.ascontiguousarray(rgb_gl[::-1]))
    return im if im.size == tuple(size) else im.resize(size, Image.LANCZOS)


def _layer_rgb(cam, layers, h):
    rgb = _crop(cam, layers.color[h])[..., :3].copy()
    item = _crop(cam, layers.item[h])
    rgb[item == 0] = EMPTY
    return rgb, float(np.count_nonzero(item)) / item.size


def _pct(x):
    return f"{100.0 * x:.1f}%" if x >= 0.001 else f"{100.0 * x:.2f}%"


def _grid(rows, panel, footer=LABEL):
    """rows: list of lists of (title, PIL image of size ``panel``) -> PIL image."""
    from PIL import Image, ImageDraw
    tf, ff = _font(TITLE_SIZE), _font(FOOTER_SIZE)
    title_h = TITLE_SIZE + 8
    ncol = max(len(r) for r in rows)
    W = PAD + ncol * (panel[0] + PAD)
    H = PAD + len(rows) * (title_h + panel[1] + PAD) + FOOTER_SIZE + 8
    out = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(out)
    y = PAD
    for row in rows:
        x = PAD
        for title, im in row:
            d.text((x + 2, y + 3), title, fill=(20, 20, 20), font=tf)
            out.paste(im, (x, y + title_h))
            x += panel[0] + PAD
        y += title_h + panel[1] + PAD
    d.text((PAD + 2, y), footer, fill=(60, 60, 60), font=ff)
    return out


def _save(img, out_dir, name):
    """PNG if it fits in 1 MiB, else JPEG quality 92 with 4:4:4 chroma.  Returns the path."""
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data, ext = buf.getvalue(), ".png"
    if len(data) > MAX_BYTES:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, subsampling=0, optimize=True)
        data, ext = buf.getvalue(), ".jpg"
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"figure {name} exceeds {MAX_BYTES} bytes even as JPEG")
    path = os.path.join(out_dir, name + ext)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ---------------------------------------------------------------------------------------
# scene state shared between figures

class _SceneCache:
    def __init__(self, ctx):
        self.ctx = ctx
        self.data = {}

    def get(self, name):
        if name not in self.data:
            from .peel import SceneRenderer
            from .scene import load_scene
            scene = load_scene(name)
            if scene.unresolved_textures:
                raise RuntimeError(f"scene {name}: unresolved textures {sorted(set(scene.unresolved_textures))}")
            r = SceneRenderer(scene, self.ctx)
            peeled = {m: r.peel(m, edp_layers=2, max_layers=128, search="sampled") for m in ("bdp", "udp", "edp")}
            for m, p in peeled.items():
                if not p.converged:
                    raise RuntimeError(f"scene {name}: {m} did not converge")
            self.data[name] = (scene, r, peeled)
        return self.data[name]

    def release(self):
        for _, r, _ in self.data.values():
            r.release()
        self.data = {}


# ---------------------------------------------------------------------------------------
# figures

def fig_keep_cull(cache):
    rows = []
    for name in FIGURE_SCENES["keep_cull"]:
        scene, r, peeled = cache.get(name)
        cam = scene.camera
        bdp, edp, udp = peeled["bdp"], peeled["edp"], peeled["udp"]
        l0, f0 = _layer_rgb(cam, bdp, 0)
        item1, depth1 = bdp.item[1], bdp.depth[1].view(np.uint32)
        kept = np.zeros(item1.shape, bool)
        for k in range(edp.n_layers):
            kept |= (edp.item[k] == item1) & (edp.depth[k].view(np.uint32) == depth1)
        kept &= item1 != 0
        rgb = _crop(cam, bdp.color[1])[..., :3].astype(np.float64)
        culled = _crop(cam, (item1 != 0) & ~kept)
        rgb[culled] = (1.0 - CULL_TINT) * rgb[culled] + CULL_TINT * EMPTY
        rgb = np.round(rgb).astype(np.uint8)
        rgb[_crop(cam, item1) == 0] = EMPTY
        fk = float(np.count_nonzero(_crop(cam, kept))) / (cam.W * cam.H)
        fb = float(np.count_nonzero(_crop(cam, item1))) / (cam.W * cam.H)
        e1, fe = _layer_rgb(cam, edp, 1)
        u1, fu = _layer_rgb(cam, udp, 1)
        st, size = SCENE_TITLES[name], GRID_PANEL
        rows.append([
            (f"{st}: source view h=0 ({_pct(f0)} non-empty)", _to_image(l0, size)),
            (f"BDP h=1: {_pct(fb)}; kept by EDP {_pct(fk)} (orange tint = culled)", _to_image(rgb, size)),
            (f"EDP h=1: {_pct(fe)} non-empty", _to_image(e1, size)),
            (f"UDP h=1: {_pct(fu)} non-empty", _to_image(u1, size)),
        ])
    return _grid(rows, GRID_PANEL)


def _box_sum(mask, h, w):
    c = np.pad(np.cumsum(np.cumsum(mask.astype(np.int64), 0), 1), ((1, 0), (1, 0)))
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


def fig_novel_view(cache):
    from PIL import Image, ImageDraw
    from .metrics import verify_views
    from .warp import LayerWarper
    size = GRID_PANEL
    rows = []
    for name in FIGURE_SCENES["novel_view"]:
        scene, r, peeled = cache.get(name)
        cam = scene.camera
        bdp, edp = peeled["bdp"], peeled["edp"]
        wb, we = LayerWarper(bdp, r.ctx), LayerWarper(edp, r.ctx)
        views, nb = verify_views(scene.E, 16)
        # the boundary view whose single-layer warp has the most disocclusion holes
        best = None
        for k, t in enumerate(views[:nb]):
            ref = r.reference(t)
            single = wb.warp(t, n_layers=1)
            n = int(np.count_nonzero((single.layer == 0) & (ref["item"] != 0)))
            if best is None or n > best[0]:
                best = (n, k, t)
        _, k, t = best
        ref = r.reference(t)
        warps = [("single-layer warp", wb.warp(t, n_layers=1)), ("full BDP warp", wb.warp(t)),
                 ("EDP warp (paper configuration)", we.warp(t))]
        wb.release()
        we.release()
        geo = ref["item"] != 0
        bg = np.round(255.0 * np.clip(np.asarray(scene.background, np.float64), 0, 1) ** (1 / 2.2)).astype(np.uint8)
        ref_rgb = ref["color"][..., :3].copy()
        ref_rgb[~geo] = bg
        panels_full = [ref_rgb]
        fracs = []
        for _, w in warps:
            rgb = w.color[..., :3].copy()
            hole = (w.layer == 0) & geo
            rgb[(w.layer == 0) & ~geo] = bg
            rgb[hole] = HOLE
            panels_full.append(rgb)
            fracs.append(float(np.count_nonzero(hole)) / hole.size)
        # inset: the window (1/5 of the image) with the most single-layer holes, shown enlarged
        hole1 = ((warps[0][1].layer == 0) & geo)[::-1]                    # top-down rows
        wh, ww = cam.H // 5, cam.W // 5
        s = _box_sum(hole1, wh, ww)
        y0, x0 = np.unravel_index(int(np.argmax(s)), s.shape)
        sx, sy = size[0] / cam.W, size[1] / cam.H
        inset = (size[0] // 2, size[1] // 2)
        in_br = x0 * sx + ww * sx > size[0] - inset[0] and y0 * sy + wh * sy > size[1] - inset[1]
        pos = (2, 2) if in_br else (size[0] - inset[0] - 2, size[1] - inset[1] - 2)
        row = []
        angle = np.degrees(np.arctan2(t[1], t[0])) % 360.0
        titles = [f"{SCENE_TITLES[name]}: direct render, |t| = E at {angle:.1f} deg"]
        titles += [f"{title}: holes {_pct(f)}" for (title, _), f in zip(warps, fracs)]
        for title, rgb in zip(titles, panels_full):
            im = _to_image(rgb, size)
            full = Image.fromarray(np.ascontiguousarray(rgb[::-1]))
            zoom = full.crop((x0, y0, x0 + ww, y0 + wh)).resize(inset, Image.LANCZOS)
            d = ImageDraw.Draw(im)
            box = (int(x0 * sx), int(y0 * sy), int((x0 + ww) * sx), int((y0 + wh) * sy))
            d.rectangle(box, outline=(255, 255, 255), width=2)
            im.paste(zoom, pos)
            d.rectangle((pos[0] - 1, pos[1] - 1, pos[0] + inset[0], pos[1] + inset[1]), outline=(255, 255, 255),
                        width=2)
            row.append((title, im))
        rows.append(row)
    return _grid(rows, size)


def fig_layers(cache):
    name = FIGURE_SCENES["layers"][0]
    scene, r, peeled = cache.get(name)
    cam, size = scene.camera, GRID_PANEL
    rows = []
    for m in ("bdp", "edp"):
        row = []
        for h in range(4):
            layers = peeled[m]
            if h < layers.n_layers:
                rgb, f = _layer_rgb(cam, layers, h)
            else:
                rgb, f = np.tile(EMPTY, (cam.H, cam.W, 1)), 0.0
            what = "z-buffer" if h == 0 else f"hidden layer, {layers.modes[h] if h < layers.n_layers else 'empty'}"
            row.append((f"{SCENE_TITLES[name]} {m.upper()} h={h} ({what}): {_pct(f)} non-empty",
                        _to_image(rgb, size)))
        rows.append(row)
    return _grid(rows, size)


BUILDERS = {"keep_cull": fig_keep_cull, "novel_view": fig_novel_view, "layers": fig_layers}


def generate(out_dir, names=None, ctx=None, log=print):
    """Render the named figures (default: all) into out_dir; returns {name: path}."""
    from . import gl
    names = list(names or FIGURES)
    unknown = [n for n in names if n not in BUILDERS]
    if unknown:
        raise ValueError(f"unknown figure(s) {unknown}; choose from {FIGURES}")
    cache = _SceneCache(ctx or gl.create_context())
    paths = {}
    try:
        for n in names:
            img = BUILDERS[n](cache)
            paths[n] = _save(img, out_dir, n)
            if log:
                log(f"{n}: {os.path.basename(paths[n])} {img.size[0]}x{img.size[1]}, "
                    f"{os.path.getsize(paths[n])} bytes")
    finally:
        cache.release()
    return paths


def compare(a_path, b_path, tol=2, max_frac=1e-3):
    """Decoded-RGB comparison: (passed, byte_identical, fraction of pixels differing by > tol/255)."""
    from PIL import Image
    with open(a_path, "rb") as fa, open(b_path, "rb") as fb:
        identical = fa.read() == fb.read()
    if identical:
        return True, True, 0.0
    a = np.asarray(Image.open(a_path).convert("RGB"), np.int16)
    b = np.asarray(Image.open(b_path).convert("RGB"), np.int16)
    if a.shape != b.shape:
        return False, False, 1.0
    frac = float(np.count_nonzero(np.abs(a - b).max(axis=2) > tol)) / (a.shape[0] * a.shape[1])
    return frac <= max_frac, False, frac

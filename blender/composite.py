"""Stage 3 of the teaser: diagonal "peel" composite of the Cycles render and hidden layer h = 1.

Left of a slanted seam: the Blender Cycles path-traced source view (beauty.png).  Right of
it: the re-implementation's first hidden layer (h = 1) behind the same pixels, coloured by
its PVHV test - kept fragments in their renderer colour, culled fragments dimmed and tinted
orange, empty pixels dark slate.  Both images come from the same camera and are pixel-aligned.

Modes (numpy + Pillow only, renderer env):
    composite.py                       -> build/teaser_full.png and out/teaser.webp
    composite.py --check COMMITTED     compare the freshly written --out-webp with COMMITTED
    composite.py --align-check DEPTH   one-time camera alignment check against a Blender depth .npy
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")

# seam: straight line from (SEAM_TOP * W, 0) to (SEAM_BOTTOM * W, H); right side = hidden layer
SEAM_TOP, SEAM_BOTTOM = 0.62, 0.50
SEAM_WIDTH = 2.5                        # px, light seam line
SEAM_COLOR = (246, 240, 228)
ORANGE = np.array((0xF2, 0xA6, 0x5A), np.float32)        # page accent, culled tint
EMPTY = (0x15, 0x1A, 0x22)                               # no hidden surface
KEPT_LIFT = 0.18                        # kept = c + KEPT_LIFT * (255 - c)
CULL_DIM, CULL_TINT = 0.08, 0.15        # culled = CULL_DIM * c + CULL_TINT * ORANGE
FONT_PX = 23
WEBP_MAX_BYTES = 1_000_000
WEBP_QUALITIES = (86, 82, 78, 74, 70)

# --check tolerance (measured between two independent full regenerations; see README)
TOL_MEAN_ABS = 1.5                      # mean |delta| over all channels, in 8-bit units
TOL_FRAC_GT8 = 0.01                     # fraction of pixels whose max-channel |delta| > 8


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--beauty", default=os.path.join(BUILD, "beauty.png"))
    p.add_argument("--layers", default=os.path.join(BUILD, "layer1_1920x1080.npz"))
    p.add_argument("--out-png", default=os.path.join(BUILD, "teaser_full.png"))
    p.add_argument("--out-webp", default=os.path.join(HERE, "out", "teaser.webp"))
    p.add_argument("--check", metavar="COMMITTED_WEBP", default=None,
                   help="after writing --out-webp, compare it with this file and exit 1 if out of tolerance")
    p.add_argument("--align-check", metavar="BLENDER_DEPTH_NPY", default=None,
                   help="alignment check only: compare layer-0 depth with a Blender depth render")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------------------
def font(px, bold=False):
    names = ("Lato-Bold.ttf" if bold else "Lato-Semibold.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    for d, n in (("/usr/share/fonts/truetype/lato", names[0]), ("/usr/share/fonts/truetype/dejavu", names[1])):
        path = os.path.join(d, n)
        if os.path.isfile(path):
            return ImageFont.truetype(path, px)
    return ImageFont.load_default(size=px)


def hidden_layer_rgb(z):
    c = z["color"].astype(np.float32)
    mask, kept = z["mask"], z["in_pvhv"]
    out = np.empty_like(c)
    out[...] = np.array(EMPTY, np.float32)
    out[kept] = c[kept] + KEPT_LIFT * (255.0 - c[kept])
    cu = mask & ~kept
    out[cu] = CULL_DIM * c[cu] + CULL_TINT * ORANGE
    return out


def seam_distance(w, h):
    """Signed distance (px) of each pixel centre to the seam; > 0 on the right side."""
    x0, x1 = SEAM_TOP * w, SEAM_BOTTOM * w
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64) + 0.5
    # line through (x0, 0) and (x1, h); unit normal (h, x0 - x1) / norm points to +x
    return ((xx - x0) * h + yy * (x0 - x1)) / np.hypot(h, x0 - x1)


def compose(beauty, hidden):
    h, w = hidden.shape[:2]
    d = seam_distance(w, h)[..., None]
    cover = np.clip(d + 0.5, 0.0, 1.0)                       # analytic box-filter coverage
    img = beauty * (1.0 - cover) + hidden * cover
    line = np.clip(SEAM_WIDTH / 2.0 + 0.5 - np.abs(d), 0.0, 1.0)
    img = img * (1.0 - line) + np.array(SEAM_COLOR, np.float32) * line
    return Image.fromarray(np.clip(np.rint(img), 0, 255).astype(np.uint8))


def draw_tags(im):
    w, h = im.size
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    f, fb = font(FONT_PX), font(FONT_PX, bold=True)
    pad, margin, gap = 12, 24, 10

    def box(x0, y0, x1, y1):
        dr.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=(12, 14, 18, 175))

    # bottom-left: what the left part is
    text = "Source view · path-traced in Blender Cycles"
    tw = dr.textlength(text, font=f)
    asc, desc = f.getmetrics()
    th = asc + desc
    x0, y1 = margin, h - margin
    box(x0, y1 - th - 2 * pad, x0 + tw + 2 * pad, y1)
    dr.text((x0 + pad, y1 - pad - th), text, font=f, fill=(245, 242, 236, 255))

    # bottom-right: what the right part is, plus a kept / culled / empty legend
    title = "Hidden layer h = 1 · PVHV test, re-implementation (2026)"
    items = [("kept (own colour)", hidden_swatch("kept")), ("culled", hidden_swatch("culled")), ("empty", EMPTY)]
    sw = th - 6
    legend_w = sum(sw + 8 + dr.textlength(n, font=f) for n, _ in items) + 2 * 22
    bw = max(dr.textlength(title, font=fb), legend_w) + 2 * pad
    bh = 2 * th + gap + 2 * pad
    x1, y1 = w - margin, h - margin
    bx0, by0 = x1 - bw, y1 - bh
    box(bx0, by0, x1, y1)
    dr.text((bx0 + pad, by0 + pad), title, font=fb, fill=(245, 242, 236, 255))
    x, y = bx0 + pad, by0 + pad + th + gap
    for name, col in items:
        dr.rectangle((x, y + 3, x + sw, y + 3 + sw), fill=(*col, 255), outline=(200, 200, 200, 255))
        x += sw + 8
        dr.text((x, y), name, font=f, fill=(225, 222, 216, 255))
        x += dr.textlength(name, font=f) + 22
    return Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")


def hidden_swatch(kind):
    """Representative legend colour: a mid-grey renderer colour through the same mapping."""
    c = np.float32(150.0)
    if kind == "kept":
        v = np.array((c, c, c)) + KEPT_LIFT * (255.0 - c)
    else:
        v = CULL_DIM * np.array((c, c, c)) + CULL_TINT * ORANGE
    return tuple(int(round(float(x))) for x in v)


def write_webp(im, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    for q in WEBP_QUALITIES:
        im.save(tmp, "WEBP", quality=q, method=6)
        if os.path.getsize(tmp) <= WEBP_MAX_BYTES:
            break
    os.replace(tmp, path)
    return q, os.path.getsize(path)


def compare(a_path, b_path):
    a = np.asarray(Image.open(a_path).convert("RGB"), np.int16)
    b = np.asarray(Image.open(b_path).convert("RGB"), np.int16)
    if a.shape != b.shape:
        return float("inf"), 1.0
    d = np.abs(a - b)
    return float(d.mean()), float(np.mean(d.max(axis=2) > 8))


# ---------------------------------------------------------------------------------------
def depth_edges(z, rel=0.05):
    e = np.zeros(z.shape, bool)
    dx = np.abs(np.diff(z, axis=1)) > rel * np.minimum(z[:, 1:], z[:, :-1])
    dy = np.abs(np.diff(z, axis=0)) > rel * np.minimum(z[1:], z[:-1])
    e[:, 1:] |= dx
    e[:, :-1] |= dx
    e[1:] |= dy
    e[:-1] |= dy
    return e


def dilate(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def align_check(layers, depth_npy, beauty):
    zr = np.load(layers)["depth0"]
    zb = np.load(depth_npy)
    both = (zr > 0) & (zb > 0)
    dz = np.abs(zr - zb)[both]
    rel = dz / zr[both]
    print(f"coverage: renderer {np.mean(zr > 0):.4f}  blender {np.mean(zb > 0):.4f}  both {np.mean(both):.4f}")
    print(f"|dz| over both: median {np.median(dz):.2e} m  p95 {np.percentile(dz, 95):.2e} m  "
          f"(relative median {np.median(rel):.2e}, p95 {np.percentile(rel, 95):.2e}, "
          f"frac rel>1% {np.mean(rel > 0.01):.4f})")
    best = []
    for sy in range(-2, 3):
        for sx in range(-2, 3):
            a, b = zr[2:-2, 2:-2], np.roll(np.roll(zb, sy, 0), sx, 1)[2:-2, 2:-2]
            m = (a > 0) & (b > 0)
            best.append((float(np.median(np.abs(a - b)[m] / a[m])), sx, sy))
    best.sort()
    print("best integer shift (dx, dy) by median relative |dz|: "
          f"({best[0][1]}, {best[0][2]}) {best[0][0]:.2e}; next ({best[1][1]}, {best[1][2]}) {best[1][0]:.2e}")
    er, eb = depth_edges(zr), depth_edges(zb)
    for r in (0, 1, 2):
        print(f"depth edges within {r} px: renderer->blender {np.mean(dilate(eb, r)[er]):.3f}  "
              f"blender->renderer {np.mean(dilate(er, r)[eb]):.3f}")
    base = np.asarray(Image.open(beauty).convert("L"), np.float32) * 0.35 if os.path.isfile(beauty) else \
        np.zeros(zr.shape, np.float32)
    img = np.repeat(base[..., None], 3, axis=2)
    img[er & ~eb] = (255, 60, 60)
    img[eb & ~er] = (60, 220, 255)
    img[er & eb] = (255, 255, 255)
    out = os.path.join(os.path.dirname(os.path.abspath(depth_npy)), "align_edges.png")
    Image.fromarray(img.astype(np.uint8)).save(out)
    print(f"wrote {out} (white = both, red = renderer only, cyan = Blender only)")


def main(argv=None):
    args = parse_args(argv)
    if args.align_check:
        align_check(args.layers, args.align_check, args.beauty)
        return 0
    if args.check and not os.path.isfile(args.check):
        print(f"error: --check baseline {args.check} does not exist", file=sys.stderr)
        return 2
    if args.check and os.path.exists(args.out_webp) and os.path.samefile(args.out_webp, args.check):
        print(f"error: --out-webp {args.out_webp} is the --check baseline {args.check}; "
              "write the fresh image elsewhere (e.g. --out-webp build/check/teaser.webp)", file=sys.stderr)
        return 2
    z = np.load(args.layers)
    beauty = np.asarray(Image.open(args.beauty).convert("RGB"), np.float32)
    hidden = hidden_layer_rgb(z)
    if beauty.shape != hidden.shape:
        sys.exit(f"error: beauty {beauty.shape} and layers {hidden.shape} differ in size")
    im = draw_tags(compose(beauty, hidden))
    os.makedirs(os.path.dirname(os.path.abspath(args.out_png)), exist_ok=True)
    im.save(args.out_png + ".tmp.png")
    os.replace(args.out_png + ".tmp.png", args.out_png)
    q, size = write_webp(im, args.out_webp)
    print(f"wrote {args.out_png} and {args.out_webp} ({im.size[0]}x{im.size[1]}, webp q={q}, {size} bytes)")
    if args.check:
        mean_abs, frac = compare(args.out_webp, args.check)
        ok = mean_abs <= TOL_MEAN_ABS and frac <= TOL_FRAC_GT8
        print(f"check vs {args.check}: mean|d| = {mean_abs:.4f} (tol {TOL_MEAN_ABS}), "
              f"frac(max|d| > 8) = {frac:.5f} (tol {TOL_FRAC_GT8}) -> {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

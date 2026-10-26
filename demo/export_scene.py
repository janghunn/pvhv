#!/usr/bin/env python
"""Export renderer scenes for the WebGL2 demo (docs/assets/demo/).

For each scene the exporter writes exactly what the offline renderer uploads to
the GPU (renderer/pvhv/peel.py, camera.py): per-batch float32 vertex arrays
(re-indexed losslessly), float32 matrices in column-major order, the probe
pattern, the per-pass constants, and the original diffuse JPEGs (after checking
that they decode to the renderer's texture arrays).

Usage:
    python demo/export_scene.py [--out DIR] [--scenes chess,blocks] [--check]

Chess needs PVHV_MODELS_CACHE (the glTF model cache).  Single-writer manual
build tool: not safe against concurrent runs; git is the recovery mechanism for
an interrupted export.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "renderer"))

import numpy as np  # noqa: E402

from pvhv.camera import gl_mat  # noqa: E402
from pvhv.peel import DELTA, EPS, MARGIN, N_PATTERN  # noqa: E402
from pvhv.sampling import probe_samples  # noqa: E402
from pvhv.scene import load_scene, models_cache_dir  # noqa: E402
from pvhv.warp import linear_to_srgb8  # noqa: E402

DEFAULT_OUT = os.path.join(REPO, "docs", "assets", "demo")
WIDTH, HEIGHT = 640, 360
LABEL = "Re-implementation (2026), not the code used for the paper's experiments"

SCENES = {
    "chess": {
        "title": "Chess set",
        "attribution_text": "Chess Set by Riley Queen, Poly Haven",
        "attribution_url": "https://polyhaven.com/a/chess_set",
        "licence": "CC0 1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    },
    "blocks": {
        "title": "Blocks",
        "attribution_text": "Procedural blocks scene (part of this re-implementation)",
        "attribution_url": None,
        "licence": "MIT",
        "licence_url": None,
    },
}


def f32(x):
    """Exact float32 value as a Python float (JSON prints it with repr)."""
    return float(np.float32(x))


def f32_list(a):
    return [float(v) for v in np.asarray(a, np.float32).reshape(-1)]


def f64_list(a):
    return [float(v) for v in np.asarray(a, np.float64).reshape(-1)]


def mat_f32(m):
    """Row-major 4x4 -> the 16 float32 values peel.py uploads (column-major)."""
    return [float(v) for v in np.frombuffer(gl_mat(m), np.float32)]


def normal_matrix_f32(model):
    """normalMatrix exactly as peel.py builds it: inv(model[:3,:3]).T, column-major float32."""
    m3 = np.asarray(model, np.float64)[:3, :3]
    nm = np.linalg.inv(m3).T
    return [float(v) for v in np.ascontiguousarray(nm.T, np.float32).reshape(-1)]


def reindex(batch):
    """De-duplicate the batch's vertices on the exact float32 bytes of (position, normal, uv).

    Returns (positions, normals, uvs, indices) with vertices in order of first
    occurrence and the original triangle order kept."""
    pos = np.ascontiguousarray(batch.positions, np.float32)
    nrm = np.ascontiguousarray(batch.normals, np.float32)
    uv = np.ascontiguousarray(batch.uvs, np.float32)
    n = len(pos)
    if not (len(nrm) == len(uv) == len(batch.items) == n) or n % 3:
        raise ValueError(f"batch {batch.name!r}: inconsistent vertex arrays")
    items = np.unique(batch.items)
    if len(items) != 1:
        raise ValueError(f"batch {batch.name!r}: vertices carry {len(items)} items, expected 1")
    packed = np.ascontiguousarray(np.hstack([pos.view(np.uint32), nrm.view(np.uint32), uv.view(np.uint32)]))
    keys = packed.view(np.dtype((np.void, packed.dtype.itemsize * packed.shape[1]))).reshape(-1)
    _, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    order = np.argsort(first, kind="stable")              # unique slots by first occurrence
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    sel = first[order]
    indices = rank[inverse]
    if not (np.array_equal(pos[sel][indices].view(np.uint32), pos.view(np.uint32))
            and np.array_equal(nrm[sel][indices].view(np.uint32), nrm.view(np.uint32))
            and np.array_equal(uv[sel][indices].view(np.uint32), uv.view(np.uint32))):
        raise AssertionError(f"batch {batch.name!r}: re-indexing is not lossless")
    return pos[sel], nrm[sel], uv[sel], indices, int(items[0])


def _pad4(buf):
    pad = (-len(buf)) % 4
    if pad:
        buf.extend(b"\0" * pad)


def _gltf_base_color_images(scene):
    """Absolute paths of the baseColorTexture images of every glTF object in the scene config."""
    paths = []
    for obj in scene.config.get("objects", []):
        if "gltf" not in obj:
            continue
        gpath = os.path.join(models_cache_dir(), obj["gltf"])
        with open(gpath) as fh:
            doc = json.load(fh)
        for mat in doc.get("materials", []):
            tex = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture")
            if tex is None:
                continue
            uri = doc["images"][doc["textures"][tex["index"]]["source"]]["uri"]
            p = os.path.join(os.path.dirname(gpath), uri)
            if p not in paths:
                paths.append(p)
    return paths


def _decode_rgb(path):
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        return np.asarray(im.convert("RGB"), np.uint8)


def export_textures(scene):
    """Map every scene texture key to the original JPEG that decodes to exactly its pixels.

    Returns {key: (file_name, jpeg_bytes, width, height)}; raises if a texture has
    no bit-identical source file (the exporter never re-encodes)."""
    if not scene.textures:
        return {}
    candidates = [(p, _decode_rgb(p)) for p in _gltf_base_color_images(scene)]
    out = {}
    for key in sorted(scene.textures):
        arr = scene.textures[key]
        if arr.ndim != 3 or arr.shape[2] != 4 or not np.all(arr[..., 3] == 255):
            raise ValueError(f"texture {key!r}: expected opaque RGBA (alpha 255)")
        hits = [p for p, rgb in candidates if rgb.shape == arr.shape[:2] + (3,) and np.array_equal(rgb, arr[..., :3])]
        if len(hits) != 1:
            raise ValueError(f"texture {key!r}: {len(hits)} source JPEG(s) decode to exactly its pixels; "
                             "refusing to re-encode")
        with open(hits[0], "rb") as fh:
            data = fh.read()
        h, w = arr.shape[:2]
        out[key] = (os.path.basename(hits[0]), data, w, h)
    return out


def canonical_batches(scene):
    """Reorder ``scene.batches`` in place: items keep their scene order, batches of one item
    are sorted by a hash of their vertex data.

    The glTF loader's order of the primitives of one multi-primitive node depends on
    Python's string-hash seed (trimesh node naming), so the raw order is not
    reproducible between runs.  Batches of one item share the item ID, so this order
    only decides depth ties inside one item; crosscheck.py applies the same order."""
    first = {}
    for k, b in enumerate(scene.batches):
        first.setdefault(int(b.items[0]), k)

    def key(b):
        h = hashlib.sha256()
        for a in (b.positions, b.normals, b.uvs):
            h.update(np.ascontiguousarray(a, np.float32).tobytes())
        return (first[int(b.items[0])], h.hexdigest())

    scene.batches.sort(key=key)
    return scene


def export_scene(name):
    """Return {relative_path: bytes} for one scene (paths relative to the output dir)."""
    scene = canonical_batches(load_scene(name, width=WIDTH, height=HEIGHT))
    cam = scene.camera
    meta = SCENES[name]
    textures = export_textures(scene)
    names = [t[0] for t in textures.values()]
    if len(set(names)) != len(names):
        raise ValueError(f"{name}: texture file names collide")

    geo = bytearray()
    batches = []
    for b in scene.batches:
        pos, nrm, uv, idx, item = reindex(b)
        nv = len(pos)
        itype = "uint16" if nv <= 65535 else "uint32"
        offsets = {}
        for key, arr in (("positions", pos), ("normals", nrm), ("uvs", uv),
                         ("indices", idx.astype("<u2" if itype == "uint16" else "<u4"))):
            offsets[key] = len(geo)
            geo.extend(np.ascontiguousarray(arr).astype(arr.dtype.newbyteorder("<"), copy=False).tobytes())
            _pad4(geo)
        batches.append({
            "name": b.name,
            "item": item,
            "base_color": f32_list(b.base_color),
            "texture": textures[b.texture][0] if b.texture is not None else None,
            "alpha_test": bool(b.alpha_test),
            "model": mat_f32(b.model),
            "normal_matrix": normal_matrix_f32(b.model),
            "vertex_count": nv,
            "index_count": int(len(idx)),
            "index_type": itype,
            "offsets": offsets,
        })
        if b.alpha_texture is not None:
            raise ValueError(f"{name}: alpha textures are not supported by the demo exporter")

    E = f32(scene.E)
    right, up, back = cam.basis()
    pattern = np.asarray(probe_samples(scene.samples), np.float64).astype(np.float32)
    if not 1 <= scene.samples <= N_PATTERN:
        raise ValueError(f"{name}: samples must be in 1..{N_PATTERN}")
    data = {
        "id": name,
        "title": meta["title"],
        "version": 1,
        "W": cam.W, "H": cam.H, "guard": cam.guard, "ox": cam.ox, "oy": cam.oy, "Wg": cam.Wg, "Hg": cam.Hg,
        "F": float(cam.F),
        "fovy": float(cam.fovy_deg),
        "near": f32(cam.near), "far": f32(cam.far),
        "E": E,
        "eps": f32(EPS), "delta": f32(DELTA), "margin": f32(MARGIN),
        "nSamples": int(scene.samples),
        "probes": f32_list(pattern),
        "background": [float(c) for c in scene.background],
        "background_srgb8": [int(c) for c in linear_to_srgb8(np.asarray(scene.background, np.float64))],
        "cull_backfaces": bool(scene.cull_backfaces),
        "eye": f64_list(cam.eye), "target": f64_list(cam.target), "up_config": f64_list(cam.up),
        "basis": {"right": f64_list(right), "up": f64_list(up), "back": f64_list(back)},
        "view0": mat_f32(cam.view_matrix()),
        # view at t = (E, 0) with E the exported float32 value; used by the JS math test
        "view_tE": mat_f32(cam.view_matrix((E, 0.0))),
        "proj_src": mat_f32(cam.projection_matrix(cam.Wg, cam.Hg)),
        "proj_ref": mat_f32(cam.projection_matrix(cam.W, cam.H)),
        "items": scene.n_items,
        "triangles": scene.n_triangles,
        "batches": batches,
        "textures": [{"file": f, "width": w, "height": h} for (f, _, w, h) in
                     sorted(textures.values(), key=lambda t: t[0])],
        "geometry_bytes": len(geo),
        "label": LABEL,
    }
    for k in ("attribution_text", "attribution_url", "licence", "licence_url"):
        data[k] = meta[k]
    files = {f"{name}/scene.json": dump_json(data), f"{name}/geometry.bin": bytes(geo)}
    for (f, blob, _, _) in textures.values():
        files[f"{name}/{f}"] = blob
    return files


def dump_json(obj):
    return (json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def attribution_md():
    return f"""# Demo scene data: sources and licences

{LABEL}.

The files in this directory are generated by `demo/export_scene.py` from the
scenes of the re-implementation's offline renderer (`renderer/pvhv/scenes/`).

| scene | source | author | licence |
|---|---|---|---|
| `chess/` | [Chess Set](https://polyhaven.com/a/chess_set), Poly Haven (1k glTF; geometry re-exported, the three diffuse JPEGs copied unchanged) | Riley Queen | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `blocks/` | Procedural blocks scene generated by `renderer/pvhv/scene.py` | part of this re-implementation | MIT (this repository) |

No model of the paper's own scenes (including the elephant) is used or redistributed here.
""".encode("utf-8")


def build(scenes):
    """All output files {relative_path: bytes}, manifest.json included."""
    files = {"ATTRIBUTION.md": attribution_md()}
    entries = []
    for name in scenes:
        files.update(export_scene(name))
        meta = SCENES[name]
        entries.append({"id": name, "title": meta["title"], "path": f"{name}/scene.json",
                        **{k: meta[k] for k in ("attribution_text", "attribution_url", "licence", "licence_url")}})
    manifest = {
        "scenes": entries,
        "files": [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                  for p, b in sorted(files.items())],
        "label": LABEL,
    }
    files["manifest.json"] = dump_json(manifest)
    return files


def _write_atomic(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".export-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _existing_files(root):
    out = set()
    for d, dirs, fs in os.walk(root):
        dirs.sort()
        for f in fs:
            out.add(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    return out


class Refused(Exception):
    """The output directory is not one the exporter may write into (nothing was written)."""


def _check_rel(rel, where):
    """Refused unless rel is a plain relative POSIX path (no '..', '.', empty part, backslash or root)."""
    if (not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel
            or os.path.isabs(rel) or any(part in ("", ".", "..") for part in rel.split("/"))):
        raise Refused(f"unsafe path {rel!r} in {where}")
    return rel


def _refuse_symlinks(out_dir):
    """Refused if out_dir or anything below it (file or directory) is a symbolic link.

    All later paths are then plain lexical joins under out_dir that cannot leave it."""
    if os.path.islink(out_dir):
        raise Refused(f"{out_dir} is a symbolic link; refusing to export into it")
    if not os.path.isdir(out_dir):
        return

    def _raise(e):
        raise Refused(f"cannot scan {out_dir}: {e}")

    for d, dirs, fs in os.walk(out_dir, followlinks=False, onerror=_raise):
        for name in dirs + fs:
            if os.path.islink(os.path.join(d, name)):
                raise Refused(f"{os.path.join(d, name)} is a symbolic link; refusing to export into {out_dir}")


def previous_outputs(out_dir):
    """Relative paths the previous export published (the file list of <out>/manifest.json).

    Raises Refused when out_dir exists, is not empty and has no manifest.json (not an export
    directory), or when the old manifest is unreadable or lists an unsafe path."""
    if not os.path.isdir(out_dir):
        if os.path.lexists(out_dir):
            raise Refused(f"{out_dir} exists and is not a directory")
        return set()
    manifest = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(manifest):
        if os.listdir(out_dir):
            raise Refused(f"{out_dir} is not empty and has no manifest.json; refusing to export into it")
        return set()
    try:
        with open(manifest, encoding="utf-8") as fh:
            entries = json.load(fh)["files"]
        rels = [e["path"] for e in entries]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused(f"cannot read the file list of {manifest}: {exc}")
    return {_check_rel(rel, manifest) for rel in rels}


def publish(files, out_dir):
    """Write every file atomically, manifest.json last, then remove the files that the previous
    manifest listed and the new export no longer produces (and directories emptied by that).

    Files the exporter never published are left alone.  Raises Refused before writing anything
    when out_dir is not an export directory (see previous_outputs), contains a symbolic link
    anywhere, or a path is unsafe.  All paths are lexical joins under out_dir (never realpaths)."""
    out_dir = os.path.abspath(out_dir)
    _refuse_symlinks(out_dir)
    old = previous_outputs(out_dir)
    for rel in files:
        _check_rel(rel, "the new export")
    for rel in sorted(files):
        if rel != "manifest.json":
            _write_atomic(os.path.join(out_dir, rel), files[rel])
    _write_atomic(os.path.join(out_dir, "manifest.json"), files["manifest.json"])
    for rel in sorted(old - set(files)):
        path = os.path.join(out_dir, rel)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        os.unlink(path)
        d = os.path.dirname(path)
        while d != out_dir and d.startswith(out_dir + os.sep) and not os.path.islink(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)


def compare_dirs(fresh, out_dir):
    """List of differences between a fresh export directory and out_dir."""
    a, b = _existing_files(fresh), _existing_files(out_dir) if os.path.isdir(out_dir) else set()
    problems = [f"missing in {out_dir}: {p}" for p in sorted(a - b)]
    problems += [f"not produced by the exporter: {p}" for p in sorted(b - a)]
    for p in sorted(a & b):
        with open(os.path.join(fresh, p), "rb") as fa, open(os.path.join(out_dir, p), "rb") as fb:
            if fa.read() != fb.read():
                problems.append(f"differs: {p}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT, help="output directory (default docs/assets/demo)")
    ap.add_argument("--scenes", default="chess,blocks", help="comma-separated scene names")
    ap.add_argument("--check", action="store_true",
                    help="export into a fresh temp dir and byte-compare with --out (never writes into --out)")
    args = ap.parse_args(argv)
    scenes = [s for s in args.scenes.split(",") if s]
    for s in scenes:
        if s not in SCENES:
            ap.error(f"unknown scene {s!r} (known: {', '.join(SCENES)})")
    out_dir = os.path.abspath(args.out)
    files = build(scenes)
    if args.check:
        tmp = tempfile.mkdtemp(prefix="pvhv-demo-export-")
        try:
            publish(files, tmp)
            problems = compare_dirs(tmp, out_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for p in problems:
            print(p)
        print(f"export check: {'FAIL' if problems else 'OK'} ({len(files)} files, {out_dir})")
        return 1 if problems else 0
    try:
        publish(files, out_dir)
    except Refused as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2
    total = 0
    for rel in sorted(files):
        print(f"{len(files[rel]):>10}  {rel}")
        total += len(files[rel])
    print(f"{total:>10}  total ({out_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Scenes: json configs, the procedural ``blocks`` scene and an OBJ/MTL loader.

A scene is a camera (section A), per-scene constants (near, far, E, ...) and a
list of draw batches.  A batch is a non-indexed triangle list with one material;
every vertex carries the item ID of the object it belongs to (item >= 1, one per
OBJ object, or per group when the file has no ``o`` statements; one per
procedural object).  Item IDs are assigned in file order and are stable.
"""
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field

import numpy as np

from .camera import Camera

SCENE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenes")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_VERSION = 1


def models_cache_dir():
    return os.environ.get("PVHV_MODELS_CACHE", os.path.join(REPO_ROOT, "models", "cache"))


@dataclass
class DrawBatch:
    positions: np.ndarray            # (N, 3) float32, object space, N = 3 * triangles
    normals: np.ndarray              # (N, 3) float32
    uvs: np.ndarray                  # (N, 2) float32
    items: np.ndarray                # (N,) uint32
    model: np.ndarray                # (4, 4) float64 row-major world transform
    base_color: tuple                # linear RGB factor (MTL Kd)
    texture: str = None              # key into Scene.textures (map_Kd), or None
    alpha_texture: str = None        # key into Scene.textures (map_d), or None
    alpha_test: bool = False
    name: str = ""


@dataclass
class Scene:
    name: str
    camera: Camera
    E: float
    cull_backfaces: bool
    samples: int
    background: tuple
    batches: list = field(default_factory=list)
    textures: dict = field(default_factory=dict)        # key -> HxWx4 (or HxW) uint8, top row first
    item_names: list = field(default_factory=list)      # item_names[i-1] is the name of item i
    unresolved_textures: list = field(default_factory=list)
    config: dict = field(default_factory=dict)

    @property
    def n_items(self):
        return len(self.item_names)

    @property
    def n_triangles(self):
        return sum(len(b.items) // 3 for b in self.batches)


# --------------------------------------------------------------------------------------
# scene configs

def scene_names():
    return sorted(f[:-5] for f in os.listdir(SCENE_DIR) if f.endswith(".json"))


def load_config(name):
    path = name if name.endswith(".json") else os.path.join(SCENE_DIR, name + ".json")
    with open(path) as fh:
        return json.load(fh)


def camera_from_config(cfg):
    c = cfg["camera"]
    return Camera(eye=tuple(c["eye"]), target=tuple(c["target"]), up=tuple(c.get("up", (0, 1, 0))),
                  fovy_deg=float(c["fovy"]), W=int(c["W"]), H=int(c["H"]), guard=float(c.get("guard", 1.2)),
                  near=float(cfg["near"]), far=float(cfg["far"]))


def load_scene(name, width=None, height=None, verbose=False):
    """Load a scene by name (``pvhv/scenes/<name>.json``) or json path.

    ``width``/``height`` override the configured output size (F follows H, so the
    field of view is unchanged)."""
    cfg = load_config(name)
    cam = camera_from_config(cfg)
    if width is not None:
        cam.W = int(width)
    if height is not None:
        cam.H = int(height)
    scene = Scene(name=cfg.get("name", name), camera=cam, E=float(cfg["E"]),
                  cull_backfaces=bool(cfg.get("cull_backfaces", True)), samples=int(cfg.get("samples", 14)),
                  background=tuple(cfg.get("background", (0.0, 0.0, 0.0))), config=cfg)
    for obj in cfg["objects"]:
        model = np.asarray(obj.get("transform", np.eye(4)), np.float64).reshape(4, 4)
        if obj["model"] == "procedural:blocks":
            _add_blocks(scene, model)
        elif "obj" in obj:
            path = os.path.join(models_cache_dir(), obj["obj"])
            _add_obj(scene, path, model, model_id=obj["model"], verbose=verbose)
        elif "gltf" in obj:
            path = os.path.join(models_cache_dir(), obj["gltf"])
            _add_gltf(scene, path, model, model_id=obj["model"])
        else:
            raise ValueError(f"unknown object entry {obj!r}")
    if scene.unresolved_textures:
        print(f"warning: {len(scene.unresolved_textures)} unresolved texture(s) in scene "
              f"'{scene.name}' fall back to Kd: " + ", ".join(sorted(set(scene.unresolved_textures))[:20]),
              file=sys.stderr)
    return scene


# --------------------------------------------------------------------------------------
# procedural "blocks"

def _box(center, size):
    """Axis-aligned box as CCW-outward triangles: (positions, normals, uvs)."""
    c = np.asarray(center, float)
    h = np.asarray(size, float) / 2.0
    pos, nrm, uv = [], [], []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            n = np.zeros(3)
            n[axis] = sign
            a, b = [(axis + 1) % 3, (axis + 2) % 3]
            if sign < 0:
                a, b = b, a
            # corners in (a, b) parameter space; u x v = n for CCW winding seen from outside
            quad = []
            for (sa, sb) in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = c.copy()
                p[axis] += sign * h[axis]
                p[a] += sa * h[a]
                p[b] += sb * h[b]
                quad.append(p)
            for tri in ((0, 1, 2), (0, 2, 3)):
                for k in tri:
                    pos.append(quad[k])
                    nrm.append(n)
                    uv.append(((quad[k][a] - c[a]) / (2 * h[a]) + 0.5, (quad[k][b] - c[b]) / (2 * h[b]) + 0.5))
    return np.array(pos), np.array(nrm), np.array(uv)


def _sphere(center, r, n_lat=24, n_lon=48):
    c = np.asarray(center, float)
    th = np.linspace(0.0, np.pi, n_lat + 1)
    ph = np.linspace(0.0, 2.0 * np.pi, n_lon + 1)

    def p(i, j):
        return np.array([np.sin(th[i]) * np.cos(ph[j]), np.cos(th[i]), -np.sin(th[i]) * np.sin(ph[j])])

    pos, nrm, uv = [], [], []
    for i in range(n_lat):
        for j in range(n_lon):
            q = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            tris = []
            if i > 0:
                tris.append((q[0], q[1], q[3]))
            if i < n_lat - 1:
                tris.append((q[1], q[2], q[3]))
            for tri in tris:
                for (a, b) in tri:
                    d = p(a, b)
                    pos.append(c + r * d)
                    nrm.append(d)
                    uv.append((ph[b] / (2 * np.pi), 1.0 - th[a] / np.pi))
    return np.array(pos), np.array(nrm), np.array(uv)


def _ground(x0, x1, z0, z1, y=0.0):
    quad = [(x0, y, z1), (x1, y, z1), (x1, y, z0), (x0, y, z0)]
    pos = np.array([quad[k] for k in (0, 1, 2, 0, 2, 3)], float)
    nrm = np.tile([0.0, 1.0, 0.0], (6, 1))
    uv = pos[:, [0, 2]] / 4.0
    return pos, nrm, uv


def _merge(parts):
    return tuple(np.concatenate([p[k] for p in parts]) for k in range(3))


# (name, geometry, linear base color); one item each, in this order
def _blocks_objects():
    wall = _merge([
        _box((0.0, 0.45, 0.0), (8.0, 0.9, 0.2)),      # below the windows
        _box((0.0, 2.55, 0.0), (8.0, 0.9, 0.2)),      # above the windows
        _box((-3.15, 1.5, 0.0), (1.7, 1.2, 0.2)),     # left pier
        _box((0.0, 1.5, 0.0), (1.8, 1.2, 0.2)),       # middle pier
        _box((3.15, 1.5, 0.0), (1.7, 1.2, 0.2)),      # right pier
    ])                                               # window openings: x in [-2.3,-0.9] and [0.9,2.3], y in [0.9,2.1]
    return [
        ("ground", _ground(-15.0, 15.0, -18.0, 12.0), (0.45, 0.45, 0.42)),
        ("wall", wall, (0.80, 0.72, 0.60)),
        ("box_red", _box((-1.6, 0.6, -2.0), (1.0, 1.2, 1.0)), (0.75, 0.10, 0.08)),
        ("sphere_green", _sphere((1.5, 0.8, -2.5), 0.8), (0.10, 0.60, 0.15)),
        ("box_blue", _box((0.3, 1.0, -4.0), (1.4, 2.0, 1.0)), (0.10, 0.20, 0.75)),
        ("sphere_yellow", _sphere((-2.8, 1.2, -5.0), 1.2), (0.85, 0.75, 0.10)),
        ("box_purple", _box((2.8, 1.5, -6.5), (1.5, 3.0, 1.5)), (0.50, 0.15, 0.60)),
        ("sphere_cyan", _sphere((-0.5, 0.5, -1.2), 0.5), (0.10, 0.65, 0.70)),
        ("box_back", _box((-4.5, 2.0, -8.0), (2.0, 4.0, 1.0)), (0.55, 0.35, 0.20)),
        ("box_front", _box((-2.6, 0.35, 2.5), (0.7, 0.7, 0.7)), (0.90, 0.45, 0.10)),
        ("sphere_front", _sphere((2.4, 0.45, 3.0), 0.45), (0.85, 0.85, 0.88)),
    ]


def _add_blocks(scene, model):
    for name, (pos, nrm, uv), color in _blocks_objects():
        item = scene.n_items + 1
        scene.item_names.append(f"blocks/{name}")
        scene.batches.append(DrawBatch(
            positions=pos.astype(np.float32), normals=nrm.astype(np.float32), uvs=uv.astype(np.float32),
            items=np.full(len(pos), item, np.uint32), model=model, base_color=tuple(color), name=name))


# --------------------------------------------------------------------------------------
# OBJ / MTL

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_obj(path):
    """Parse an OBJ file into per-material triangle lists.

    Returns dict with ``mtllibs`` (list), ``item_names`` (list, file order) and
    ``groups``: list of (material, positions, normals, uvs, local_items) with
    local item IDs starting at 1.  Polygons are fan-triangulated; corners without
    a normal get the face's flat normal; corners without a texcoord get (0, 0).
    """
    v_lines, vt_lines, vn_lines = [], [], []
    cv, ct, cn = [], [], []          # per triangle-corner indices (1-based, 0 = missing)
    tri_run = []                     # per triangle: run index
    runs = []                        # (o, g, material)
    mtllibs = []
    has_o = False
    o_name, g_name, mtl = None, "default", None
    cur_run = -1
    nv = nvt = nvn = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            c0 = line[0]
            if c0 == "v":
                c1 = line[1]
                if c1 == " " or c1 == "\t":
                    v_lines.append(line[2:])
                    nv += 1
                elif c1 == "t":
                    vt_lines.append(line[3:])
                    nvt += 1
                elif c1 == "n":
                    vn_lines.append(line[3:])
                    nvn += 1
                continue
            if c0 == "f" and (line[1] == " " or line[1] == "\t"):
                if cur_run < 0:
                    runs.append((o_name, g_name, mtl))
                    cur_run = len(runs) - 1
                toks = line.split()[1:]
                idx = []
                for tok in toks:
                    p = tok.split("/")
                    a = int(p[0])
                    b = int(p[1]) if len(p) > 1 and p[1] else 0
                    c = int(p[2]) if len(p) > 2 and p[2] else 0
                    if a < 0:
                        a = nv + a + 1
                    if b < 0:
                        b = nvt + b + 1
                    if c < 0:
                        c = nvn + c + 1
                    idx.append((a, b, c))
                for k in range(1, len(idx) - 1):
                    for (a, b, c) in (idx[0], idx[k], idx[k + 1]):
                        cv.append(a)
                        ct.append(b)
                        cn.append(c)
                    tri_run.append(cur_run)
                continue
            parts = line.split(None, 1)
            if not parts:
                continue
            key = parts[0]
            arg = parts[1].strip() if len(parts) > 1 else ""
            if key == "o":
                has_o = True
                o_name = arg
                cur_run = -1
            elif key == "g":
                g_name = arg or "default"
                cur_run = -1
            elif key == "usemtl":
                mtl = arg
                cur_run = -1
            elif key == "mtllib":
                if arg not in mtllibs:
                    mtllibs.append(arg)

    def floats(lines, k):
        if not lines:
            return np.zeros((0, k), np.float64)
        arr = np.array(" ".join(" ".join(s.split()[:k]) for s in lines).split(), dtype=np.float64)
        return arr.reshape(-1, k)

    V = floats(v_lines, 3)
    VT = floats(vt_lines, 2)
    VN = floats(vn_lines, 3)
    cv = np.asarray(cv, np.int64).reshape(-1, 3) - 1
    ct = np.asarray(ct, np.int64).reshape(-1, 3) - 1
    cn = np.asarray(cn, np.int64).reshape(-1, 3) - 1
    tri_run = np.asarray(tri_run, np.int64)

    # items: per object if any 'o' statement exists, else per group; file order of first face
    item_of_key, item_names, run_item = {}, [], []
    for (o, g, m) in runs:
        key = o if has_o else g
        if key is None:
            key = "default"
        if key not in item_of_key:
            item_of_key[key] = len(item_names) + 1
            item_names.append(key)
        run_item.append(item_of_key[key])
    run_item = np.asarray(run_item, np.uint32)
    run_mtl = [m for (_, _, m) in runs]

    P = V[cv]                                                   # (T, 3, 3)
    flat = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(flat, axis=1, keepdims=True)
    flat = np.where(ln > 0, flat / np.where(ln > 0, ln, 1.0), np.array([0.0, 0.0, 1.0]))
    N = np.repeat(flat[:, None, :], 3, axis=1)
    has_n = cn >= 0
    if VN.shape[0]:
        N[has_n] = VN[cn[has_n]]
    UV = np.zeros(cv.shape + (2,))
    has_t = ct >= 0
    if VT.shape[0]:
        UV[has_t] = VT[ct[has_t]]

    groups = []
    mtl_names = sorted(set(run_mtl), key=lambda m: run_mtl.index(m))
    tri_mtl = np.asarray([mtl_names.index(m) for m in run_mtl], np.int64)[tri_run] if len(tri_run) else tri_run
    for mi, m in enumerate(mtl_names):
        sel = np.nonzero(tri_mtl == mi)[0]
        if not len(sel):
            continue
        groups.append((m, P[sel].reshape(-1, 3).astype(np.float32), N[sel].reshape(-1, 3).astype(np.float32),
                       UV[sel].reshape(-1, 2).astype(np.float32),
                       np.repeat(run_item[tri_run[sel]], 3).astype(np.uint32)))
    return {"mtllibs": mtllibs, "item_names": item_names, "groups": groups}


def _load_parsed_obj(path, model_id, verbose=False):
    """parse_obj with an .npz cache under models/cache/<id>/ keyed by the OBJ sha256."""
    sha = _sha256(path)
    cache_dir = os.path.join(models_cache_dir(), model_id)
    stem = os.path.splitext(os.path.basename(path))[0]
    cache = os.path.join(cache_dir, f"{stem}.v{CACHE_VERSION}.{sha[:16]}.npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        if meta.get("sha256") == sha:
            groups = [(m, z[f"p{i}"], z[f"n{i}"], z[f"t{i}"], z[f"i{i}"]) for i, m in enumerate(meta["materials"])]
            return {"mtllibs": meta["mtllibs"], "item_names": meta["item_names"], "groups": groups}
    if verbose:
        print(f"parsing {path} ...", file=sys.stderr)
    parsed = parse_obj(path)
    os.makedirs(cache_dir, exist_ok=True)
    arrays = {}
    for i, (m, p, n, t, it) in enumerate(parsed["groups"]):
        arrays.update({f"p{i}": p, f"n{i}": n, f"t{i}": t, f"i{i}": it})
    meta = {"sha256": sha, "mtllibs": parsed["mtllibs"], "item_names": parsed["item_names"],
            "materials": [g[0] if g[0] is not None else "" for g in parsed["groups"]]}
    fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".npz.tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(fh, meta=np.array(json.dumps(meta)), **arrays)
        os.replace(tmp, cache)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return parsed


_MAP_OPTS = {"-blendu": 1, "-blendv": 1, "-bm": 1, "-boost": 1, "-cc": 1, "-clamp": 1, "-imfchan": 1,
             "-mm": 2, "-o": 3, "-s": 3, "-t": 3, "-texres": 1, "-type": 1}


def _map_filename(arg):
    """Texture path of a map_* statement: leading options removed, the whole rest of
    the line kept verbatim (paths may contain spaces)."""
    rest = arg.strip()
    while rest:
        parts = rest.split(None, 1)
        if parts[0] not in _MAP_OPTS:
            break
        rest = parts[1] if len(parts) > 1 else ""
        for _ in range(_MAP_OPTS[parts[0]]):     # -o/-s/-t take 1..3 numbers
            parts = rest.split(None, 1)
            if not parts:
                break
            try:
                float(parts[0])
            except ValueError:
                break
            rest = parts[1] if len(parts) > 1 else ""
    return rest


def parse_mtl(path):
    """MTL materials: Kd, d (or Tr), map_Kd, map_d; keywords are case-insensitive."""
    mats, cur = {}, None
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            parts = line.strip().split(None, 1)
            if not parts or parts[0].startswith("#"):
                continue
            key, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
            if key == "newmtl":
                cur = mats.setdefault(arg, {"Kd": (1.0, 1.0, 1.0), "d": 1.0})
            elif cur is None:
                continue
            elif key == "kd":
                cur["Kd"] = tuple(float(x) for x in arg.split()[:3])
            elif key == "d":
                cur["d"] = float(arg.split()[0])
            elif key == "tr":
                cur["d"] = 1.0 - float(arg.split()[0])
            elif key == "map_kd":
                cur["map_Kd"] = _map_filename(arg)
            elif key == "map_d":
                cur["map_d"] = _map_filename(arg)
    return mats


def _resolve_texture(rel, mtl_dir, model_root, index):
    """MTL directory first, then every extracted archive dir of the model id by relative
    path, then case-insensitively by path and finally by basename."""
    rel = rel.replace("\\", "/")
    cand = os.path.join(mtl_dir, rel)
    if os.path.isfile(cand):
        return cand
    for d in sorted(os.listdir(model_root)) if os.path.isdir(model_root) else []:
        cand = os.path.join(model_root, d, rel)
        if os.path.isfile(cand):
            return cand
    hit = index["path"].get(os.path.normpath(os.path.join(mtl_dir, rel)).lower())
    return hit or index["base"].get(os.path.basename(rel).lower())


def _texture_index(model_root):
    """Case-insensitive lookups (full path, basename) over the extracted archives of a model id."""
    index = {"path": {}, "base": {}}
    for d in sorted(os.listdir(model_root)) if os.path.isdir(model_root) else []:
        top = os.path.join(model_root, d)
        if not os.path.isdir(top) or d.startswith("."):
            continue
        for root, dirs, files in os.walk(top):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(root, f)
                index["path"].setdefault(os.path.normpath(full).lower(), full)
                index["base"].setdefault(f.lower(), full)
    return index


def _load_image(path):
    """map_Kd image as HxWx4 uint8.  Only a true alpha band (RGBA/LA/PA) is kept;
    tRNS colour keys of RGB/P images are ignored (treated as opaque)."""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "PA", "RGBa", "La"):
            return np.asarray(im.convert("RGBA"), np.uint8)
        if im.mode == "P":
            im.info.pop("transparency", None)
        rgb = np.asarray(im.convert("RGB"), np.uint8)
        return np.concatenate([rgb, np.full(rgb.shape[:2] + (1,), 255, np.uint8)], axis=2)


def _mask_image(path):
    """map_d mask as HxW uint8 (Pillow convert('L'): palette masks map through the palette)."""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        if im.mode == "P":
            im.info.pop("transparency", None)
        return np.asarray(im.convert("L"), np.uint8)


def _add_obj(scene, path, model, model_id, verbose=False):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"model file not found: {path} (run models/fetch_models.py)")
    parsed = _load_parsed_obj(path, model_id, verbose=verbose)
    obj_dir = os.path.dirname(path)
    model_root = os.path.join(models_cache_dir(), model_id)
    mats, mtl_dir_of = {}, {}
    for lib in parsed["mtllibs"]:
        mp = os.path.join(obj_dir, lib.replace("\\", "/"))
        if not os.path.isfile(mp):
            raise FileNotFoundError(f"material library not found: {mp}")
        for k, v in parse_mtl(mp).items():
            mats.setdefault(k, v)
            mtl_dir_of.setdefault(k, os.path.dirname(mp))
    index = None
    base_item = scene.n_items
    scene.item_names.extend(f"{model_id}/{n}" for n in parsed["item_names"])
    for (mname, p, n, t, it) in parsed["groups"]:
        mat = mats.get(mname, {"Kd": (0.8, 0.8, 0.8), "d": 1.0})
        tex_key = alpha_key = None
        alpha_test = False
        for slot in ("map_Kd", "map_d"):
            rel = mat.get(slot)
            if not rel:
                continue
            if index is None:
                index = _texture_index(model_root)
            resolved = _resolve_texture(rel, mtl_dir_of.get(mname, obj_dir), model_root, index)
            if resolved is None:
                scene.unresolved_textures.append(rel)
                continue
            key = f"{slot}:{resolved}"
            if key not in scene.textures:
                try:
                    scene.textures[key] = _load_image(resolved) if slot == "map_Kd" else _mask_image(resolved)
                except Exception as exc:  # undecodable file: fall back to Kd like an unresolved one
                    print(f"warning: cannot decode texture {resolved}: {exc}", file=sys.stderr)
                    scene.unresolved_textures.append(rel)
                    continue
            if slot == "map_Kd":
                tex_key = key
                if scene.textures[key][..., 3].min() < 255:
                    alpha_test = True
            else:
                alpha_key = key
                alpha_test = True
        if mat.get("d", 1.0) < 0.5:        # constant d is the alpha: below the cutoff -> fully cut
            continue
        scene.batches.append(DrawBatch(
            positions=p, normals=n, uvs=t, items=(it + np.uint32(base_item)).astype(np.uint32), model=model,
            base_color=tuple(mat["Kd"]), texture=tex_key, alpha_texture=alpha_key, alpha_test=alpha_test,
            name=mname or ""))


# --------------------------------------------------------------------------------------
# glTF (trimesh scene load)

def _add_gltf(scene, path, model, model_id):
    """glTF via trimesh: one item per node instance in glTF node order; base color =
    baseColorFactor x baseColorTexture; alphaMode MASK/BLEND -> alpha test 0.5."""
    import trimesh

    if not os.path.isfile(path):
        raise FileNotFoundError(f"model file not found: {path} (run models/fetch_models.py)")
    with open(path) as fh:
        doc = json.load(fh)
    order = []                                   # glTF node indices, depth-first in scene order

    def visit(i):
        order.append(i)
        for c in doc["nodes"][i].get("children", []):
            visit(c)

    for i in doc["scenes"][doc.get("scene", 0)]["nodes"]:
        visit(i)
    names = [doc["nodes"][i].get("name", f"node{i}") for i in order]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: glTF node names must be unique to map trimesh nodes")
    tm = trimesh.load(path, force="scene")
    by_node = {}
    for gnode in tm.graph.nodes_geometry:        # trimesh may suffix multi-primitive nodes
        base = gnode if gnode in names else max((n for n in names if gnode.startswith(n)), key=len, default=None)
        if base is None:
            raise ValueError(f"{path}: cannot map trimesh node {gnode!r} to a glTF node")
        by_node.setdefault(base, []).append(gnode)
    tex_ids = {}
    textured = set()                             # names of materials whose base-colour texture was loaded
    for name in names:
        if name not in by_node:
            continue
        item = scene.n_items + 1
        scene.item_names.append(f"{model_id}/{name}")
        for gnode in sorted(by_node[name]):
            T, gname = tm.graph[gnode]
            mesh = tm.geometry[gname]
            f = np.asarray(mesh.faces, np.int64).reshape(-1)
            pos = np.asarray(mesh.vertices, np.float64)[f]
            nrm = np.asarray(mesh.vertex_normals, np.float64)[f]
            uv = np.zeros((len(f), 2))
            mat = getattr(mesh.visual, "material", None)
            if getattr(mesh.visual, "uv", None) is not None:
                uv = np.asarray(mesh.visual.uv, np.float64)[f]
            factor = np.ones(4)
            tex_key, alpha_test = None, False
            if mat is not None:
                if getattr(mat, "baseColorFactor", None) is not None:
                    factor = np.asarray(mat.baseColorFactor, np.float64)
                    if factor.max() > 1.0:              # trimesh stores uint8 factors
                        factor = factor / 255.0
                img = getattr(mat, "baseColorTexture", None)
                if img is not None:
                    tex_key = tex_ids.setdefault(id(img), f"gltf:{path}:{len(tex_ids)}")
                    if tex_key not in scene.textures:
                        try:
                            rgba = img.convert("RGBA") if img.mode in ("RGBA", "LA", "PA") else img.convert("RGB")
                            arr = np.asarray(rgba, np.uint8)
                        except Exception as exc:  # treated like an unresolved texture (falls back to the factor)
                            print(f"warning: cannot convert glTF texture of material {mat.name!r}: {exc}",
                                  file=sys.stderr)
                            tex_key = None
                        else:
                            if arr.shape[2] == 3:
                                arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), 255, np.uint8)], axis=2)
                            scene.textures[tex_key] = arr
                if tex_key is not None:
                    textured.add(getattr(mat, "name", None))
                alpha_test = str(getattr(mat, "alphaMode", None) or "OPAQUE").upper() in ("MASK", "BLEND")
            world = np.asarray(model, np.float64) @ np.asarray(T, np.float64)
            scene.batches.append(DrawBatch(
                positions=pos.astype(np.float32), normals=nrm.astype(np.float32), uvs=uv.astype(np.float32),
                items=np.full(len(f), item, np.uint32), model=world, base_color=tuple(factor[:3]),
                texture=tex_key, alpha_test=alpha_test, name=f"{name}/{gname}"))
    # trimesh drops base-colour images it cannot decode without raising: report every
    # material that declares one but arrived without it, by the image URI, once
    images, textures = doc.get("images", []), doc.get("textures", [])
    for mi, m in enumerate(doc.get("materials", [])):
        info = m.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if info is None or m.get("name", f"material{mi}") in textured:
            continue
        source = textures[info["index"]].get("source") if info.get("index", -1) < len(textures) else None
        uri = images[source].get("uri", f"image{source}") if source is not None and source < len(images) \
            else f"material {m.get('name', mi)}"
        if uri not in scene.unresolved_textures:
            scene.unresolved_textures.append(uri)

"""GPU depth peeling: BDP, UDP and EDP (plan sections B-E).

All image arrays are in OpenGL orientation (row 0 = bottom), shape (rows, cols).
"""
import os
from dataclasses import dataclass, field

import moderngl
import numpy as np

from . import gl
from .camera import gl_mat

EPS = 0.0005
DELTA = 0.002
MARGIN = 1.5
MAX_EXHAUSTIVE_RADIUS = 96.0
N_PATTERN = 64

MODE_NONE, MODE_BDP, MODE_UDP, MODE_EDP, MODE_CLASSIFY = 0, 1, 2, 3, 4
SEARCH_MODES = {"sampled": 0, "exhaustive": 1}
METHODS = ("bdp", "udp", "edp")

SHADER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shaders")


def _shader(name):
    with open(os.path.join(SHADER_DIR, name)) as fh:
        return fh.read()


@dataclass
class Layers:
    """Peeled layers of one method; layer 0 is the plain z-buffer image."""
    scene_name: str
    method: str
    edp_layers: object
    search: str
    camera: object
    E: float
    color: list = field(default_factory=list)    # (Hg, Wg, 4) uint8 each
    depth: list = field(default_factory=list)    # (Hg, Wg) float32 linear depth, 0 = empty
    item: list = field(default_factory=list)     # (Hg, Wg) uint32, 0 = empty
    counts: list = field(default_factory=list)   # non-empty pixels per layer
    modes: list = field(default_factory=list)    # per-layer rule name ('zbuffer', 'bdp', 'udp', 'edp')
    converged: bool = False
    max_layers: int = 128
    seconds: float = 0.0

    @property
    def n_layers(self):
        return len(self.item)

    @property
    def hidden_count(self):
        return int(sum(self.counts[1:]))

    @property
    def pixels(self):
        return self.camera.Wg * self.camera.Hg

    def head(self, n):
        """The first n layers (n = 1: single-layer warp input)."""
        out = Layers(self.scene_name, self.method, self.edp_layers, self.search, self.camera, self.E,
                     self.color[:n], self.depth[:n], self.item[:n], self.counts[:n], self.modes[:n],
                     self.converged and n >= self.n_layers, self.max_layers, self.seconds)
        return out

    def summary(self):
        return {"scene": self.scene_name, "method": self.method, "edp_layers": self.edp_layers,
                "search": self.search, "layers": self.n_layers, "converged": self.converged,
                "max_layers": self.max_layers, "counts": [int(c) for c in self.counts], "modes": self.modes,
                "hidden_fragments": self.hidden_count, "hidden_fraction": self.hidden_count / self.pixels,
                "Wg": self.camera.Wg, "Hg": self.camera.Hg, "seconds": round(self.seconds, 3)}


def layer_mode(method, h, edp_layers=2):
    if h == 0:
        return MODE_NONE
    if method == "bdp":
        return MODE_BDP
    if method == "udp":
        return MODE_UDP
    if method == "edp":
        if edp_layers == "all" or h <= int(edp_layers):
            return MODE_EDP
        return MODE_UDP
    raise ValueError(f"unknown method {method!r}")


_MODE_NAMES = {MODE_NONE: "zbuffer", MODE_BDP: "bdp", MODE_UDP: "udp", MODE_EDP: "edp", MODE_CLASSIFY: "classify"}


class _Target:
    """MRT render target: color RGBA8, depth R32F, item R32UI, flag R32UI + DEPTH32F."""

    def __init__(self, ctx, size):
        self.size = size
        self.color = ctx.texture(size, 4, dtype="f1")
        self.depth = ctx.texture(size, 1, dtype="f4")
        self.item = ctx.texture(size, 1, dtype="u4")
        self.flag = ctx.texture(size, 1, dtype="u4")
        for t in (self.color, self.depth, self.item, self.flag):
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)
            t.repeat_x = t.repeat_y = False
        self.zbuf = gl.depth32f_texture(ctx, size)
        self.fbo = ctx.framebuffer([self.color, self.depth, self.item, self.flag], self.zbuf)
        n = size[0] * size[1]
        self._zero4 = bytes(4 * n)

    def clear(self):
        self.fbo.use()
        self.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        # integer/float attachments are cleared explicitly (glClear on integer buffers is undefined)
        self.color.write(self._zero4)
        self.depth.write(self._zero4)
        self.item.write(self._zero4)
        self.flag.write(self._zero4)

    def read(self, with_flag=False):
        w, h = self.size
        out = {
            "color": np.frombuffer(self.color.read(), np.uint8).reshape(h, w, 4).copy(),
            "depth": np.frombuffer(self.depth.read(), np.float32).reshape(h, w).copy(),
            "item": np.frombuffer(self.item.read(), np.uint32).reshape(h, w).copy(),
        }
        if with_flag:
            out["flag"] = np.frombuffer(self.flag.read(), np.uint32).reshape(h, w).copy()
        return out


class SceneRenderer:
    """GPU resources of one scene plus the geometry/peeling passes."""

    def __init__(self, scene, ctx=None):
        self.scene = scene
        self.ctx = ctx or gl.create_context()
        ctx = self.ctx
        self.prog = ctx.program(vertex_shader=_shader("geometry.vert"), fragment_shader=_shader("peel.frag"))
        self.textures = {}
        for key, img in scene.textures.items():
            img = np.ascontiguousarray(img[::-1])                    # top row first -> GL bottom row first
            h, w = img.shape[:2]
            if img.ndim == 3:
                tex = ctx.texture((w, h), 4, img.tobytes(), internal_format=gl.GL_SRGB8_ALPHA8)
            else:
                tex = ctx.texture((w, h), 1, img.tobytes())
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            tex.anisotropy = 16.0
            self.textures[key] = tex
        self.draws = []
        self._buffers = []
        for b in scene.batches:
            vbo = ctx.buffer(np.hstack([b.positions, b.normals, b.uvs]).astype(np.float32).tobytes())
            ibo = ctx.buffer(np.ascontiguousarray(b.items, np.uint32).tobytes())
            vao = ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f", "in_pos", "in_normal", "in_uv"),
                                               (ibo, "1u", "in_item")])
            m3 = np.asarray(b.model, np.float64)[:3, :3]
            nm = np.linalg.inv(m3).T
            self.draws.append((vao, b, gl_mat(b.model), np.ascontiguousarray(nm.T, np.float32).tobytes()))
            self._buffers += [vbo, ibo]
        self._patterns = {}
        self._targets = {}
        self._dummy_f = ctx.texture((1, 1), 1, np.zeros(1, np.float32).tobytes(), dtype="f4")
        self._dummy_u = ctx.texture((1, 1), 1, np.zeros(1, np.uint32).tobytes(), dtype="u4")
        self._dummy_rgba = ctx.texture((1, 1), 4, bytes(4))
        self._dummy_r = ctx.texture((1, 1), 1, bytes(1))
        for t in (self._dummy_f, self._dummy_u):
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)

    def _pattern(self, n):
        """EDP probe pattern (section E): the first n probe points, zero-padded to 64 entries."""
        from .sampling import probe_samples

        if n not in self._patterns:
            if not 1 <= n <= N_PATTERN:
                raise ValueError(f"nSamples must be in 1..{N_PATTERN}")
            pat = np.zeros((N_PATTERN, 2), np.float32)
            pat[:n] = np.asarray(probe_samples(n), np.float64).astype(np.float32)
            self._patterns[n] = pat
        return self._patterns[n]

    def release(self):
        """Free the GPU objects of this scene (moderngl does not free them on garbage collection)."""
        for vao, *_ in self.draws:
            vao.release()
        for obj in self._buffers + list(self.textures.values()):
            obj.release()
        for tgt in self._targets.values():
            tgt.fbo.release()
            for t in (tgt.color, tgt.depth, tgt.item, tgt.flag, tgt.zbuf):
                t.release()
        for t in (self._dummy_f, self._dummy_u, self._dummy_rgba, self._dummy_r):
            t.release()
        self.prog.release()
        self.draws, self._buffers, self.textures, self._targets = [], [], {}, {}

    def _set(self, name, value):
        if name in self.prog:
            self.prog[name].value = value

    def _target(self, size, slot):
        key = (size, slot)
        if key not in self._targets:
            self._targets[key] = _Target(self.ctx, size)
        return self._targets[key]

    def render_pass(self, target, view, proj, mode, prev=None, search="sampled", n_samples=None,
                    eps=EPS, delta=DELTA, margin=MARGIN):
        """One geometry pass into ``target``; ``prev`` is the _Target holding layer h-1."""
        ctx, prog, scene = self.ctx, self.prog, self.scene
        cam = scene.camera
        target.clear()
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<"
        if scene.cull_backfaces:
            ctx.enable(moderngl.CULL_FACE)
            ctx.front_face = "ccw"
            ctx.cull_face = "back"
        else:
            ctx.disable(moderngl.CULL_FACE)
        ctx.disable(moderngl.BLEND)
        target.fbo.use()
        ctx.viewport = (0, 0) + tuple(target.size)
        prog["view"].write(gl_mat(view))
        prog["proj"].write(gl_mat(proj))
        self._set("mode", int(mode))
        # texture units: 0 base, 1 alpha, 2 prevDepth, 3 prevItem
        self._set("baseTex", 0)
        self._set("alphaTex", 1)
        self._set("prevDepth", 2)
        self._set("prevItem", 3)
        if prev is not None:
            prev.depth.use(2)
            prev.item.use(3)
        else:
            self._dummy_f.use(2)
            self._dummy_u.use(3)
        self._set("srcSize", tuple(target.size))
        self._set("eps", float(eps))
        self._set("delta", float(delta))
        self._set("E", float(scene.E))
        self._set("F", float(cam.F))
        self._set("near", float(cam.near))
        self._set("far", float(cam.far))
        self._set("margin", float(margin))
        n = int(n_samples if n_samples is not None else scene.samples)
        self._set("nSamples", n)
        self._set("searchMode", SEARCH_MODES[search])
        self._set("maxExhaustiveRadius", float(MAX_EXHAUSTIVE_RADIUS))
        if "samples" in prog:
            prog["samples"].write(self._pattern(n).tobytes())
        for vao, b, model_bytes, nm_bytes in self.draws:
            prog["model"].write(model_bytes)
            if "normalMatrix" in prog:
                prog["normalMatrix"].write(nm_bytes)
            self._set("baseColor", tuple(float(c) for c in b.base_color))
            if b.texture is not None:
                self.textures[b.texture].use(0)
            else:
                self._dummy_rgba.use(0)
            if b.alpha_texture is not None:
                self.textures[b.alpha_texture].use(1)
            else:
                self._dummy_r.use(1)
            self._set("hasTexture", b.texture is not None)
            self._set("hasAlphaTex", b.alpha_texture is not None)
            self._set("alphaTest", bool(b.alpha_test))
            vao.render(moderngl.TRIANGLES)
        ctx.finish()

    # -- public passes -----------------------------------------------------------------
    def reference(self, t=(0.0, 0.0)):
        """Direct render of the novel view translated by t (no guard band, W x H)."""
        cam = self.scene.camera
        size = cam.novel_size
        tgt = self._target(size, "ref")
        self.render_pass(tgt, cam.view_matrix(t), cam.projection_matrix(*size), MODE_NONE)
        return tgt.read()

    def peel(self, method, edp_layers=2, max_layers=128, search="sampled", n_samples=None,
             eps=EPS, delta=DELTA, margin=MARGIN):
        import time

        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")
        if search not in SEARCH_MODES:
            raise ValueError(f"search must be one of {tuple(SEARCH_MODES)}")
        t0 = time.perf_counter()
        scene, cam = self.scene, self.scene.camera
        size = cam.source_size
        view, proj = cam.view_matrix(), cam.projection_matrix(*size)
        res = Layers(scene.name, method, edp_layers, search, cam, scene.E, max_layers=int(max_layers))
        targets = [self._target(size, "a"), self._target(size, "b")]
        cur = targets[0]
        self.render_pass(cur, view, proj, MODE_NONE)
        self._append(res, cur.read(), MODE_NONE)
        converged = False
        for h in range(1, int(max_layers) + 1):
            prev, cur = cur, targets[h % 2]
            mode = layer_mode(method, h, edp_layers)
            self.render_pass(cur, view, proj, mode, prev=prev, search=search, n_samples=n_samples,
                             eps=eps, delta=delta, margin=margin)
            data = cur.read()
            if not np.any(data["item"]):
                converged = True
                break
            if h == int(max_layers):           # probe beyond the cap: non-empty -> not converged
                break
            self._append(res, data, mode)
        res.converged = converged
        res.seconds = time.perf_counter() - t0
        return res

    @staticmethod
    def _append(res, data, mode):
        res.color.append(data["color"])
        res.depth.append(data["depth"])
        res.item.append(data["item"])
        res.counts.append(int(np.count_nonzero(data["item"])))
        res.modes.append(_MODE_NAMES[mode])

    def classify_layer1(self, search="sampled", n_samples=None, eps=EPS, delta=DELTA, margin=MARGIN):
        """BDP layer 1 with the inPVHV result of every surviving fragment (debug pass).

        Returns dict: prev_depth/prev_item (layer 0), depth/item/color (BDP layer 1),
        mask (layer-1 fragment present) and in_pvhv (bool, meaningful where mask)."""
        cam = self.scene.camera
        size = cam.source_size
        view, proj = cam.view_matrix(), cam.projection_matrix(*size)
        a, b = self._target(size, "a"), self._target(size, "b")
        self.render_pass(a, view, proj, MODE_NONE)
        l0 = a.read()
        self.render_pass(b, view, proj, MODE_CLASSIFY, prev=a, search=search, n_samples=n_samples,
                         eps=eps, delta=delta, margin=margin)
        l1 = b.read(with_flag=True)
        return {"prev_depth": l0["depth"], "prev_item": l0["item"], "depth": l1["depth"], "item": l1["item"],
                "color": l1["color"], "mask": l1["item"] != 0, "in_pvhv": l1["flag"] == 2}


_RENDERERS = {}


def get_renderer(scene, ctx=None):
    """SceneRenderer cached per scene object."""
    key = (id(scene), id(ctx) if ctx is not None else None)
    r = _RENDERERS.get(key)
    if r is None or r.scene is not scene:
        r = SceneRenderer(scene, ctx)
        _RENDERERS[key] = r
    return r


def peel(scene, method, edp_layers=2, max_layers=128, search="sampled", renderer=None, **kw):
    """Peel ``scene`` with ``method`` in {'bdp','udp','edp'} -> Layers.

    ``edp_layers``: number of hidden layers that use the EDP rule (int or 'all');
    deeper layers use UDP (paper configuration: 2)."""
    r = renderer or get_renderer(scene)
    return r.peel(method, edp_layers=edp_layers, max_layers=max_layers, search=search, **kw)


def classify_layer1(scene, search="sampled", renderer=None, **kw):
    r = renderer or get_renderer(scene)
    return r.classify_layer1(search=search, **kw)


def reference(scene, t=(0.0, 0.0), renderer=None):
    r = renderer or get_renderer(scene)
    return r.reference(t)

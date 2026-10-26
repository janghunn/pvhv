"""Forward warping of peeled layers to a novel view (plan section G).

GPU point splats (4 subpixel samples per fragment) with a hardware depth test on
normalized linear depth; layers are drawn front (layer 0) to back, ties keep the
earlier draw.  Arrays are in OpenGL orientation (row 0 = bottom).
"""
from dataclasses import dataclass

import moderngl
import numpy as np

from . import gl
from .peel import _shader


@dataclass
class WarpResult:
    color: np.ndarray   # (H, W, 4) uint8
    depth: np.ndarray   # (H, W) float32 linear depth (0 = hole)
    item: np.ndarray    # (H, W) uint32 (0 = hole)
    layer: np.ndarray   # (H, W) uint32 layer index + 1 (0 = hole)

    @property
    def holes(self):
        return self.layer == 0


class LayerWarper:
    """Uploads a Layers object once and warps it to any number of novel views."""

    def __init__(self, layers, ctx=None):
        self.ctx = ctx = ctx or gl.create_context()
        self.layers = layers
        cam = layers.camera
        self.prog = ctx.program(vertex_shader=_shader("warp.vert"), fragment_shader=_shader("warp.frag"))
        self.vao = ctx.vertex_array(self.prog, [])
        self.src = cam.source_size
        self.dst = cam.novel_size
        self.tex = []
        for c, d, i in zip(layers.color, layers.depth, layers.item):
            tc = ctx.texture(self.src, 4, np.ascontiguousarray(c).tobytes())
            td = ctx.texture(self.src, 1, np.ascontiguousarray(d, np.float32).tobytes(), dtype="f4")
            ti = ctx.texture(self.src, 1, np.ascontiguousarray(i, np.uint32).tobytes(), dtype="u4")
            for t in (tc, td, ti):
                t.filter = (moderngl.NEAREST, moderngl.NEAREST)
            self.tex.append((tc, td, ti))
        W, H = self.dst
        self.out = [ctx.texture(self.dst, 4, dtype="f1"), ctx.texture(self.dst, 1, dtype="f4"),
                    ctx.texture(self.dst, 1, dtype="u4"), ctx.texture(self.dst, 1, dtype="u4")]
        for t in self.out:
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.zbuf = gl.depth32f_texture(ctx, self.dst)
        self.fbo = ctx.framebuffer(self.out, self.zbuf)
        self._zero = bytes(4 * W * H)

    def warp(self, t, n_layers=None):
        ctx, prog, cam = self.ctx, self.prog, self.layers.camera
        n = len(self.tex) if n_layers is None else min(int(n_layers), len(self.tex))
        self.fbo.use()
        self.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        for t_ in self.out:
            t_.write(self._zero)
        ctx.viewport = (0, 0) + tuple(self.dst)
        ctx.enable(moderngl.DEPTH_TEST | moderngl.PROGRAM_POINT_SIZE)
        ctx.disable(moderngl.CULL_FACE | moderngl.BLEND)
        ctx.depth_func = "<"
        prog["layerColor"].value = 0
        prog["layerDepth"].value = 1
        prog["layerItem"].value = 2
        prog["srcSize"].value = tuple(self.src)
        prog["dstSize"].value = tuple(float(x) for x in self.dst)
        prog["t"].value = (float(t[0]), float(t[1]))
        prog["F"].value = float(cam.F)
        prog["near"].value = float(cam.near)
        prog["far"].value = float(cam.far)
        count = self.src[0] * self.src[1]
        for h in range(n):
            tc, td, ti = self.tex[h]
            tc.use(0)
            td.use(1)
            ti.use(2)
            prog["layerIndex"].value = h
            self.vao.render(moderngl.POINTS, vertices=count, instances=4)
        ctx.finish()
        W, H = self.dst
        return WarpResult(
            color=np.frombuffer(self.out[0].read(), np.uint8).reshape(H, W, 4).copy(),
            depth=np.frombuffer(self.out[1].read(), np.float32).reshape(H, W).copy(),
            item=np.frombuffer(self.out[2].read(), np.uint32).reshape(H, W).copy(),
            layer=np.frombuffer(self.out[3].read(), np.uint32).reshape(H, W).copy())

    def release(self):
        for group in self.tex:
            for t in group:
                t.release()
        for t in self.out:
            t.release()
        self.zbuf.release()
        self.fbo.release()
        self.vao.release()
        self.prog.release()


def warp(layers, t, n_layers=None, warper=None, ctx=None):
    """Warp ``layers`` (all, or the first ``n_layers``) to the view translated by t.

    Pass the context that peeled the layers as ``ctx`` (or a ``warper``); without
    either, a context on the default EGL device (PVHV_EGL_DEVICE) is used."""
    w = warper or LayerWarper(layers, ctx)
    try:
        return w.warp(t, n_layers=n_layers)
    finally:
        if warper is None:
            w.release()


def linear_to_srgb8(c):
    c = np.clip(c, 0.0, 1.0)
    s = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)
    return np.round(s * 255.0).astype(np.uint8)

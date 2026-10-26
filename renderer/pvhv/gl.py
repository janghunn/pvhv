"""Headless OpenGL 4.3 core context over EGL (moderngl).

The EGL device is chosen by ``device_index`` (CLI ``--egl-device``), else by the
environment variable ``PVHV_EGL_DEVICE``, else 0.  Note that the EGL device
order need not match the ``nvidia-smi`` GPU order.
"""
import ctypes
import os

import moderngl

GL_TEXTURE_2D = 0x0DE1
GL_DEPTH_COMPONENT = 0x1902
GL_DEPTH_COMPONENT32F = 0x8CAC
GL_FLOAT = 0x1406
GL_TEXTURE_INTERNAL_FORMAT = 0x1003
GL_SRGB8_ALPHA8 = 0x8C43

_CONTEXTS = {}
_LIBGL = None


def device_index_from_env(device_index=None):
    """Resolve the EGL device index: explicit value, then PVHV_EGL_DEVICE, then 0."""
    if device_index is not None:
        return int(device_index)
    return int(os.environ.get("PVHV_EGL_DEVICE", "0"))


def create_context(device_index=None):
    """Return a (cached, per device) standalone EGL context requiring GL 4.3."""
    idx = device_index_from_env(device_index)
    ctx = _CONTEXTS.get(idx)
    if ctx is None:
        ctx = moderngl.create_context(standalone=True, backend="egl", require=430, device_index=idx)
        ctx.extra = {"device_index": idx}
        _CONTEXTS[idx] = ctx
    return ctx


def renderer_string(ctx):
    """GL renderer and version, e.g. 'NVIDIA RTX ... / 4.3.0 NVIDIA ...'."""
    return f"{ctx.info['GL_RENDERER']} | {ctx.info['GL_VERSION']}"


def _libgl():
    global _LIBGL
    if _LIBGL is None:
        _LIBGL = ctypes.CDLL("libOpenGL.so.0")
    return _LIBGL


def depth32f_texture(ctx, size):
    """Depth texture with DEPTH_COMPONENT32F storage.

    moderngl only allocates DEPTH_COMPONENT24, so the storage of the texture object
    it creates is re-specified with glTexImage2D (same texture name, so moderngl
    framebuffers attach it unchanged).  The format is checked after the call.
    """
    tex = ctx.depth_texture(size)
    gl = _libgl()
    gl.glBindTexture(GL_TEXTURE_2D, tex.glo)
    gl.glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT32F, int(size[0]), int(size[1]), 0,
                    GL_DEPTH_COMPONENT, GL_FLOAT, None)
    fmt = ctypes.c_int()
    gl.glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, ctypes.byref(fmt))
    gl.glBindTexture(GL_TEXTURE_2D, 0)
    if fmt.value != GL_DEPTH_COMPONENT32F:
        raise RuntimeError(f"could not allocate DEPTH_COMPONENT32F (got 0x{fmt.value:x})")
    return tex

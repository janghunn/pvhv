"""Shared fixtures for the GPU tests (marker ``gpu``).

GPU tests are skipped when no EGL OpenGL 4.3 context on an NVIDIA GPU can be
created, unless ``PVHV_REQUIRE_GPU=1``: then a missing context is a test error.
"""
import os

import pytest


@pytest.fixture(scope="session")
def gl_ctx():
    try:
        from pvhv import gl
        ctx = gl.create_context()
        renderer = gl.renderer_string(ctx)
        if "NVIDIA" not in renderer:
            raise RuntimeError(f"EGL context is not on an NVIDIA GPU: {renderer}")
    except Exception as exc:
        if os.environ.get("PVHV_REQUIRE_GPU") == "1":
            raise
        pytest.skip(f"no EGL OpenGL 4.3 NVIDIA context: {exc}")
    print(f"\nGL renderer: {renderer} (EGL device {gl.device_index_from_env()})")
    return ctx


@pytest.fixture(scope="session")
def blocks(gl_ctx):
    from pvhv.scene import load_scene
    return load_scene("blocks")


@pytest.fixture(scope="session")
def blocks_renderer(gl_ctx, blocks):
    from pvhv.peel import SceneRenderer
    return SceneRenderer(blocks, gl_ctx)


@pytest.fixture(scope="session")
def blocks_layers(blocks_renderer):
    """Full peels of blocks: BDP, UDP and EDP (paper configuration, sampled search)."""
    return {m: blocks_renderer.peel(m, edp_layers=2, max_layers=128, search="sampled") for m in ("bdp", "udp", "edp")}

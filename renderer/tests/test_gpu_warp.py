"""Forward warping tests (plan tests 8 and 12)."""
import numpy as np
import pytest

from pvhv.warp import LayerWarper

pytestmark = pytest.mark.gpu


@pytest.mark.parametrize("method", ["bdp", "udp", "edp"])
def test_zero_offset_warp_is_central_crop_of_layer0(blocks, blocks_layers, gl_ctx, method):
    layers = blocks_layers[method]
    cam = blocks.camera
    w = LayerWarper(layers, gl_ctx).warp((0.0, 0.0))
    crop = (slice(cam.oy, cam.oy + cam.H), slice(cam.ox, cam.ox + cam.W))
    assert np.array_equal(w.item, layers.item[0][crop])
    assert np.array_equal(w.depth.view(np.uint32), layers.depth[0][crop].view(np.uint32))
    assert np.array_equal(w.color, layers.color[0][crop])
    assert np.all((w.layer == 1) == (w.item != 0)) and not np.any(w.layer > 1)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_bdp_warp_matches_direct_reference(blocks, blocks_layers, blocks_renderer, gl_ctx, axis):
    E = blocks.E
    t = (E, 0.0) if axis == "x" else (0.0, E)
    w = LayerWarper(blocks_layers["bdp"], gl_ctx).warp(t)
    ref = blocks_renderer.reference(t)
    fg = ref["item"] != 0
    agree = np.count_nonzero(w.item[fg] == ref["item"][fg]) / np.count_nonzero(fg)
    # the hidden layers must matter for this view (otherwise the check is weak)
    single = LayerWarper(blocks_layers["bdp"].head(1), gl_ctx).warp(t)
    agree_single = np.count_nonzero(single.item[fg] == ref["item"][fg]) / np.count_nonzero(fg)
    print(f"\nt={t}: item agreement BDP-full {agree:.5f}, single-layer {agree_single:.5f}")
    assert agree >= 0.98
    # sign/intrinsics errors would leave the warp misaligned with the reference
    mirrored = LayerWarper(blocks_layers["bdp"], gl_ctx).warp((-t[0], -t[1]))
    assert np.count_nonzero(mirrored.item[fg] == ref["item"][fg]) / np.count_nonzero(fg) < agree


def test_cli_warp_uses_only_the_selected_egl_device(gl_ctx, monkeypatch, tmp_path):
    """--egl-device must win over PVHV_EGL_DEVICE for every pass of a command (peel and warp)."""
    from pvhv import cli, gl

    monkeypatch.setenv("PVHV_EGL_DEVICE", "1")
    monkeypatch.setattr(gl, "_CONTEXTS", {})
    out = tmp_path / "warp.png"
    rc = cli.main(["--egl-device", "0", "warp", "--scene", "blocks", "--method", "edp", "--t", "0", "0",
                   "--out", str(out)])
    assert rc == 0 and out.is_file()
    assert set(gl._CONTEXTS) == {0}

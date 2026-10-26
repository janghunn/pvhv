"""GPU peeling tests on the procedural blocks scene (plan tests 9 and 10)."""
import numpy as np
import pytest

from pvhv.peel import EPS

pytestmark = pytest.mark.gpu

# GLSL float division is not IEEE-exact on the GPU; the numpy re-evaluation of
# zn = (z - near)/(far - near) may differ by a few ulp at the threshold.
ULP_SLACK = 1e-6


def _zn(cam, z):
    return (np.asarray(z, np.float32) - np.float32(cam.near)) / np.float32(cam.far - cam.near)


def test_bdp_layers_ordered_propagating_and_converged(blocks, blocks_layers):
    bdp = blocks_layers["bdp"]
    cam = blocks.camera
    assert bdp.converged, "BDP did not converge within 128 layers"
    assert bdp.n_layers >= 3, "blocks must have at least two hidden BDP layers"
    assert all(c > 0 for c in bdp.counts)
    for h in range(1, bdp.n_layers):
        cur, prev = bdp.item[h] != 0, bdp.item[h - 1] != 0
        assert not np.any(cur & ~prev), f"layer {h} has fragments where layer {h - 1} is empty"
        zn_h = _zn(cam, bdp.depth[h][cur])
        zn_p = _zn(cam, bdp.depth[h - 1][cur])
        assert np.all(zn_h > zn_p + np.float32(EPS) - np.float32(ULP_SLACK)), f"layer {h} violates zn > zn_prev + eps"
        assert np.count_nonzero(cur) == bdp.counts[h]


def test_edp_layers_obey_rule_c(blocks, blocks_layers):
    from pvhv.pvhv_cpu import algorithm1, umbra_end

    edp, udp, bdp = blocks_layers["edp"], blocks_layers["udp"], blocks_layers["bdp"]
    cam = blocks.camera
    print(f"\nhidden fragments: BDP {bdp.hidden_count}, UDP {udp.hidden_count}, EDP {edp.hidden_count}")
    assert edp.converged
    assert edp.hidden_count < bdp.hidden_count
    assert edp.modes[1:3] == ["edp", "edp"] and all(m == "udp" for m in edp.modes[3:])
    for h in range(1, edp.n_layers):
        cur, prev = edp.item[h] != 0, edp.item[h - 1] != 0
        assert not np.any(cur & ~prev), f"EDP layer {h}: fragments over an empty pixel of layer {h - 1}"
        qz, pz = edp.depth[h - 1][cur], edp.depth[h][cur]
        pn, qn = _zn(cam, pz), _zn(cam, qz)
        if edp.modes[h] == "edp":
            assert np.all(pn > qn + np.float32(EPS) - np.float32(ULP_SLACK))
            keep = algorithm1(edp.depth[h - 1], edp.item[h - 1], edp.depth[h], cur, blocks.E, cam.F, cam.near,
                              cam.far, delta=0.002, margin=1.5, n_samples=blocks.samples, search="sampled")
            outside = np.count_nonzero(cur & ~keep)
            # same float-rounding tolerance as the classifier cross-check (test 11)
            assert outside <= 1e-4 * np.count_nonzero(cur), f"EDP layer {h}: {outside} fragments outside the PVHV"
        else:
            un = _zn(cam, umbra_end(qz, blocks.E, cam.F).astype(np.float32))
            assert np.all(np.isfinite(un))
            assert np.all(pn > np.maximum(qn + np.float32(EPS), un) - np.float32(ULP_SLACK))

"""Verification gates G1-G3 on blocks (plan test 13)."""
import pytest

from pvhv.metrics import G2_MAX, G2_MEAN, G3_MIN, verify_scene

pytestmark = pytest.mark.gpu


@pytest.fixture(scope="module")
def blocks_verify(blocks, blocks_renderer):
    return verify_scene(blocks, n_views=16, renderer=blocks_renderer, log=print)


def test_g1_edp_culls_and_all_converge(blocks_verify):
    m = blocks_verify["methods"]
    assert blocks_verify["gates"]["converged"]
    assert m["edp"]["hidden_fragments"] < m["bdp"]["hidden_fragments"]


def test_g2_edp_warp_loses_nothing_bdp_gets_right(blocks_verify):
    """G2 (round 1): L_v = fraction of pixels where the BDP warp matches the direct reference and
    the EDP warp does not."""
    assert blocks_verify["L_max"] <= G2_MAX, f"max L_v {blocks_verify['L_max']:.3e}"
    assert blocks_verify["L_mean"] <= G2_MEAN, f"mean L_v {blocks_verify['L_mean']:.3e}"


def test_g3_views_expose_hidden_layers(blocks_verify):
    assert blocks_verify["Hf_boundary_mean"] >= G3_MIN

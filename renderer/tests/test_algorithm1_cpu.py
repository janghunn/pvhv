"""Synthetic raster cases for Algorithm 1 and the edge-based reference."""

import numpy as np
import pytest

from pvhv import pvhv_cpu
from pvhv.pvhv_cpu import algorithm1, reference_edge_test
from pvhv.sampling import disk_samples


def _flat_map(size=65):
    depth = np.full((size, size), 2.0, dtype=np.float32)
    item = np.ones(depth.shape, dtype=np.uint32)
    candidate = np.full(depth.shape, 4.0, dtype=np.float32)
    mask = np.zeros(depth.shape, dtype=bool)
    return depth, item, candidate, mask


def _classify(depth, item, candidate, mask, search, **kwargs):
    params = dict(E=0.16, F=100.0, near=0.0, far=10.0)
    params.update(kwargs)
    return algorithm1(depth, item, candidate, mask, search=search, **params)


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
@pytest.mark.parametrize("background", ["empty", "farther"])
def test_step_edge_distance(search, background):
    depth, item, candidate, mask = _flat_map(97)
    edge, y = 64, 48
    if background == "empty":
        item[:, edge:] = 0
        depth[:, edge:] = 0
    else:
        depth[:, edge:] = 6
    xs = np.arange(edge - 20, edge)
    mask[y, xs] = True
    result = _classify(depth, item, candidate, mask, search)
    d = edge - xs
    effective_radius = 100 * 0.16 * (1 / 2 - 1 / 4) + 1.5
    if search == "exhaustive":
        outside_one_pixel_band = np.abs(d - effective_radius) > 1
        np.testing.assert_array_equal(
            result[y, xs][outside_one_pixel_band], (d <= effective_radius)[outside_one_pixel_band]
        )
    assert not np.any(result[y, xs[d > effective_radius + 1]])
    assert np.all(result[y, xs[d <= 1]])
    assert not np.any(result[~mask])


def test_sampled_probes_reach_step_in_outer_radius_band(monkeypatch):
    depth, item, candidate, mask = _flat_map(161)
    x, edge = 64, 95
    rows = np.arange(40, 104)
    item[:, edge:] = 0
    depth[:, edge:] = 0
    mask[rows, x] = True
    # E=1.22 gives R_eff=100*1.22*(1/2-1/4)+1.5=32 px.
    effective_radius = 32.0
    distance = edge - x
    assert 0.94 * effective_radius < distance <= effective_radius
    # Rounding to a texel cannot let any old probe reach the step either.
    assert np.linalg.norm(disk_samples(14), axis=1).max() * effective_radius < distance - 0.5
    exhaustive = _classify(depth, item, candidate, mask, "exhaustive", E=1.22)
    sampled = _classify(depth, item, candidate, mask, "sampled", E=1.22)
    assert np.all(exhaustive[rows, x])
    assert np.any(sampled[rows, x])
    # Reproduce the old loop with the original disk pattern, then restore it.
    with monkeypatch.context() as patch:
        patch.setattr(pvhv_cpu, "probe_samples", disk_samples)
        old_sampled = _classify(depth, item, candidate, mask, "sampled", E=1.22)
    assert not np.any(old_sampled)
    assert not np.any(sampled[~mask])


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
def test_equal_depth_item_edge(search):
    depth, item, candidate, mask = _flat_map()
    item[:, 33:] = 2
    mask[32, 32] = True
    assert _classify(depth, item, candidate, mask, search)[32, 32]


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
@pytest.mark.parametrize("step,expected", [(0.25, False), (0.5, False), (0.75, True)])
def test_depth_connectivity_threshold(search, step, expected):
    depth, item, candidate, mask = _flat_map()
    depth[:, 33:] += step
    mask[32, 32] = True
    # Power-of-two values make equality at delta exactly representable.
    result = _classify(depth, item, candidate, mask, search, far=8, delta=0.0625)
    assert bool(result[32, 32]) == expected


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
@pytest.mark.parametrize("step,expected", [(0.25, False), (0.5, False), (0.75, True)])
def test_nearer_surface_line17_approximation(search, step, expected):
    depth, item, candidate, mask = _flat_map()
    depth[:, 33:] -= step
    mask[32, 32] = True
    result = _classify(depth, item, candidate, mask, search, far=8, delta=0.0625)
    assert bool(result[32, 32]) == expected


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
def test_border_is_conservative_and_connected_interior_is_culled(search):
    depth, item, candidate, mask = _flat_map()
    mask[32, [0, 32, 64]] = True
    result = _classify(depth, item, candidate, mask, search)
    np.testing.assert_array_equal(result[32, [0, 32, 64]], [True, False, True])
    assert result.dtype == bool


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
def test_candidate_mask_and_empty_propagation(search):
    depth, item, candidate, mask = _flat_map()
    item[32, 32], depth[32, 32] = 0, 0
    mask[32, 32] = True
    # A hole at the candidate's previous layer cannot produce a peeled pixel.
    assert not np.any(_classify(depth, item, candidate, mask, search))
    mask[:] = False
    candidate[:] = 0  # Empty/unselected candidate depths need not be valid.
    assert not np.any(_classify(depth, item, candidate, mask, search))


def test_exhaustive_radius_cap_is_strict_and_conservative():
    depth, item, candidate, mask = _flat_map(201)
    mask[100, 100] = True
    # No edge lies in either disk. Only exceeding the cap should force a keep.
    at_cap = _classify(depth, item, candidate, mask, "exhaustive", E=3.84, margin=0)
    over_cap = _classify(depth, item, candidate, mask, "exhaustive", E=3.88, margin=0)
    assert not at_cap[100, 100]
    assert over_cap[100, 100]
    assert not _classify(depth, item, candidate, mask, "sampled", E=3.88, margin=0)[100, 100]


def test_exhaustive_omits_center_and_uses_closed_disk():
    depth, item, candidate, mask = _flat_map()
    mask[32, 32] = True
    item[32, 33] = 2
    # Radius exactly one includes the neighbour; a subpixel radius excludes it.
    assert _classify(depth, item, candidate, mask, "exhaustive", E=0, margin=1)[32, 32]
    assert not _classify(depth, item, candidate, mask, "exhaustive", E=0, margin=0.99)[32, 32]


def test_invalid_search_and_sample_count():
    maps = _flat_map()
    with pytest.raises(ValueError, match="search"):
        _classify(*maps, search="unknown")
    with pytest.raises(ValueError, match="n_samples"):
        _classify(*maps, search="sampled", n_samples=65)


def test_edge_reference_matches_exact_raycast_on_1d_step_heightfield():
    rng = np.random.default_rng(3592108)
    depth, item, candidate, mask = _flat_map(97)
    edge, y, F = 64, 48, 100.0
    depth[:, edge:] = 8.0
    xs = np.arange(44, edge)
    mask[y, xs] = True
    cx = depth.shape[1] / 2
    # The reference edge fragment's center defines the continuous half-plane.
    edge_x = (edge - 1 + 0.5 - cx) * 2.0 / F
    seen = hidden = 0
    for _ in range(128):
        E = rng.uniform(0.05, 0.4)
        candidate[y, xs] = rng.uniform(2.5, 5.5, xs.size)
        pz = candidate[y, xs].astype(np.float64)
        px = (xs + 0.5 - cx) * pz / F
        # All possible segment/half-plane intersections range between these
        # endpoint intersections. The background at depth 8 is behind p.
        lens = np.array([-E, E])
        hits = lens[None, :] + (2.0 / pz)[:, None] * (px[:, None] - lens[None, :])
        truth = np.any(hits > edge_x, axis=1)
        result = reference_edge_test(depth, item, candidate, mask, E, F, 0, 10, margin=0)
        np.testing.assert_array_equal(result[y, xs], truth)
        assert not np.any(result[~mask])
        seen += truth.sum()
        hidden += (~truth).sum()
    assert seen > 100 and hidden > 100


def test_edge_reference_uses_each_edges_own_depth():
    depth, item, candidate, mask = _flat_map(97)
    depth[:, 57] = 1.0
    item[:, 58:] = 0
    depth[:, 58:] = 0
    mask[48, 48] = True
    # f at x=57 has radius 15 px; using qz=2 at p would give only 5 px.
    result = reference_edge_test(depth, item, candidate, mask, 0.2, 100, 0, 10, margin=0)
    assert result[48, 48]
    assert not _classify(depth, item, candidate, mask, "exhaustive", E=0.2, margin=0)[48, 48]

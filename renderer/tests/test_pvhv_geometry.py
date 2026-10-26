"""Geometric checks, including an independent segment/half-plane oracle."""

import numpy as np
import pytest

from pvhv.pvhv_cpu import (
    in_pvhv_circular_full,
    in_pvhv_circular_half,
    in_pvhv_edge_line,
    in_pvhv_linear,
    lcoc_px,
    lcoc_world,
    umbra_end,
)


def test_lcoc_equation_and_projection():
    pz = np.array([3.0, 6.0, 9.0])
    fz = np.array([1.0, 2.0, 4.0])
    E, F = 0.4, 800.0
    np.testing.assert_allclose(lcoc_world(pz, fz, E), (pz - fz) / fz * E, rtol=1e-15)
    np.testing.assert_allclose(lcoc_px(pz, fz, E, F), F * E * (1 / fz - 1 / pz), rtol=1e-15)
    np.testing.assert_allclose(lcoc_px(pz, fz, E, F) * pz / F, lcoc_world(pz, fz, E), rtol=1e-15)


def test_no_lcoc_at_or_before_blocker_or_undefined_depth():
    pz, fz = [2.0, 1.0, 0.0, 4.0, -1.0], [2.0, 2.0, 2.0, 0.0, -2.0]
    with np.errstate(all="raise"):
        np.testing.assert_array_equal(lcoc_world(pz, fz, 0.5), np.zeros(5))
        np.testing.assert_array_equal(lcoc_px(pz, fz, 0.5, 100), np.zeros(5))
    assert lcoc_world(4, 2, 0) == 0


@pytest.mark.parametrize(
    "point,expected",
    [
        ((-0.5, 0, -4), True),     # Inside the triangle, R = 1.
        ((-1.1, 0, -4), False),    # Beyond the far ray.
        ((0.5, 0, -4), False),     # Wrong side of the source->f ray.
        ((-0.1, 0, -2), False),    # At the blocker depth.
        ((-0.1, 0, -1), False),    # In front of the blocker.
        ((-1.0, 0, -4), False),    # Eq. 2 uses a strict radius test.
        ((0, 0, -4), False),       # The other triangle boundary is strict too.
        ((-0.5, 0.1, -4), False),  # Outside the epipolar plane in 3D.
    ],
)
def test_equation2_hand_cases(point, expected):
    assert bool(in_pvhv_linear(point, (0, 0, -2), (0, 0, 0), (1, 0, 0))) == expected


def test_linear_triangle_is_invariant_under_translation():
    s = np.array([4.0, -2.0, 7.0])
    assert in_pvhv_linear(s + [-0.5, 0, -4], s + [0, 0, -2], s, s + [1, 0, 0])


def test_full_and_half_cones_and_invalid_depths():
    points = np.array([[-0.5, 0.2, -4], [0.5, 0.2, -4], [-1.1, 0, -4], [-0.1, 0, -1]])
    f = (0, 0, -2)
    np.testing.assert_array_equal(in_pvhv_circular_full(points, f, 1), [True, True, False, False])
    np.testing.assert_array_equal(in_pvhv_circular_half(points, f, 1, (1, 0)), [True, False, False, False])
    np.testing.assert_array_equal(in_pvhv_circular_half(points, f, 1, (3, 0, 0)), [True, False, False, False])
    for blocker in [(0, 0, 0), (0, 0, 1)]:
        with np.errstate(all="raise"):
            assert not in_pvhv_circular_full((-0.1, 0, -2), blocker, 1)
            assert not in_pvhv_linear((-0.1, 0, -2), blocker, (0, 0, 0), (1, 0, 0))


def test_umbra_end_formula_and_unbounded_case():
    qz, E, F = np.array([2.0, 4.0, 8.0]), 0.25, 100.0
    np.testing.assert_allclose(umbra_end(qz, E, F), qz * E / (E - qz / (2 * F)))
    # a = 0.01: include equality and a zero-radius lens without division errors.
    with np.errstate(all="raise"):
        np.testing.assert_array_equal(np.isinf(umbra_end(2, [0, 0.005, 0.01], 100)), [True] * 3)


def test_straight_edge_against_dense_disk_raycast_and_fragment_union():
    rng = np.random.default_rng(20261026)
    count = 2304
    fz = rng.uniform(0.7, 2.0, count)
    pz = fz * rng.uniform(1.2, 3.0, count)
    E = rng.uniform(0.5, 2.0, count)
    # Build hidden points around arbitrary, rotated edge lines. Sampling their
    # perpendicular distances includes visible and fully occluded examples.
    angle = rng.uniform(0, 2 * np.pi, count)
    normal = np.column_stack((np.cos(angle), np.sin(angle)))
    tangent = np.column_stack((-normal[:, 1], normal[:, 0]))
    e0 = rng.uniform(-0.6, 0.6, (count, 2))
    radius = (pz - fz) / fz * E
    distance = radius * rng.uniform(0.02, 1.5, count)
    along = radius * rng.uniform(-1, 1, count)
    foot = e0 + (along * fz / pz)[:, None] * tangent
    pxy = foot * (pz / fz)[:, None] - distance[:, None] * normal
    p = np.column_stack((pxy, -pz))
    closest_f = np.column_stack((foot, -fz))

    # Exactly 4096 lens positions: 16 dense rings, including the bound.
    theta = np.tile(2 * np.pi * np.arange(256) / 256, 16)
    rings = np.repeat(np.arange(1, 17) / 16, 256)
    lens_unit = rings[:, None] * np.column_stack((np.cos(theta), np.sin(theta)))
    truth = np.zeros(count, dtype=bool)
    for start in range(0, count, 128):
        sl = slice(start, start + 128)
        lens = E[sl, None, None] * lens_unit[None, :, :]
        fraction = (fz[sl] / pz[sl])[:, None, None]
        # Exact segment intersection with the occluder plane at depth fz.
        intersection = lens + fraction * (pxy[sl, None, :] - lens)
        outside = np.sum((intersection - e0[sl, None, :]) * normal[sl, None, :], axis=-1) > 0
        truth[sl] = outside.any(axis=1)
    assert truth.sum() > 500 and (~truth).sum() > 500
    unambiguous = np.abs(distance - radius) > 1e-3 * radius
    assert unambiguous.sum() >= 2000

    wedge = in_pvhv_edge_line(p, e0, normal, fz, E)
    half = in_pvhv_circular_half(p, closest_f, E, normal)
    np.testing.assert_array_equal(wedge[unambiguous], truth[unambiguous])
    np.testing.assert_array_equal(half, wedge)

    # The segment endpoint points out through the edge; affine half-plane
    # distance means an endpoint test is exact for all views on this segment.
    n = np.column_stack((E[:, None] * normal, np.zeros(count)))
    endpoint_hit = (fz / pz)[:, None] * pxy + (1 - fz / pz)[:, None] * n[:, :2]
    segment_truth = np.sum((endpoint_hit - e0) * normal, axis=1) > 0
    linear = in_pvhv_linear(p, closest_f, np.zeros(3), n)
    np.testing.assert_array_equal(linear[unambiguous], segment_truth[unambiguous])

    # The whole edge is represented by many fragments. Include the foot and
    # cover both sides through at least R+|p|; projected spacing is <= 0.009R.
    union = np.zeros(count, dtype=bool)
    for i in range(count):
        spacing = 0.009 * radius[i] * fz[i] / pz[i]
        assert spacing <= 0.01 * radius[i]
        extent = int(np.ceil((radius[i] + np.linalg.norm(p[i])) / spacing))
        offsets = np.arange(-extent, extent + 1) * spacing
        edge_xy = foot[i] + offsets[:, None] * tangent[i]
        fragments = np.column_stack((edge_xy, np.full(offsets.size, -fz[i])))
        union[i] = np.any(in_pvhv_circular_full(p[i], fragments, E[i]))
    assert not np.any(truth & ~union), "an actually visible point must belong to the edge-fragment union"

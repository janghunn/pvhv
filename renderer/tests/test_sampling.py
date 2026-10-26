"""Known-value and range checks for the shared deterministic sample pattern."""

import numpy as np
import pytest

from pvhv.sampling import disk_samples, halton, pixel_hash01, probe_samples


@pytest.mark.parametrize(
    "i,base,expected",
    [(0, 2, 0.0), (1, 2, 0.5), (2, 2, 0.25), (3, 2, 0.75), (1, 3, 1 / 3), (3, 3, 1 / 9)],
)
def test_halton_known_values(i, base, expected):
    assert halton(i, base) == expected


@pytest.mark.parametrize("i,base", [(-1, 2), (1, 1), (0, 0)])
def test_halton_rejects_undefined_indices(i, base):
    with pytest.raises(ValueError):
        halton(i, base)


def test_disk_pattern_and_offset():
    points = disk_samples(1256)
    assert points.dtype == np.float64
    assert points.shape == (1256, 2)
    assert np.all(np.linalg.norm(points, axis=1) <= 1.0)
    np.testing.assert_allclose(points[0], [-np.sqrt(2) / 4, np.sqrt(6) / 4], atol=1e-15)
    np.testing.assert_array_equal(disk_samples(256, offset=1000), points[1000:1256])
    assert disk_samples(0).shape == (0, 2)


def test_probe_pattern_normalizes_the_fourteen_sample_disk():
    disk = disk_samples(14)
    divisor = np.linalg.norm(disk, axis=1).max()
    assert divisor == pytest.approx(np.sqrt(7 / 8), rel=0, abs=1e-15)
    probes = probe_samples(14)
    assert probes.dtype == np.float64
    assert probes.shape == (14, 2)
    assert np.linalg.norm(probes, axis=1).max() == pytest.approx(1.0, rel=0, abs=1e-15)
    np.testing.assert_array_equal(probes, disk / divisor)
    np.testing.assert_array_equal(disk_samples(14), disk)


@pytest.mark.parametrize("n", [1, 2, 64])
def test_probe_pattern_reaches_unit_circle_for_other_counts(n):
    probes = probe_samples(n)
    assert probes.dtype == np.float64
    assert probes.shape == (n, 2)
    assert np.linalg.norm(probes, axis=1).max() == pytest.approx(1.0, rel=0, abs=1e-15)


@pytest.mark.parametrize("n", [0, -1, -14])
def test_probe_pattern_rejects_nonpositive_counts(n):
    with pytest.raises(ValueError, match="at least 1"):
        probe_samples(n)


def test_pixel_hash_hard_coded_uint32_table():
    # Section E evaluated with integer arithmetic and explicit & 0xffffffff.
    expected = np.array(
        [0.651081383228302, 0.6722803711891174, 0.9346001744270325, 0.3125461935997009],
        dtype=np.float32,
    )
    actual = pixel_hash01([0, 1, 0, 1234], [0, 0, 1, 567])
    assert actual.dtype == np.float32
    np.testing.assert_array_equal(actual, expected)
    assert np.all((actual >= 0) & (actual < 1))


def test_pixel_hash_broadcasting_and_wraparound():
    # These coordinates overflow the initial multiply as well as the mixer.
    xs = np.array([0, 1, 2**31, 2**32 - 1], dtype=np.uint32)
    ys = np.array([0, 17, 2**32 - 1], dtype=np.uint32)[:, None]
    result = pixel_hash01(xs, ys)
    expected = np.empty((3, 4), dtype=np.float32)
    for j, y in enumerate(ys[:, 0]):
        for i, x in enumerate(xs):
            h = (int(x) * 1973 + int(y) * 9277 + 26699) & 0xFFFFFFFF
            h ^= h >> 16
            h = (h * 0x7FEB352D) & 0xFFFFFFFF
            h ^= h >> 15
            h = (h * 0x846CA68B) & 0xFFFFFFFF
            h ^= h >> 16
            expected[j, i] = (h >> 8) / 16777216.0
    np.testing.assert_array_equal(result, expected)
    assert np.all((result >= 0) & (result < 1))
    assert pixel_hash01(-1, 0) == pixel_hash01(2**32 - 1, 0)

"""Deterministic disk samples and the uint32 pixel hash used by the shaders."""

import math
import operator

import numpy as np


def halton(i, base):
    """Return the radical inverse of nonnegative integer *i* in *base*."""
    i, base = operator.index(i), operator.index(base)
    if i < 0 or base < 2:
        raise ValueError("halton requires i >= 0 and base >= 2")
    result, weight = 0.0, 1.0
    while i:
        i, digit = divmod(i, base)
        weight /= base
        result += digit * weight
    return result


def disk_samples(n, offset=0):
    """Return float64 Halton disk points, using indices offset+1 through offset+n.

    Keep these computations in float64. The renderer uploads the resulting
    pattern as float32, rather than reconstructing Halton points in GLSL.
    """
    n, offset = operator.index(n), operator.index(offset)
    if n < 0 or offset < 0:
        raise ValueError("n and offset must be nonnegative")
    points = np.empty((n, 2), dtype=np.float64)
    for k in range(n):
        i = offset + k + 1
        radius = math.sqrt(halton(i, 2))
        phi = 2.0 * math.pi * halton(i, 3)
        points[k] = radius * math.cos(phi), radius * math.sin(phi)
    return points


def probe_samples(n):
    """Return float64 EDP probes scaled so the outermost reaches radius one."""
    n = operator.index(n)
    if n < 1:
        raise ValueError("n must be at least 1")
    points = disk_samples(n)
    return points / np.linalg.norm(points, axis=1).max()


def pixel_hash01(x, y):
    """Hash broadcastable integer coordinates to a float32 array in [0, 1).

    All integer operations explicitly use uint32, including multiplication and
    addition, so overflow wraps without scalar-overflow warnings.
    """
    x = np.asarray(x).astype(np.uint32, copy=False)
    y = np.asarray(y).astype(np.uint32, copy=False)
    h = np.add(
        np.multiply(x, np.uint32(1973), dtype=np.uint32),
        np.multiply(y, np.uint32(9277), dtype=np.uint32),
        dtype=np.uint32,
    )
    h = np.add(h, np.uint32(26699), dtype=np.uint32)
    h = np.bitwise_xor(h, np.right_shift(h, np.uint32(16)))
    h = np.multiply(h, np.uint32(0x7FEB352D), dtype=np.uint32)
    h = np.bitwise_xor(h, np.right_shift(h, np.uint32(15)))
    h = np.multiply(h, np.uint32(0x846CA68B), dtype=np.uint32)
    h = np.bitwise_xor(h, np.right_shift(h, np.uint32(16)))
    upper = np.asarray(np.right_shift(h, np.uint32(8)), dtype=np.float32)
    return np.asarray(upper / np.float32(16777216.0), dtype=np.float32)

"""NumPy reference of the paper's geometry and the specified Algorithm 1.

World points use OpenGL camera coordinates: the source looks down -Z and
positive linear depth is -Z. Raster maps are indexed [y, x], with y increasing
upwards. These functions have no OpenGL dependency.
"""

import operator

import numpy as np

from .sampling import pixel_hash01, probe_samples


def lcoc_world(pz, fz, E):
    """Equation 1's radius at pz; return zero unless pz > fz > 0.

    Inputs broadcast. E is the nonnegative lens radius in world units.
    """
    pz, fz, E = np.broadcast_arrays(
        np.asarray(pz, dtype=np.float64),
        np.asarray(fz, dtype=np.float64),
        np.asarray(E, dtype=np.float64),
    )
    if np.any(E < 0):
        raise ValueError("E must be nonnegative")
    ratio = np.zeros(pz.shape, dtype=np.float64)
    np.divide(pz - fz, fz, out=ratio, where=(pz > fz) & (fz > 0))
    return ratio * E


def lcoc_px(pz, fz, E, F):
    """Screen radius F*E*(1/fz - 1/pz), zero outside pz > fz > 0."""
    pz, fz, E, F = np.broadcast_arrays(
        *[np.asarray(v, dtype=np.float64) for v in (pz, fz, E, F)]
    )
    if np.any(F <= 0):
        raise ValueError("F must be positive")
    radius = np.zeros(pz.shape, dtype=np.float64)
    np.divide(lcoc_world(pz, fz, E) * F, pz, out=radius, where=(pz > fz) & (fz > 0))
    return radius


def _point3(point):
    point = np.asarray(point, dtype=np.float64)
    if point.ndim == 0 or point.shape[-1] != 3:
        raise ValueError("world points must have last dimension 3")
    return point


def _plane_xy(vector):
    vector = np.asarray(vector, dtype=np.float64)
    if vector.ndim == 0 or vector.shape[-1] not in (2, 3):
        raise ValueError("lens-plane vectors must have last dimension 2 or 3")
    if vector.shape[-1] == 3 and np.any(vector[..., 2] != 0):
        raise ValueError("edge normals must lie in the XY lens plane")
    return vector[..., :2]


def _source_ray_offset(p, f):
    p, f = np.broadcast_arrays(_point3(p), _point3(f))
    pz, fz = -p[..., 2], -f[..., 2]
    scale = np.zeros(pz.shape, dtype=np.float64)
    np.divide(pz, fz, out=scale, where=fz > 0)
    return pz, fz, p[..., :2] - f[..., :2] * scale[..., None]


def in_pvhv_linear(p, f, s, n):
    """Equation 2's triangle for the view segment s->n and an edge point f.

    s and n share a Z coordinate. In 3D the triangle lies in their epipolar
    plane through f; points outside that plane are excluded. All points may
    carry broadcastable leading dimensions. Triangle boundaries are excluded.
    """
    p, f, s, n = np.broadcast_arrays(*[_point3(v) for v in (p, f, s, n)])
    if np.any(n[..., 2] != s[..., 2]):
        raise ValueError("s and n must share the lens plane's Z coordinate")
    pz, fz, offset = _source_ray_offset(p - s, f - s)
    direction = (n - s)[..., :2]
    distance = np.linalg.norm(offset, axis=-1)
    extent = np.linalg.norm(direction, axis=-1)
    cross = offset[..., 0] * direction[..., 1] - offset[..., 1] * direction[..., 0]
    coplanar = np.abs(cross) <= 1e-12 * np.maximum(1.0, distance * extent)
    return (
        (pz > fz)
        & (fz > 0)
        & (distance < lcoc_world(pz, fz, extent))
        & (np.sum(offset * direction, axis=-1) < 0)
        & coplanar
    )


def in_pvhv_circular_full(p, f, E):
    """Test the full cone around the source->f ray, with source at the origin."""
    pz, fz, offset = _source_ray_offset(p, f)
    return (pz > fz) & (fz > 0) & (np.linalg.norm(offset, axis=-1) < lcoc_world(pz, fz, E))


def in_pvhv_circular_half(p, f, E, edge_normal):
    """Test the half cone on the occluded side of a straight edge through f.

    edge_normal points outwards, into the opening, in the XY plane (2 or 3
    components). Its magnitude is immaterial. The source is at the origin.
    """
    normal = _plane_xy(edge_normal)
    if np.any(np.linalg.norm(normal, axis=-1) == 0):
        raise ValueError("edge_normal must be nonzero")
    _, _, offset = _source_ray_offset(p, f)
    return in_pvhv_circular_full(p, f, E) & (np.sum(offset * normal, axis=-1) < 0)


def in_pvhv_edge_line(p, e0, d, fz, E):
    """Straight-edge wedge used by the independent visibility property test.

    e0 is a 2D/3D point on the edge at depth fz; d is its outward XY normal.
    Measure perpendicular distance at p's depth to the plane through the source
    and the entire edge line. This differs from a single edge fragment's cone.
    """
    p = _point3(p)
    e0 = np.asarray(e0, dtype=np.float64)
    if e0.ndim == 0 or e0.shape[-1] not in (2, 3):
        raise ValueError("e0 must have last dimension 2 or 3")
    fz = np.asarray(fz, dtype=np.float64)
    if e0.shape[-1] == 3 and np.any(e0[..., 2] != -fz):
        raise ValueError("e0 must lie at depth fz")
    normal = _plane_xy(d)
    length = np.linalg.norm(normal, axis=-1)
    if np.any(length == 0):
        raise ValueError("d must be nonzero")
    pz, fz = np.broadcast_arrays(-p[..., 2], fz)
    scale = np.zeros(pz.shape, dtype=np.float64)
    np.divide(pz, fz, out=scale, where=fz > 0)
    offset = p[..., :2] - e0[..., :2] * scale[..., None]
    signed_distance = np.sum(offset * (normal / length[..., None]), axis=-1)
    return (pz > fz) & (fz > 0) & (signed_distance < 0) & (-signed_distance < lcoc_world(pz, fz, E))


def umbra_end(qz, E, F):
    """End of the pixel's umbra, or infinity when E <= qz/(2F)."""
    qz, E, F = np.broadcast_arrays(
        *[np.asarray(v, dtype=np.float64) for v in (qz, E, F)]
    )
    if np.any(qz <= 0) or np.any(F <= 0) or np.any(E < 0):
        raise ValueError("umbra_end requires qz > 0, F > 0, and E >= 0")
    a = qz / (2.0 * F)
    end = np.full(qz.shape, np.inf, dtype=np.float64)
    np.divide(qz * E, E - a, out=end, where=E > a)
    return end


def _raster_inputs(prev_depth, prev_item, p_depth, p_mask, E, F, near, far, delta, margin):
    depth = np.asarray(prev_depth, dtype=np.float32)
    item = np.asarray(prev_item, dtype=np.uint32)
    candidate = np.asarray(p_depth, dtype=np.float32)
    mask = np.asarray(p_mask, dtype=bool)
    if depth.ndim != 2 or any(v.shape != depth.shape for v in (item, candidate, mask)):
        raise ValueError("depth, item, candidate depth, and mask must share a 2D shape")
    params = np.asarray([E, F, near, far, delta, margin], dtype=np.float32)
    if not np.all(np.isfinite(params)):
        raise ValueError("raster parameters must be finite")
    E, F, near, far, delta, margin = params
    if E < 0 or F <= 0 or near < 0 or far <= near or delta < 0 or margin < 0:
        raise ValueError("require E >= 0, F > 0, 0 <= near < far, delta/margin >= 0")
    if np.any(mask & (~np.isfinite(candidate) | (candidate <= 0))):
        raise ValueError("masked candidate depths must be finite and positive")
    occupied = item != 0
    if np.any(occupied & (~np.isfinite(depth) | (depth <= 0))):
        raise ValueError("occupied previous depths must be finite and positive")
    return depth, item, candidate, mask, E, F, near, far, delta, margin


def algorithm1(
    prev_depth, prev_item, p_depth, p_mask, E, F, near, far,
    delta=0.002, margin=1.5, n_samples=14, search="sampled",
):
    """Return Algorithm 1's inPVHV bit for each selected candidate fragment.

    p_mask selects fragments already accepted by the BDP depth/epsilon test;
    this function implements sections D/E, not the caller's peeling test.
    Empty previous pixels propagate as empty. Rotation, normalization, radius,
    and probe coordinates use float32 to mirror GLSL. Exhaustive search keeps
    conservatively above the fixed 96-pixel radius cap.
    """
    depth, item, candidate, mask, E, F, near, far, delta, margin = _raster_inputs(
        prev_depth, prev_item, p_depth, p_mask, E, F, near, far, delta, margin
    )
    n_samples = operator.index(n_samples)
    if not 1 <= n_samples <= 64:
        raise ValueError("n_samples must be between 1 and 64")
    if search not in ("sampled", "exhaustive"):
        raise ValueError("search must be 'sampled' or 'exhaustive'")
    result = np.zeros(depth.shape, dtype=bool)
    ys, xs = np.nonzero(mask & (item != 0))
    if not xs.size:
        return result
    qz, pz = depth[ys, xs], candidate[ys, xs]
    normalized = (depth - near) / (far - near)
    qn, qi = normalized[ys, xs], item[ys, xs]
    radius = (F * E) * (np.float32(1.0) / qz - np.float32(1.0) / pz) + margin
    keep = np.zeros(xs.size, dtype=bool)
    height, width = depth.shape

    def probe(indices, wx, wy):
        outside = (wx < 0) | (wy < 0) | (wx >= width) | (wy >= height)
        wx, wy = np.clip(wx, 0, width - 1), np.clip(wy, 0, height - 1)
        wn = normalized[wy, wx]
        return (
            outside
            | (item[wy, wx] != qi[indices])
            | (wn > qn[indices] + delta)
            | (wn < qn[indices] - delta)
        )

    if search == "sampled":
        theta = np.float32(6.28318530718) * pixel_hash01(xs, ys)
        c, s = np.cos(theta), np.sin(theta)
        centers_x = xs.astype(np.float32) + np.float32(0.5)
        centers_y = ys.astype(np.float32) + np.float32(0.5)
        indices = np.arange(xs.size)
        for dx, dy in probe_samples(n_samples).astype(np.float32):
            ox = radius * (c * dx - s * dy)
            oy = radius * (s * dx + c * dy)
            wx = np.floor(centers_x + ox).astype(np.int64)
            wy = np.floor(centers_y + oy).astype(np.int64)
            keep |= probe(indices, wx, wy)
    else:
        keep = radius > np.float32(96.0)
        remaining = radius[~keep]
        extent = max(0, int(np.floor(remaining.max()))) if remaining.size else 0
        radius_squared = radius * radius
        # Row-major enumeration, omitting the center, exactly as in section D.
        for dy in range(-extent, extent + 1):
            for dx in range(-extent, extent + 1):
                if dx == 0 and dy == 0:
                    continue
                indices = np.flatnonzero((~keep) & (np.float32(dx * dx + dy * dy) <= radius_squared))
                if indices.size:
                    keep[indices] |= probe(indices, xs[indices] + dx, ys[indices] + dy)
            if keep.all():
                break
    result[ys, xs] = keep
    return result


def reference_edge_test(
    prev_depth, prev_item, p_depth, p_mask, E, F, near, far,
    delta=0.002, margin=1.5,
):
    """Section F's brute-force edge-cone union, intended for small test maps.

    Every edge uses its own depth, without Algorithm 1's flat approximation.
    Out-of-image neighbours are treated as empty. No BDP epsilon or sampling
    is applied; p_mask specifies the candidate fragments to test.
    """
    depth, item, candidate, mask, E, F, near, far, delta, margin = _raster_inputs(
        prev_depth, prev_item, p_depth, p_mask, E, F, near, far, delta, margin
    )
    # Geometry is evaluated in float64 independently of the float32 classifier.
    normalized = (depth.astype(np.float64) - float(near)) / (float(far) - float(near))
    height, width = depth.shape
    padded_item = np.pad(item, 1, constant_values=0)
    padded_depth = np.pad(normalized, 1, constant_values=0)
    edge = np.zeros(depth.shape, dtype=bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            neighbour_item = padded_item[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
            neighbour_depth = padded_depth[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
            edge |= (neighbour_item != item) | (neighbour_depth > normalized + float(delta))
    edge &= item != 0
    ys, xs = np.nonzero(mask)
    pz = candidate[ys, xs].astype(np.float64)
    keep = np.zeros(xs.size, dtype=bool)
    for fy, fx in zip(*np.nonzero(edge)):
        fz = float(depth[fy, fx])
        radius = lcoc_px(pz, fz, float(E), float(F)) + float(margin)
        distance_squared = (xs - fx) ** 2 + (ys - fy) ** 2
        keep |= (pz > fz) & (distance_squared <= radius * radius)
        if keep.all():
            break
    result = np.zeros(depth.shape, dtype=bool)
    result[ys, xs] = keep
    return result

"""Cameras and conventions (plan section A).

OpenGL conventions: the camera looks down -Z with +Y up; pixel centers are at
(i+0.5, j+0.5) with the origin at the bottom-left.  All image arrays in this
package are stored in that orientation (row 0 = bottom row); only PNG writers
flip them.  Linear depth z = -Z_cam > 0, normalized zn = (z - near)/(far - near).
"""
from dataclasses import dataclass

import numpy as np


def _normalize(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


@dataclass
class Camera:
    eye: tuple
    target: tuple
    up: tuple
    fovy_deg: float
    W: int
    H: int
    guard: float
    near: float
    far: float

    # ---- intrinsics -------------------------------------------------------------
    @property
    def F(self):
        """Focal length in pixels, shared by the source and novel images."""
        return (self.H / 2.0) / np.tan(np.radians(self.fovy_deg) / 2.0)

    @property
    def ox(self):
        return int(round((self.guard - 1.0) * self.W / 2.0))

    @property
    def oy(self):
        return int(round((self.guard - 1.0) * self.H / 2.0))

    @property
    def Wg(self):
        return self.W + 2 * self.ox

    @property
    def Hg(self):
        return self.H + 2 * self.oy

    @property
    def source_size(self):
        return (self.Wg, self.Hg)

    @property
    def novel_size(self):
        return (self.W, self.H)

    # ---- extrinsics -------------------------------------------------------------
    def basis(self):
        """Camera axes in world space: right, up, back (= -viewing direction)."""
        back = _normalize(np.asarray(self.eye, float) - np.asarray(self.target, float))
        right = _normalize(np.cross(np.asarray(self.up, float), back))
        up = np.cross(back, right)
        return right, up, back

    def eye_at(self, t=(0.0, 0.0)):
        """Eye of the novel view translated by t = (tx, ty, 0) in source camera space."""
        right, up, _ = self.basis()
        return np.asarray(self.eye, float) + float(t[0]) * right + float(t[1]) * up

    def view_matrix(self, t=(0.0, 0.0)):
        """World -> camera matrix (4x4, row-major numpy) of the view translated by t."""
        right, up, back = self.basis()
        eye = self.eye_at(t)
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3] = right, up, back
        m[:3, 3] = -m[:3, :3] @ eye
        return m

    def projection_matrix(self, width, height):
        """Perspective with focal F (pixels) and the principal point at the image center."""
        n, f, F = self.near, self.far, self.F
        p = np.zeros((4, 4))
        p[0, 0] = 2.0 * F / width
        p[1, 1] = 2.0 * F / height
        p[2, 2] = -(f + n) / (f - n)
        p[2, 3] = -2.0 * f * n / (f - n)
        p[3, 2] = -1.0
        return p

    def normalize_depth(self, z):
        return (np.asarray(z, np.float32) - np.float32(self.near)) / np.float32(self.far - self.near)

    # ---- section A pixel mappings (numpy reference, float64) -----------------------
    def warp_pixels(self, u, v, z, t):
        """Source pixel point (u, v) at linear depth z -> novel pixel point (u', v')."""
        F = self.F
        inv = 1.0 / np.asarray(z, np.float64)
        up = self.W / 2.0 + (np.asarray(u) - self.Wg / 2.0) - F * t[0] * inv
        vp = self.H / 2.0 + (np.asarray(v) - self.Hg / 2.0) - F * t[1] * inv
        return up, vp

    def unwarp_pixels(self, up, vp, z, t):
        """Inverse of warp_pixels: novel pixel point -> source pixel point."""
        F = self.F
        inv = 1.0 / np.asarray(z, np.float64)
        u = self.Wg / 2.0 + (np.asarray(up) - self.W / 2.0) + F * t[0] * inv
        v = self.Hg / 2.0 + (np.asarray(vp) - self.H / 2.0) + F * t[1] * inv
        return u, v

    def lcoc_px(self, pz, fz, E):
        """Screen-space LCOC R_px(p_z, f_z) = F*E*(1/f_z - 1/p_z)."""
        return self.F * E * (1.0 / fz - 1.0 / pz)


def gl_mat(m):
    """Row-major numpy 4x4 -> bytes for a GLSL (column-major) mat4 uniform."""
    return np.ascontiguousarray(np.asarray(m, np.float64).T, dtype=np.float32).tobytes()

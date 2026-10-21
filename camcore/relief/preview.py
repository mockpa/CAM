"""Shaded preview of a height field (relief plan phase 7): how the relief looks under a
low light from the upper left, as an 8-bit grey image (row 0 = the back edge).
Plain numpy, no Qt; the app's relief dialog shows it."""
from __future__ import annotations

import math

import numpy as np

from .heightfield import HeightField, sample_uv


def shaded(F: HeightField, relief_mm: float, max_side: int = 480, azimuth: float = 315.0,
           altitude: float = 35.0) -> np.ndarray:
    """(rows, cols) uint8: Lambert shading of the surface Z = relief_mm x h, the light from
    `azimuth` degrees (clockwise from up, 315 = upper left) and `altitude` degrees above
    the artwork, scaled to at most `max_side` pixels."""
    scale = max_side / max(F.width, F.height)
    nx, ny = max(2, round(F.width * scale)), max(2, round(F.height * scale))
    u, v = np.meshgrid(np.linspace(0, 1, nx), np.linspace(0, 1, ny))
    z = sample_uv(F.z, u, v) * relief_mm
    px, py = F.width / (nx - 1), F.height / (ny - 1)
    dzdx = np.gradient(z, px, axis=1)
    dzdy = -np.gradient(z, py, axis=0)              # rows run from the back (Y high) to the front
    az, alt = math.radians(azimuth), math.radians(altitude)
    light = np.array([math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt)])
    n = np.stack([-dzdx, -dzdy, np.ones_like(z)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    lit = np.clip(n @ light, 0, 1)
    return np.round(40 + 215 * lit).astype(np.uint8)

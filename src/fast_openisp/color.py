"""Colour science for chart-based calibration.

OpenCV does the RGB/XYZ conversions (:func:`cv2.cvtColor`) and applies the matrices
(:func:`cv2.transform`). Only what OpenCV does not expose is implemented here: CIE L*a*b* for an
arbitrary reference white (OpenCV's Lab is fixed to D65), the chromatic adaptation matrices and
the CIEDE2000 colour difference.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

__all__ = [
    "CAT_METHODS",
    "COLORCHECKER24",
    "ILLUMINANTS",
    "NEUTRAL_INDICES",
    "SKIN_INDICES",
    "WEIGHT_PRESETS",
    "ChartPatch",
    "ReferenceTargets",
    "adaptation_matrix",
    "delta_e_2000",
    "lab_to_xyz",
    "linear_rgb_to_xyz",
    "reference_targets",
    "white_xyz",
    "xyz_to_lab",
    "xyz_to_linear_rgb",
]

# CIE 1931 2-degree chromaticity coordinates of the supported illuminants
ILLUMINANTS: dict[str, tuple[float, float]] = {
    "D65": (0.31270, 0.32900),
    "D50": (0.34567, 0.35850),
    "D55": (0.33242, 0.34743),
    "A": (0.44757, 0.40745),
    "TL84": (0.38052, 0.37713),  # CIE F11
    "E": (1.0 / 3.0, 1.0 / 3.0),
}

CAT_METHODS: dict[str, np.ndarray | None] = {
    "bradford": np.array(
        [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]]
    ),
    "cat02": np.array(
        [[0.7328, 0.4296, -0.1624], [-0.7036, 1.6975, 0.0061], [0.0030, 0.0136, 0.9834]]
    ),
    "von_kries": np.array(
        [[0.40024, 0.70760, -0.08081], [-0.22630, 1.16532, 0.04570], [0.0, 0.0, 0.91822]]
    ),
    "none": None,
}
"""Cone response matrices of the supported adaptation transforms ("none" skips adaptation)."""

_EPSILON = 216.0 / 24389.0
_KAPPA = 24389.0 / 27.0


def white_xyz(illuminant: str) -> np.ndarray:
    """XYZ of an illuminant's white point, normalised to ``Y = 1``."""
    try:
        x, y = ILLUMINANTS[illuminant]
    except KeyError:
        raise ValueError(f"Unknown illuminant {illuminant!r}") from None
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def adaptation_matrix(source: np.ndarray, target: np.ndarray, method: str) -> np.ndarray:
    """(3, 3) XYZ matrix adapting from the ``source`` white point to ``target``."""
    try:
        cone = CAT_METHODS[method]
    except KeyError:
        raise ValueError(f"Unknown adaptation method {method!r}") from None
    if cone is None:
        return np.eye(3)
    gain = (cone @ target) / (cone @ source)
    return np.linalg.inv(cone) @ np.diag(gain) @ cone


def _as_triples(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).reshape(-1, 3)


def xyz_to_lab(xyz: np.ndarray, white: np.ndarray) -> np.ndarray:
    """CIE L*a*b* of ``(N, 3)`` XYZ values relative to the ``white`` point."""
    ratio = _as_triples(xyz) / np.asarray(white, dtype=np.float64)
    cube_root = np.cbrt(np.abs(ratio)) * np.sign(ratio)
    f = np.where(ratio > _EPSILON, cube_root, (_KAPPA * ratio + 16) / 116)
    return np.stack(
        [116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1
    )


def lab_to_xyz(lab: np.ndarray, white: np.ndarray) -> np.ndarray:
    """Inverse of :func:`xyz_to_lab`."""
    lab = _as_triples(lab)
    fy = (lab[:, 0] + 16) / 116
    fx = fy + lab[:, 1] / 500
    fz = fy - lab[:, 2] / 200
    f = np.stack([fx, fy, fz], axis=1)
    cubed = f**3
    ratio = np.where(cubed > _EPSILON, cubed, (116 * f - 16) / _KAPPA)
    ratio[:, 1] = np.where(lab[:, 0] > _KAPPA * _EPSILON, fy**3, lab[:, 0] / _KAPPA)
    return ratio * np.asarray(white, dtype=np.float64)


def xyz_to_linear_rgb(xyz: np.ndarray) -> np.ndarray:
    """XYZ to linear sRGB (Rec. 709 primaries, D65), via OpenCV."""
    triples = _as_triples(xyz).astype(np.float32).reshape(-1, 1, 3)
    return cv2.cvtColor(triples, cv2.COLOR_XYZ2RGB).reshape(-1, 3).astype(np.float64)


def linear_rgb_to_xyz(rgb: np.ndarray) -> np.ndarray:
    """Linear sRGB to XYZ, via OpenCV."""
    triples = _as_triples(rgb).astype(np.float32).reshape(-1, 1, 3)
    return cv2.cvtColor(triples, cv2.COLOR_RGB2XYZ).reshape(-1, 3).astype(np.float64)


def delta_e_2000(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    """CIEDE2000 colour difference between two ``(N, 3)`` sets of Lab values."""
    lab_a, lab_b = _as_triples(lab_a), _as_triples(lab_b)
    l1, a1, b1 = lab_a[:, 0], lab_a[:, 1], lab_a[:, 2]
    l2, a2, b2 = lab_b[:, 0], lab_b[:, 1], lab_b[:, 2]

    c_bar = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360

    delta_l = l2 - l1
    delta_c = c2p - c1p
    delta_h = h2p - h1p
    delta_h = np.where(delta_h > 180, delta_h - 360, delta_h)
    delta_h = np.where(delta_h < -180, delta_h + 360, delta_h)
    delta_h = np.where(c1p * c2p == 0, 0.0, delta_h)
    delta_hp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(delta_h) / 2)

    l_bar = (l1 + l2) / 2
    cp_bar = (c1p + c2p) / 2
    h_sum = h1p + h2p
    h_diff = np.abs(h1p - h2p)
    h_bar = np.where(
        c1p * c2p == 0,
        h_sum,
        np.where(
            h_diff <= 180,
            h_sum / 2,
            np.where(h_sum < 360, (h_sum + 360) / 2, (h_sum - 360) / 2),
        ),
    )

    t = (
        1
        - 0.17 * np.cos(np.radians(h_bar - 30))
        + 0.24 * np.cos(np.radians(2 * h_bar))
        + 0.32 * np.cos(np.radians(3 * h_bar + 6))
        - 0.20 * np.cos(np.radians(4 * h_bar - 63))
    )
    s_l = 1 + (0.015 * (l_bar - 50) ** 2) / np.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * cp_bar
    s_h = 1 + 0.015 * cp_bar * t
    r_t = (
        -2
        * np.sqrt(cp_bar**7 / (cp_bar**7 + 25.0**7))
        * np.sin(np.radians(60 * np.exp(-(((h_bar - 275) / 25) ** 2))))
    )
    return np.sqrt(
        (delta_l / s_l) ** 2
        + (delta_c / s_c) ** 2
        + (delta_hp / s_h) ** 2
        + r_t * (delta_c / s_c) * (delta_hp / s_h)
    )


@dataclass(frozen=True)
class ChartPatch:
    """One patch of the reference chart."""

    index: int
    """1-based patch number, in reading order."""
    name: str
    lab_d50: tuple[float, float, float]
    """CIE L*a*b* under D50/2 degrees (BabelColor averages of the ColorChecker Classic)."""


COLORCHECKER24: tuple[ChartPatch, ...] = (
    ChartPatch(1, "dark skin", (37.99, 13.56, 14.06)),
    ChartPatch(2, "light skin", (65.71, 18.13, 17.81)),
    ChartPatch(3, "blue sky", (49.93, -4.88, -21.93)),
    ChartPatch(4, "foliage", (43.14, -13.10, 21.91)),
    ChartPatch(5, "blue flower", (55.11, 8.84, -25.40)),
    ChartPatch(6, "bluish green", (70.72, -33.40, -0.20)),
    ChartPatch(7, "orange", (62.66, 36.07, 57.10)),
    ChartPatch(8, "purplish blue", (40.02, 10.41, -45.96)),
    ChartPatch(9, "moderate red", (51.12, 48.24, 16.25)),
    ChartPatch(10, "purple", (30.33, 22.98, -21.59)),
    ChartPatch(11, "yellow green", (72.53, -23.71, 57.26)),
    ChartPatch(12, "orange yellow", (71.94, 19.36, 67.86)),
    ChartPatch(13, "blue", (28.78, 14.18, -50.30)),
    ChartPatch(14, "green", (55.26, -38.34, 31.37)),
    ChartPatch(15, "red", (42.10, 53.38, 28.19)),
    ChartPatch(16, "yellow", (81.73, 4.04, 79.82)),
    ChartPatch(17, "magenta", (51.94, 49.99, -14.57)),
    ChartPatch(18, "cyan", (51.04, -28.63, -28.64)),
    ChartPatch(19, "white", (96.54, -0.43, 1.19)),
    ChartPatch(20, "neutral 8", (81.26, -0.64, -0.34)),
    ChartPatch(21, "neutral 6.5", (66.77, -0.73, -0.50)),
    ChartPatch(22, "neutral 5", (50.87, -0.15, -0.27)),
    ChartPatch(23, "neutral 3.5", (35.66, -0.42, -1.23)),
    ChartPatch(24, "black", (20.46, -0.08, -0.97)),
)

NEUTRAL_INDICES: tuple[int, ...] = (19, 20, 21, 22, 23, 24)
"""The grey ramp, white to black."""

SKIN_INDICES: tuple[int, ...] = (1, 2, 7, 12)
"""Patches closest to skin tones: dark skin, light skin, orange, orange yellow."""

WHITE_INDEX = 19
"""Patch used to normalise exposure before fitting."""


def _preset(
    emphasis: tuple[int, ...], strong: float, neutral: float, other: float
) -> tuple[float, ...]:
    values = []
    for patch in COLORCHECKER24:
        if patch.index in emphasis:
            values.append(strong)
        elif patch.index in NEUTRAL_INDICES:
            values.append(neutral)
        else:
            values.append(other)
    return tuple(values)


WEIGHT_PRESETS: dict[str, tuple[float, ...]] = {
    "All patches": tuple(1.0 for _ in COLORCHECKER24),
    "Neutrals (grey ramp)": _preset(NEUTRAL_INDICES, 4.0, 4.0, 0.25),
    "Neutrals only": _preset(NEUTRAL_INDICES, 1.0, 1.0, 0.0),
    "Skin tones": _preset(SKIN_INDICES, 4.0, 1.0, 0.25),
}
"""Per-patch weights for the usual optimisation goals, in patch order."""


@dataclass(frozen=True)
class ReferenceTargets:
    """Reference patch colours after chromatic adaptation to the target illuminant."""

    illuminant: str
    cat: str
    white: np.ndarray
    """XYZ of the target white point."""
    xyz: np.ndarray
    """(24, 3) adapted XYZ."""
    lab: np.ndarray
    """(24, 3) Lab relative to :attr:`white`."""
    linear_rgb: np.ndarray
    """(24, 3) linear sRGB, the fit's target values."""


def reference_targets(*, adapt_to: str = "D65", cat: str = "bradford") -> ReferenceTargets:
    """Adapt the chart's D50 reference data to ``adapt_to`` using the ``cat`` transform."""
    source_white = white_xyz("D50")
    target_white = white_xyz(adapt_to)
    lab_d50 = np.array([patch.lab_d50 for patch in COLORCHECKER24])
    xyz = lab_to_xyz(lab_d50, source_white)
    xyz = xyz @ adaptation_matrix(source_white, target_white, cat).T
    return ReferenceTargets(
        illuminant=adapt_to,
        cat=cat,
        white=target_white,
        xyz=xyz,
        lab=xyz_to_lab(xyz, target_white),
        linear_rgb=xyz_to_linear_rgb(xyz),
    )

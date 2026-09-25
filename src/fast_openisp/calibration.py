"""Fit a colour correction matrix on a photographed ColorChecker Classic chart.

The chart is located by :func:`detect_chart` (OpenCV's ``mcc`` module) or by the four corners the
user drags in the GUI. Patch means are taken from the **linear, white-balanced RGB** the CCM
module itself consumes, and the matrix is fitted with :class:`cv2.ccm.ColorCorrectionModel`.

Per-patch weights are applied by repeating patches rather than through
``ColorCorrectionModel.setWeightsList``, because the weighted affine path of OpenCV 5.0 raises a
size assertion. Repetition gives the same weighted loss and works for both matrix types.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from fast_openisp.color import (
    COLORCHECKER24,
    NEUTRAL_INDICES,
    WEIGHT_PRESETS,
    WHITE_INDEX,
    ReferenceTargets,
    delta_e_2000,
    linear_rgb_to_xyz,
    reference_targets,
    xyz_to_lab,
)
from fast_openisp.config import IDENTITY_CCM, CcmRow
from fast_openisp.modules.base import SaturationValues

__all__ = [
    "DEFAULT_PATCH_SCALE",
    "CalibrationError",
    "CcmFit",
    "ChartQuad",
    "PatchError",
    "PatchSamples",
    "detect_chart",
    "evaluate",
    "fit_ccm",
    "sample_patches",
]

CHART_ROWS = 4
CHART_COLS = 6
DEFAULT_PATCH_SCALE = 0.6
"""Fraction of a cell's width and height that is averaged."""

SATURATION_LIMIT = 0.98
"""Pixels above this fraction of the HDR maximum count as clipped."""

MAX_CLIPPED_FRACTION = 0.02
"""A patch with more clipped pixels than this is excluded from the fit."""

_UNIT_SQUARE = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)


class CalibrationError(RuntimeError):
    """Raised when a matrix cannot be fitted from the given samples."""


@dataclass(frozen=True)
class ChartQuad:
    """The chart outline in image pixels: top-left, top-right, bottom-right, bottom-left.

    "Top-left" is the corner next to patch 1 (dark skin), so the orientation decides which
    patch is which.
    """

    corners: tuple[
        tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]
    ]

    @classmethod
    def from_points(cls, points: np.ndarray) -> ChartQuad:
        """Build a quad from four unordered points, assuming an upright chart."""
        pts = np.asarray(points, dtype=np.float64).reshape(4, 2)
        by_sum = np.argsort(pts[:, 0] + pts[:, 1])
        top_left, bottom_right = pts[by_sum[0]], pts[by_sum[-1]]
        rest = [pts[i] for i in by_sum[1:3]]
        top_right, bottom_left = sorted(rest, key=lambda p: -p[0])
        return cls(
            (
                (float(top_left[0]), float(top_left[1])),
                (float(top_right[0]), float(top_right[1])),
                (float(bottom_right[0]), float(bottom_right[1])),
                (float(bottom_left[0]), float(bottom_left[1])),
            )
        )

    @classmethod
    def centred(cls, width: int, height: int, *, margin: float = 0.15) -> ChartQuad:
        """A default quad covering the middle of an image with the chart's 3:2 aspect ratio."""
        cx, cy = width / 2, height / 2
        half_w = width * (0.5 - margin)
        half_h = min(height * (0.5 - margin), half_w * CHART_ROWS / CHART_COLS)
        return cls(
            (
                (cx - half_w, cy - half_h),
                (cx + half_w, cy - half_h),
                (cx + half_w, cy + half_h),
                (cx - half_w, cy + half_h),
            )
        )

    def moved(self, index: int, position: tuple[float, float]) -> ChartQuad:
        """Copy with one corner moved."""
        corners = list(self.corners)
        corners[index] = (float(position[0]), float(position[1]))
        return ChartQuad((corners[0], corners[1], corners[2], corners[3]))

    def rotated(self) -> ChartQuad:
        """Copy rotated a quarter turn, for a chart that is not in landscape orientation."""
        a, b, c, d = self.corners
        return ChartQuad((b, c, d, a))

    @property
    def array(self) -> np.ndarray:
        return np.array(self.corners, dtype=np.float32)

    def transform(self) -> np.ndarray:
        """Perspective matrix mapping the unit chart to image coordinates."""
        return cv2.getPerspectiveTransform(_UNIT_SQUARE, self.array)

    def _map(self, points: np.ndarray) -> np.ndarray:
        mapped = cv2.perspectiveTransform(
            points.reshape(-1, 1, 2).astype(np.float64), self.transform()
        )
        return mapped.reshape(-1, 2)

    def patch_centres(self) -> np.ndarray:
        """(24, 2) patch centres in image pixels, in reading order."""
        unit = np.array(
            [
                [(col + 0.5) / CHART_COLS, (row + 0.5) / CHART_ROWS]
                for row in range(CHART_ROWS)
                for col in range(CHART_COLS)
            ]
        )
        return self._map(unit)

    def patch_polygons(self, scale: float = DEFAULT_PATCH_SCALE) -> np.ndarray:
        """(24, 4, 2) sampling squares in image pixels, ``scale`` of each cell."""
        half_u = scale / (2 * CHART_COLS)
        half_v = scale / (2 * CHART_ROWS)
        polygons = []
        for row in range(CHART_ROWS):
            for col in range(CHART_COLS):
                u = (col + 0.5) / CHART_COLS
                v = (row + 0.5) / CHART_ROWS
                corners = np.array(
                    [
                        [u - half_u, v - half_v],
                        [u + half_u, v - half_v],
                        [u + half_u, v + half_v],
                        [u - half_u, v + half_v],
                    ]
                )
                polygons.append(self._map(corners))
        return np.array(polygons)


def detect_chart(image8: np.ndarray) -> ChartQuad | None:
    """Locate a ColorChecker Classic with OpenCV's ``mcc`` detector.

    ``image8`` is an 8-bit RGB image. Returns ``None`` when no chart is found, or when the
    installed OpenCV has no ``mcc`` module.
    """
    if not hasattr(cv2, "mcc"):
        return None
    bgr = cv2.cvtColor(image8, cv2.COLOR_RGB2BGR)
    detector = cv2.mcc.CCheckerDetector.create()
    if not detector.process(bgr, cv2.mcc.MCC24):
        return None
    checker = detector.getBestColorChecker()
    if checker is None:
        return None
    return ChartQuad.from_points(np.array(checker.getBox()))


@dataclass(frozen=True)
class PatchSamples:
    """Measured patch colours, normalised so that 1.0 is the HDR saturation value."""

    rgb: np.ndarray
    """(24, 3) mean linear RGB in [0, 1]."""
    clipped: np.ndarray
    """(24,) fraction of sampled pixels at or above the saturation limit."""
    scale: float
    exposure: float
    """Factor applied to :attr:`rgb` to match the reference white's luminance."""

    @property
    def saturated(self) -> np.ndarray:
        """(24,) True where too much of the patch is clipped to be usable."""
        return self.clipped > MAX_CLIPPED_FRACTION


def _patch_mean(image: np.ndarray, polygon: np.ndarray, limit: float) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    x0 = max(int(np.floor(polygon[:, 0].min())), 0)
    y0 = max(int(np.floor(polygon[:, 1].min())), 0)
    x1 = min(int(np.ceil(polygon[:, 0].max())) + 1, width)
    y1 = min(int(np.ceil(polygon[:, 1].max())) + 1, height)
    if x1 <= x0 or y1 <= y0:
        return np.zeros(3), 1.0
    roi = image[y0:y1, x0:x1]
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    local = (polygon - np.array([x0, y0])).astype(np.int32)
    cv2.fillConvexPoly(mask, local, 255)
    count = int(cv2.countNonZero(mask))
    if count == 0:
        return np.zeros(3), 1.0
    mean = np.array(cv2.mean(roi, mask=mask)[:3])
    hot = np.any(roi >= limit, axis=2).astype(np.uint8) * (mask > 0)
    return mean, float(np.count_nonzero(hot)) / count


def sample_patches(
    linear_rgb: np.ndarray,
    quad: ChartQuad,
    *,
    scale: float = DEFAULT_PATCH_SCALE,
    saturation: SaturationValues,
    targets: ReferenceTargets | None = None,
) -> PatchSamples:
    """Average each patch of ``linear_rgb`` (the array the CCM module receives).

    The result is divided by the HDR saturation value and then scaled so the brightest usable
    neutral patch matches the reference white's luminance, which keeps the fitted matrix's row
    sums near 1.0 instead of absorbing an exposure error.
    """
    if linear_rgb.ndim != 3 or linear_rgb.shape[2] != 3:
        raise CalibrationError(f"Expected an (H, W, 3) RGB image, got shape {linear_rgb.shape}")
    targets = targets or reference_targets()
    limit = SATURATION_LIMIT * saturation.hdr
    polygons = quad.patch_polygons(scale)
    means = np.zeros((len(COLORCHECKER24), 3))
    clipped = np.zeros(len(COLORCHECKER24))
    for index, polygon in enumerate(polygons):
        means[index], clipped[index] = _patch_mean(linear_rgb, polygon, limit)
    means /= saturation.hdr

    exposure = 1.0
    for patch_index in NEUTRAL_INDICES:
        position = patch_index - 1
        if clipped[position] <= MAX_CLIPPED_FRACTION and means[position].mean() > 1e-6:
            exposure = float(targets.linear_rgb[position].mean() / means[position].mean())
            break
    return PatchSamples(rgb=means * exposure, clipped=clipped, scale=scale, exposure=exposure)


def matrix_to_array(matrix: tuple[CcmRow, CcmRow, CcmRow], hdr: int) -> np.ndarray:
    """Config matrix to the (3, 4) form :func:`cv2.transform` wants, offsets normalised."""
    array = np.array(matrix, dtype=np.float64)
    array[:, 3] /= hdr
    return array


def apply_matrix(rgb: np.ndarray, matrix: tuple[CcmRow, CcmRow, CcmRow], hdr: int) -> np.ndarray:
    """Apply a configuration matrix to normalised linear RGB, clipping like the module does."""
    triples = np.asarray(rgb, dtype=np.float64).reshape(-1, 1, 3)
    out = cv2.transform(triples, matrix_to_array(matrix, hdr)).reshape(-1, 3)
    return np.clip(out, 0.0, 1.0)


def _lab(rgb: np.ndarray, targets: ReferenceTargets) -> np.ndarray:
    return xyz_to_lab(linear_rgb_to_xyz(rgb), targets.white)


def evaluate(
    samples: PatchSamples,
    targets: ReferenceTargets,
    matrix: tuple[CcmRow, CcmRow, CcmRow],
    hdr: int,
) -> np.ndarray:
    """Per-patch CIEDE2000 error of ``matrix`` on ``samples``."""
    corrected = apply_matrix(samples.rgb, matrix, hdr)
    return delta_e_2000(_lab(corrected, targets), targets.lab)


@dataclass(frozen=True)
class PatchError:
    """One row of the calibration report."""

    index: int
    name: str
    weight: float
    delta_e_before: float
    delta_e_after: float
    excluded: bool
    """True when the patch was left out of the fit (zero weight or clipped)."""


@dataclass(frozen=True)
class CcmFit:
    """A fitted matrix and how well it does, in configuration convention."""

    matrix: tuple[CcmRow, CcmRow, CcmRow]
    patches: tuple[PatchError, ...]
    mean_before: float
    mean_after: float
    max_after: float
    mean_neutral_after: float
    used: int
    """Number of patches that took part in the fit."""
    loss: float
    """OpenCV's final weighted loss."""


def _repeat_counts(weights: np.ndarray, resolution: int = 16) -> np.ndarray:
    """Integer repeat counts proportional to ``weights`` (zero weights drop the patch)."""
    positive = weights[weights > 0]
    if positive.size == 0:
        return np.zeros_like(weights, dtype=int)
    counts = np.maximum(np.round(weights * resolution / positive.max()), 1).astype(int)
    counts[weights <= 0] = 0
    return counts


def _row(values: np.ndarray) -> CcmRow:
    red, green, blue, offset = (round(float(value), 4) for value in values)
    return red, green, blue, offset


def _to_config_matrix(cv_ccm: np.ndarray, hdr: int) -> tuple[CcmRow, CcmRow, CcmRow]:
    """OpenCV's ``dst = src @ ccm`` matrix to the configuration's row-per-output-channel form."""
    linear = np.asarray(cv_ccm, dtype=np.float64)
    offsets = np.zeros(3)
    if linear.shape[0] == 4:
        offsets = linear[3] * hdr
        linear = linear[:3]
    rows = np.hstack([linear.T, offsets.reshape(3, 1)])
    return _row(rows[0]), _row(rows[1]), _row(rows[2])


def _normalise_rows(matrix: tuple[CcmRow, CcmRow, CcmRow]) -> tuple[CcmRow, CcmRow, CcmRow]:
    scaled = []
    for row in matrix:
        total = sum(row[:3])
        factor = 1.0 / total if abs(total) > 1e-6 else 1.0
        scaled.append(np.array([row[0] * factor, row[1] * factor, row[2] * factor, row[3]]))
    return _row(scaled[0]), _row(scaled[1]), _row(scaled[2])


def _lstsq_fit(src: np.ndarray, dst: np.ndarray, affine: bool) -> np.ndarray:
    """Plain least-squares fallback when OpenCV has no ``ccm`` module."""
    design = np.hstack([src, np.ones((len(src), 1))]) if affine else src
    solution, *_ = np.linalg.lstsq(design, dst, rcond=None)
    return solution


def fit_ccm(
    samples: PatchSamples,
    *,
    targets: ReferenceTargets | None = None,
    weights: np.ndarray | tuple[float, ...] | None = None,
    saturation: SaturationValues,
    affine: bool = False,
    distance: str = "cie2000",
    preserve_neutral: bool = False,
    current: tuple[CcmRow, CcmRow, CcmRow] = IDENTITY_CCM,
) -> CcmFit:
    """Fit a colour correction matrix and report CIEDE2000 before and after.

    :param samples: patch means from :func:`sample_patches`.
    :param targets: reference colours; defaults to D65 with a Bradford adaptation.
    :param weights: per-patch weights; defaults to ``WEIGHT_PRESETS["All patches"]``.
    :param affine: fit the offset column as well (OpenCV's ``CCM_AFFINE``).
    :param distance: ``"cie2000"`` or ``"cie76"``, the metric OpenCV minimises.
    :param preserve_neutral: scale every row so its 3x3 part sums to 1.0.
    :param current: the matrix in use, used for the "before" column.
    """
    targets = targets or reference_targets()
    weight_array = np.asarray(
        WEIGHT_PRESETS["All patches"] if weights is None else weights, dtype=np.float64
    )
    if weight_array.shape != (len(COLORCHECKER24),):
        raise CalibrationError(f"Expected {len(COLORCHECKER24)} weights, got {weight_array.shape}")

    usable = weight_array.copy()
    usable[samples.saturated] = 0.0
    counts = _repeat_counts(usable)
    if int(np.count_nonzero(counts)) < 4:
        raise CalibrationError(
            "Fewer than four usable patches: check the chart outline, the patch scale and the "
            "exposure (clipped patches are excluded)."
        )

    rows = np.repeat(np.arange(len(COLORCHECKER24)), counts)
    src = samples.rgb[rows].reshape(-1, 1, 3)
    dst = targets.linear_rgb[rows].reshape(-1, 1, 3)

    loss = float("nan")
    if hasattr(cv2, "ccm"):
        model = cv2.ccm.ColorCorrectionModel(src, dst, cv2.ccm.COLOR_SPACE_SRGBL)
        model.setColorSpace(cv2.ccm.COLOR_SPACE_SRGB)
        model.setCcmType(cv2.ccm.CCM_AFFINE if affine else cv2.ccm.CCM_LINEAR)
        model.setLinearization(cv2.ccm.LINEARIZATION_IDENTITY)
        model.setDistance(
            cv2.ccm.DISTANCE_CIE76 if distance == "cie76" else cv2.ccm.DISTANCE_CIE2000
        )
        model.setInitialMethod(cv2.ccm.INITIAL_METHOD_LEAST_SQUARE)
        try:
            model.compute()
        except cv2.error as error:  # pragma: no cover - depends on the OpenCV build
            raise CalibrationError(f"OpenCV could not fit a matrix: {error}") from error
        cv_ccm = np.asarray(model.getColorCorrectionMatrix())
        loss = float(model.getLoss())
    else:  # pragma: no cover - only on an OpenCV without the ccm module
        cv_ccm = _lstsq_fit(src.reshape(-1, 3), dst.reshape(-1, 3), affine)

    matrix = _to_config_matrix(cv_ccm, saturation.hdr)
    if preserve_neutral:
        matrix = _normalise_rows(matrix)

    before = evaluate(samples, targets, current, saturation.hdr)
    after = evaluate(samples, targets, matrix, saturation.hdr)
    patches = tuple(
        PatchError(
            index=patch.index,
            name=patch.name,
            weight=float(weight_array[position]),
            delta_e_before=float(before[position]),
            delta_e_after=float(after[position]),
            excluded=counts[position] == 0,
        )
        for position, patch in enumerate(COLORCHECKER24)
    )
    included = np.array([not patch.excluded for patch in patches])
    neutrals = np.array([patch.index in NEUTRAL_INDICES for patch in patches]) & included
    return CcmFit(
        matrix=matrix,
        patches=patches,
        mean_before=float(before[included].mean()),
        mean_after=float(after[included].mean()),
        max_after=float(after[included].max()),
        mean_neutral_after=float(after[neutrals].mean()) if neutrals.any() else float("nan"),
        used=int(included.sum()),
        loss=loss,
    )


def white_patch_rgb(samples: PatchSamples) -> np.ndarray:
    """Mean RGB of the white patch, for a quick sanity check in the GUI."""
    return samples.rgb[WHITE_INDEX - 1]

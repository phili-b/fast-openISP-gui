import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fast_openisp.calibration import CHART_COLS, CHART_ROWS, ChartQuad
from fast_openisp.color import reference_targets
from fast_openisp.modules.base import SaturationValues

ROOT = Path(__file__).resolve().parents[1]

CHART_SATURATION = SaturationValues(raw=4095, hdr=4095)
CHART_MATRIX = np.array(
    [[1.65, -0.55, -0.10], [-0.22, 1.45, -0.23], [0.04, -0.45, 1.41]], dtype=np.float64
)
"""A plausible sensor matrix; the synthetic chart is built so this matrix corrects it exactly."""


@pytest.fixture
def mikros_path() -> Path:
    return ROOT / "raw" / "mikros110.tiff"


@pytest.fixture
def test_raw_path() -> Path:
    return ROOT / "raw" / "test.RAW"


# ------------------------------------------------- synthetic colour checker
def make_chart(
    matrix: np.ndarray = CHART_MATRIX,
    *,
    offsets: np.ndarray | None = None,
    size: tuple[int, int] = (400, 600),
) -> np.ndarray:
    """Linear sensor image of a ColorChecker that ``matrix`` (plus ``offsets``) corrects exactly."""
    targets = reference_targets()
    reference = targets.linear_rgb if offsets is None else targets.linear_rgb - offsets
    sensor = reference @ np.linalg.inv(matrix.T)
    height, width = size
    image = np.zeros((height, width, 3))
    cell_h, cell_w = height // CHART_ROWS, width // CHART_COLS
    for row in range(CHART_ROWS):
        for col in range(CHART_COLS):
            patch = sensor[row * CHART_COLS + col]
            image[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w] = patch
    return np.clip(image * CHART_SATURATION.hdr, 0, CHART_SATURATION.hdr).astype(np.uint16)


def full_chart_quad(image: np.ndarray) -> ChartQuad:
    """A quad covering the whole image, matching :func:`make_chart`'s layout."""
    height, width = image.shape[:2]
    return ChartQuad(
        ((0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height)))
    )


@pytest.fixture
def chart_saturation() -> SaturationValues:
    return CHART_SATURATION


@pytest.fixture
def chart_matrix() -> np.ndarray:
    return CHART_MATRIX


@pytest.fixture
def chart_image() -> np.ndarray:
    return make_chart()


@pytest.fixture
def chart_factory() -> Callable[..., np.ndarray]:
    return make_chart


@pytest.fixture
def chart_quad() -> Callable[[np.ndarray], ChartQuad]:
    return full_chart_quad

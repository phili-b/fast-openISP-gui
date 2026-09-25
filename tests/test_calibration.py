"""Colour checker calibration: reference data, chart geometry, the fit and the DPC statistics."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from fast_openisp.calibration import (
    CHART_COLS,
    CalibrationError,
    ChartQuad,
    apply_matrix,
    detect_chart,
    fit_ccm,
    sample_patches,
)
from fast_openisp.color import (
    COLORCHECKER24,
    NEUTRAL_INDICES,
    WEIGHT_PRESETS,
    adaptation_matrix,
    delta_e_2000,
    reference_targets,
    white_xyz,
)
from fast_openisp.config import IDENTITY_CCM, IspConfig
from fast_openisp.imaging import render_linear
from fast_openisp.modules.base import Context, PipelineData, SaturationValues
from fast_openisp.modules.ccm import CCM
from fast_openisp.pipeline import Pipeline

QuadFactory = Callable[[np.ndarray], ChartQuad]


# --------------------------------------------------------------------- colour
def test_delta_e_2000_matches_reference_pair() -> None:
    # Sharma's CIEDE2000 test data, pair 17
    value = delta_e_2000(np.array([[50.0, 2.6772, -79.7751]]), np.array([[50.0, 0.0, -82.7485]]))
    assert value[0] == pytest.approx(2.0425, abs=1e-4)


def test_delta_e_of_identical_colours_is_zero() -> None:
    targets = reference_targets()
    assert delta_e_2000(targets.lab, targets.lab).max() == 0.0


@pytest.mark.parametrize("method", ["bradford", "cat02", "von_kries"])
def test_cat_maps_the_white_point_and_round_trips(method: str) -> None:
    d50, d65 = white_xyz("D50"), white_xyz("D65")
    forward = adaptation_matrix(d50, d65, method)
    assert forward @ d50 == pytest.approx(d65, abs=1e-9)
    assert forward @ adaptation_matrix(d65, d50, method) == pytest.approx(np.eye(3), abs=1e-9)


def test_cat_none_is_the_identity() -> None:
    matrix = adaptation_matrix(white_xyz("D50"), white_xyz("A"), "none")
    assert matrix == pytest.approx(np.eye(3))


def test_neutral_reference_patches_stay_neutral_after_adaptation() -> None:
    targets = reference_targets(adapt_to="D65", cat="bradford")
    for index in NEUTRAL_INDICES:
        _, a, b = targets.lab[index - 1]
        assert abs(a) < 1.5 and abs(b) < 1.5


def test_adapting_to_illuminant_a_warms_the_targets() -> None:
    d65 = reference_targets(adapt_to="D65").linear_rgb[18]
    warm = reference_targets(adapt_to="A").linear_rgb[18]
    assert warm[0] / warm[2] > d65[0] / d65[2]


def test_weight_presets_cover_every_patch() -> None:
    for weights in WEIGHT_PRESETS.values():
        assert len(weights) == len(COLORCHECKER24)
    neutrals_only = WEIGHT_PRESETS["Neutrals only"]
    assert [i + 1 for i, w in enumerate(neutrals_only) if w > 0] == list(NEUTRAL_INDICES)


# -------------------------------------------------------------------- geometry
def test_patch_centres_follow_the_quad() -> None:
    quad = ChartQuad(((0.0, 0.0), (600.0, 0.0), (600.0, 400.0), (0.0, 400.0)))
    centres = quad.patch_centres()
    assert centres.shape == (24, 2)
    assert centres[0] == pytest.approx([50.0, 50.0])
    assert centres[23] == pytest.approx([550.0, 350.0])


def test_patch_scale_sets_the_sampled_area() -> None:
    quad = ChartQuad(((0.0, 0.0), (600.0, 0.0), (600.0, 400.0), (0.0, 400.0)))
    assert np.ptp(quad.patch_polygons(0.2)[0][:, 0]) == pytest.approx(20.0)
    assert np.ptp(quad.patch_polygons(0.8)[0][:, 0]) == pytest.approx(80.0)


def test_from_points_orders_the_corners() -> None:
    points = np.array([[600.0, 400.0], [0.0, 0.0], [0.0, 400.0], [600.0, 0.0]])
    quad = ChartQuad.from_points(points)
    assert quad.corners == ((0.0, 0.0), (600.0, 0.0), (600.0, 400.0), (0.0, 400.0))


def test_rotating_four_times_returns_the_original() -> None:
    quad = ChartQuad.centred(600, 400)
    assert quad.rotated().rotated().rotated().rotated() == quad


def test_sampling_a_rotated_chart_finds_the_same_patches(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    upright = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    turned = np.rot90(chart_image).copy()  # counter-clockwise
    height, width = turned.shape[:2]
    # Counter-clockwise: the chart's top-left corner is now the image's bottom-left
    quad = ChartQuad(
        ((0.0, float(height)), (0.0, 0.0), (float(width), 0.0), (float(width), float(height)))
    )
    rotated = sample_patches(turned, quad, saturation=chart_saturation)
    assert rotated.rgb == pytest.approx(upright.rgb, abs=2e-3)


# ------------------------------------------------------------------- the fit
def test_fit_recovers_a_known_matrix(
    chart_image: np.ndarray,
    chart_quad: QuadFactory,
    chart_matrix: np.ndarray,
    chart_saturation: SaturationValues,
) -> None:
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    assert samples.exposure == pytest.approx(1.0, abs=0.01)

    fit = fit_ccm(samples, saturation=chart_saturation)
    assert np.array(fit.matrix)[:, :3] == pytest.approx(chart_matrix, abs=0.01)
    assert np.array(fit.matrix)[:, 3] == pytest.approx(np.zeros(3))
    assert fit.mean_after < 0.5 < fit.mean_before
    assert fit.used == len(COLORCHECKER24)


def test_config_matrix_convention_matches_the_ccm_module(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    """A fitted matrix must mean the same to :class:`CCM` as to the fit's own error report."""
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    fit = fit_ccm(samples, saturation=chart_saturation)

    context = Context(bayer_pattern="bggr", bit_depth=12, saturation=chart_saturation)
    params = IspConfig().modules.ccm.model_copy(update={"ccm": fit.matrix})
    data = PipelineData(bayer=np.zeros((2, 2), np.uint16))
    data.rgb_image = (
        np.round(samples.rgb * chart_saturation.hdr).astype(np.uint16).reshape(1, -1, 3)
    )
    CCM(params, context).execute(data)

    expected = apply_matrix(samples.rgb, fit.matrix, chart_saturation.hdr)
    assert data.rgb_image.reshape(-1, 3) / chart_saturation.hdr == pytest.approx(expected, abs=2e-3)


def test_affine_fit_recovers_an_offset(
    chart_factory: Callable[..., np.ndarray],
    chart_quad: QuadFactory,
    chart_saturation: SaturationValues,
) -> None:
    offsets = np.array([0.02, -0.01, 0.03])
    image = chart_factory(offsets=offsets)
    samples = sample_patches(image, chart_quad(image), saturation=chart_saturation)

    fit = fit_ccm(samples, saturation=chart_saturation, affine=True)
    assert np.array(fit.matrix)[:, 3] / chart_saturation.hdr == pytest.approx(offsets, abs=0.01)


def test_zero_weights_exclude_patches_from_the_fit(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    fit = fit_ccm(samples, saturation=chart_saturation, weights=WEIGHT_PRESETS["Neutrals only"])
    assert fit.used == len(NEUTRAL_INDICES)
    excluded = {patch.index for patch in fit.patches if patch.excluded}
    assert excluded == set(range(1, 25)) - set(NEUTRAL_INDICES)


def test_preserve_neutral_normalises_the_row_sums(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    fit = fit_ccm(samples, saturation=chart_saturation, preserve_neutral=True)
    assert np.array(fit.matrix)[:, :3].sum(axis=1) == pytest.approx(np.ones(3), abs=1e-3)


def test_clipped_patches_are_reported_and_excluded(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    chart_image[:100, :100] = chart_saturation.hdr  # blow out patch 1
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    assert samples.saturated[0]
    assert not samples.saturated[1:].any()

    fit = fit_ccm(samples, saturation=chart_saturation)
    assert fit.patches[0].excluded
    assert fit.used == len(COLORCHECKER24) - 1


def test_too_few_usable_patches_is_an_error(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    weights = np.zeros(len(COLORCHECKER24))
    weights[:3] = 1.0
    with pytest.raises(CalibrationError, match="Fewer than four"):
        fit_ccm(samples, saturation=chart_saturation, weights=weights)


def test_before_column_uses_the_current_matrix(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    samples = sample_patches(chart_image, chart_quad(chart_image), saturation=chart_saturation)
    identity = fit_ccm(samples, saturation=chart_saturation, current=IDENTITY_CCM)
    fitted = fit_ccm(samples, saturation=chart_saturation, current=identity.matrix)
    assert fitted.mean_before == pytest.approx(identity.mean_after, abs=1e-6)


# ------------------------------------------------------------ pipeline glue
def _small_config() -> IspConfig:
    return IspConfig().with_hardware(width=64, height=64, bit_depth=12, bayer_pattern="bggr")


def test_run_until_stops_before_the_named_module() -> None:
    bayer = np.full((64, 64), 512, dtype=np.uint16)
    data = Pipeline(_small_config()).run_until(bayer, "ccm")
    assert data.rgb_image is not None  # CFA ran
    assert data.y_image is None  # CSC, which comes after CCM, did not


def test_run_until_stops_even_when_the_module_is_disabled() -> None:
    config = _small_config()
    config = config.with_module("ccm", config.modules.ccm.model_copy(update={"enabled": False}))
    data = Pipeline(config).run_until(np.full((64, 64), 512, np.uint16), "ccm")
    assert data.y_image is None


def test_dpc_reports_the_number_of_corrected_pixels() -> None:
    bayer = np.full((64, 64), 512, dtype=np.uint16)
    hot = [(10, 10), (20, 20), (31, 41)]
    for y, x in hot:
        bayer[y, x] = 4000
    result = Pipeline(_small_config()).execute(bayer)
    assert result.stats["dpc_corrected"] == len(hot)
    assert result.stats["dpc_total"] == bayer.size


# ------------------------------------------------------------- detection
def test_detect_finds_the_synthetic_chart(
    chart_image: np.ndarray, chart_quad: QuadFactory, chart_saturation: SaturationValues
) -> None:
    """The detector needs a gamma-encoded image; it is fed the same render as the dialog."""
    preview = render_linear(chart_image, chart_saturation.hdr)
    quad = detect_chart(preview)
    assert quad is not None

    # The synthetic chart fills the frame, so the outline must too
    assert quad.area() > 0.9 * chart_image.shape[0] * chart_image.shape[1]
    # Within an eighth of a cell of the ideal grid (the synthetic patches have no gaps
    # between them, so the detector's patch corners are looser than on a real chart)
    cell = chart_image.shape[1] / CHART_COLS
    centres = quad.patch_centres()
    expected = chart_quad(chart_image).patch_centres()
    assert centres == pytest.approx(expected, abs=cell / 8)


def test_detected_quad_fits_the_matrix(
    chart_image: np.ndarray, chart_matrix: np.ndarray, chart_saturation: SaturationValues
) -> None:
    quad = detect_chart(render_linear(chart_image, chart_saturation.hdr))
    assert quad is not None
    samples = sample_patches(chart_image, quad, saturation=chart_saturation)
    fit = fit_ccm(samples, saturation=chart_saturation)
    assert np.array(fit.matrix)[:, :3] == pytest.approx(chart_matrix, abs=0.05)
    assert fit.mean_after < 1.0


def test_detection_survives_a_downscaled_search(
    chart_factory: Callable[..., np.ndarray], chart_saturation: SaturationValues
) -> None:
    """A chart larger than DETECTION_MAX_EDGE is searched small and scaled back up."""
    big = chart_factory(size=(1400, 2100))
    quad = detect_chart(render_linear(big, chart_saturation.hdr))
    assert quad is not None
    assert quad.area() > 0.9 * big.shape[0] * big.shape[1]


# ------------------------------------------------- remembering the outline
def test_quad_survives_a_normalised_round_trip() -> None:
    quad = ChartQuad(((10.0, 20.0), (600.0, 30.0), (590.0, 400.0), (20.0, 390.0)))
    stored = quad.normalised(640, 480)
    assert all(0.0 <= value <= 1.0 for value in stored)
    assert ChartQuad.from_normalised(stored, 640, 480) == quad


def test_normalised_quad_rescales_to_another_image_size() -> None:
    quad = ChartQuad(((0.0, 0.0), (640.0, 0.0), (640.0, 480.0), (0.0, 480.0)))
    restored = ChartQuad.from_normalised(quad.normalised(640, 480), 1280, 960)
    assert restored is not None
    assert restored.corners == ((0.0, 0.0), (1280.0, 0.0), (1280.0, 960.0), (0.0, 960.0))


@pytest.mark.parametrize("stored", [None, "nonsense", [1.0, 2.0], [9.0] * 8, ["x"] * 8])
def test_unusable_stored_quads_are_rejected(stored: object) -> None:
    assert ChartQuad.from_normalised(stored, 640, 480) is None


def test_area_reports_a_collapsed_outline() -> None:
    assert ChartQuad.centred(600, 400).area() > 10_000
    assert ChartQuad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))).area() < 2

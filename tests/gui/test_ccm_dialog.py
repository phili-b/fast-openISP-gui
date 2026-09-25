"""Colour checker calibration in the GUI (run offscreen with pytest-qt)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from fast_openisp.calibration import ChartQuad
from fast_openisp.config import IDENTITY_CCM
from fast_openisp.gui.ccm_dialog import CcmCalibration, CcmCalibrationDialog, render_preview
from fast_openisp.gui.main_window import MainWindow
from fast_openisp.modules.base import SaturationValues

TIMEOUT = 120_000
QuadFactory = Callable[[np.ndarray], ChartQuad]


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    monkeypatch.setattr(win, "ask_import_settings", lambda path, defaults, headerless: defaults)
    qtbot.addWidget(win)
    yield win
    win.dirty = False
    win.runner.shutdown()


@pytest.fixture
def dialog(
    qtbot: QtBot,
    chart_image: np.ndarray,
    chart_quad: QuadFactory,
    chart_saturation: SaturationValues,
) -> CcmCalibrationDialog:
    widget = CcmCalibrationDialog(chart_image, chart_saturation, IDENTITY_CCM)
    qtbot.addWidget(widget)
    widget.overlay.set_quad(chart_quad(chart_image))
    return widget


def test_render_preview_is_displayable(
    chart_image: np.ndarray, chart_saturation: SaturationValues
) -> None:
    preview = render_preview(chart_image, chart_saturation)
    assert preview.dtype == np.uint8
    assert preview.shape == chart_image.shape


def test_dialog_fits_the_known_matrix(
    dialog: CcmCalibrationDialog, chart_matrix: np.ndarray
) -> None:
    assert dialog.fit is not None
    assert np.array(dialog.fit.matrix)[:, :3] == pytest.approx(chart_matrix, abs=0.01)
    assert dialog.fit.mean_after < dialog.fit.mean_before
    assert dialog.table.rowCount() == 24
    assert "mean" in dialog.summary_label.text()
    assert dialog.apply_button.isEnabled()


def test_patch_scale_slider_refits(dialog: CcmCalibrationDialog, chart_matrix: np.ndarray) -> None:
    dialog.scale_slider.setValue(30)
    assert "30 %" in dialog.scale_label.text()
    assert dialog.fit is not None
    assert np.array(dialog.fit.matrix)[:, :3] == pytest.approx(chart_matrix, abs=0.02)


def test_preset_changes_the_weights(dialog: CcmCalibrationDialog) -> None:
    dialog.preset_combo.setCurrentText("Neutrals only")
    assert dialog.fit is not None
    assert dialog.fit.used == 6
    assert sum(patch.weight for patch in dialog.fit.patches) == pytest.approx(6.0)


def test_cat_and_illuminant_change_the_targets(dialog: CcmCalibrationDialog) -> None:
    assert dialog.fit is not None
    bradford = np.array(dialog.fit.matrix)
    dialog.illuminant_combo.setCurrentText("A")
    assert dialog.fit is not None
    warm = np.array(dialog.fit.matrix)
    assert not np.allclose(warm, bradford)

    dialog.cat_combo.setCurrentIndex(dialog.cat_combo.findData("none"))
    assert dialog.fit is not None
    assert not np.allclose(np.array(dialog.fit.matrix), warm)


def test_settings_round_trip(
    dialog: CcmCalibrationDialog,
    qtbot: QtBot,
    chart_image: np.ndarray,
    chart_saturation: SaturationValues,
) -> None:
    dialog.preset_combo.setCurrentText("Skin tones")
    dialog.neutral_box.setChecked(True)
    dialog.scale_slider.setValue(45)
    settings = dialog.settings()
    assert settings["preset"] == "Skin tones"
    assert settings["preserve_neutral"] is True
    assert settings["scale"] == 45

    again = CcmCalibrationDialog(chart_image, chart_saturation, IDENTITY_CCM, settings)
    qtbot.addWidget(again)
    assert again.scale_slider.value() == 45
    assert again.neutral_box.isChecked()
    assert again.preset_combo.currentText() == "Skin tones"
    assert again.fit is not None


def test_weight_edit_switches_to_custom(dialog: CcmCalibrationDialog) -> None:
    item = dialog.table.item(0, 2)
    assert item is not None
    item.setText("0")
    assert dialog.preset_combo.currentText() == "Custom"
    assert dialog.fit is not None
    assert dialog.fit.patches[0].excluded
    assert dialog.fit.used == 23


def test_main_window_applies_the_calibration(
    qtbot: QtBot, window: MainWindow, mikros_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        assert window.open_path(mikros_path)
    assert window.config.modules.ccm.ccm == IDENTITY_CCM

    fitted = ((1.4, -0.3, -0.1, 0.0), (-0.2, 1.4, -0.2, 0.0), (0.0, -0.4, 1.4, 0.0))
    captured: dict[str, object] = {}

    def fake_dialog(linear_rgb, saturation, current):
        captured["shape"] = linear_rgb.shape
        captured["current"] = current
        return CcmCalibration(
            matrix=fitted,
            mean_before=6.0,
            mean_after=1.5,
            max_after=3.0,
            used=24,
            settings={"preset": "All patches"},
        )

    monkeypatch.setattr(window, "ask_ccm_calibration", fake_dialog)
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        window._calibrate_module("ccm")

    assert window.config.modules.ccm.ccm == fitted
    assert captured["current"] == IDENTITY_CCM
    # Calibration runs on the full-resolution image, not on the preview
    assert captured["shape"] == (1096, 1090, 3)
    assert window.dirty
    report = window.panel.boxes["ccm"].report_label
    assert report is not None
    assert "1.50" in report.text()


def test_cancelling_the_dialog_keeps_the_matrix(
    qtbot: QtBot, window: MainWindow, mikros_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        assert window.open_path(mikros_path)
    monkeypatch.setattr(window, "ask_ccm_calibration", lambda *args: None)
    window._calibrate_module("ccm")
    assert window.config.modules.ccm.ccm == IDENTITY_CCM


def test_calibration_without_an_image_warns(window: MainWindow) -> None:
    window._calibrate_module("ccm")
    assert "color checker" in window.banner.label.text()


def test_dpc_count_is_reported(qtbot: QtBot, window: MainWindow, mikros_path: Path) -> None:
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        assert window.open_path(mikros_path)
    report = window.panel.boxes["dpc"].report_label
    assert report is not None

    # The bundled mikros110 config may have DPC switched off; a disabled module reports nothing
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        window.panel.boxes["dpc"].enable_box.setChecked(True)
        window.schedule_preview(immediate=True)
    assert "Corrected" in report.text()
    assert "preview 1:2" in report.text()

    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        window.panel.boxes["dpc"].enable_box.setChecked(False)
        window.schedule_preview(immediate=True)
    assert report.text() == "–"


def test_the_outline_is_remembered(
    qtbot: QtBot,
    dialog: CcmCalibrationDialog,
    chart_image: np.ndarray,
    chart_saturation: SaturationValues,
) -> None:
    moved = dialog.overlay.quad.moved(0, (12.0, 34.0))
    dialog.overlay.set_quad(moved)
    settings = dialog.settings()
    stored = settings["quad"]
    assert isinstance(stored, list)
    assert stored[:2] == pytest.approx([12.0 / 600, 34.0 / 400])

    again = CcmCalibrationDialog(chart_image, chart_saturation, IDENTITY_CCM, settings)
    qtbot.addWidget(again)
    assert np.array(again.overlay.quad.corners) == pytest.approx(np.array(moved.corners))
    assert "restored" in again.status_label.text()


def test_a_stored_outline_rescales_to_a_bigger_image(
    qtbot: QtBot,
    dialog: CcmCalibrationDialog,
    chart_factory: Callable[..., np.ndarray],
    chart_saturation: SaturationValues,
) -> None:
    settings = dialog.settings()
    bigger = chart_factory(size=(800, 1200))
    again = CcmCalibrationDialog(bigger, chart_saturation, IDENTITY_CCM, settings)
    qtbot.addWidget(again)
    assert np.array(again.overlay.quad.corners[2]) == pytest.approx(np.array([1200.0, 800.0]))
    assert again.fit is not None


def test_a_junk_outline_falls_back_to_detection(
    qtbot: QtBot, chart_image: np.ndarray, chart_saturation: SaturationValues
) -> None:
    settings: dict[str, object] = {"quad": [0.0] * 8}  # collapsed, so unusable
    again = CcmCalibrationDialog(chart_image, chart_saturation, IDENTITY_CCM, settings)
    qtbot.addWidget(again)
    assert "detected" in again.status_label.text()
    assert again.overlay.quad.area() > 0.5 * chart_image.shape[0] * chart_image.shape[1]

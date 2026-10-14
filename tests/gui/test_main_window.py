"""GUI smoke tests (run offscreen with pytest-qt)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import cv2
import pytest
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDropEvent
from pytestqt.qtbot import QtBot

from fast_openisp.gui.export_dialog import ExportOptions
from fast_openisp.gui.image_view import ViewMode
from fast_openisp.gui.import_dialog import ImportSettings
from fast_openisp.gui.main_window import MainWindow

SCREENSHOTS = os.environ.get("FAST_OPENISP_SCREENSHOTS")
TIMEOUT = 120_000


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    # The bundled config is meant to be tuned freely; pin the parameters these tests rely on.
    data = win.config.model_dump()
    data["modules"]["awb"].update(enabled=True, mode="grey_world")
    for name in ("csc", "nlm", "bnf", "eeh", "fcs", "hsc", "bcc"):
        data["modules"][name]["enabled"] = True
    win._set_base_config(type(win.config).model_validate(data), "connan_raw14", None)
    # Accept the import dialog defaults without showing it
    monkeypatch.setattr(win, "ask_import_settings", lambda path, defaults, headerless: defaults)
    # An edited config would otherwise raise a modal "discard changes?" box when the test ends
    monkeypatch.setattr(win, "_confirm_discard", lambda: True)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.dirty = False
    win.runner.shutdown()


def _open(qtbot: QtBot, win: MainWindow, path: Path) -> None:
    with qtbot.waitSignal(win.runner.preview_ready, timeout=TIMEOUT):
        assert win.open_path(path)


def _screenshot(win: MainWindow, name: str) -> None:
    if SCREENSHOTS:
        Path(SCREENSHOTS).mkdir(parents=True, exist_ok=True)
        win.grab().save(str(Path(SCREENSHOTS) / f"{name}.png"))


def test_open_raw_shows_preview(qtbot: QtBot, window: MainWindow, connan_path: Path) -> None:
    _open(qtbot, window, connan_path)
    assert window.raw is not None
    assert window.raw.bit_depth == 14
    assert window.config.hardware.bayer_pattern == "bggr"
    assert window.factor == 6
    assert window.last_image is not None
    assert window.last_image.shape == (674, 1012, 3)
    assert "connan_raw14.raw" in window.info_label.text()
    window.view.fit()
    _screenshot(window, "01_processed")


def test_drop_file_opens_it(qtbot: QtBot, window: MainWindow, connan_path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(connan_path))])
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        window.dropEvent(event)
    assert window.raw is not None
    assert window.raw.source_path == connan_path


def test_disabling_csc_disables_dependents(
    qtbot: QtBot, window: MainWindow, connan_path: Path
) -> None:
    _open(qtbot, window, connan_path)
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        window.panel.boxes["csc"].enable_box.setChecked(False)
    dependents = ("nlm", "bnf", "eeh", "fcs", "hsc", "bcc")
    for name in dependents:
        box = window.panel.boxes[name]
        assert not box.enable_box.isChecked()
        assert not box.enable_box.isEnabled()
        assert not window.config.modules.get(name).enabled
    assert "Requires CSC" in window.panel.boxes["nlm"].enable_box.toolTip()
    _screenshot(window, "02_csc_disabled")

    # Re-enabling restores the previous state of the dependents
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        window.panel.boxes["csc"].enable_box.setChecked(True)
    for name in dependents:
        assert window.config.modules.get(name).enabled
        assert window.panel.boxes[name].enable_box.isEnabled()


def test_parameter_edit_reruns(qtbot: QtBot, window: MainWindow, connan_path: Path) -> None:
    _open(qtbot, window, connan_path)
    assert window.last_image is not None
    before = window.last_image.copy()
    box = window.panel.boxes["gac"]
    box.set_expanded(True)
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        box.editors["gamma"].set_value(0.8)  # emits `changed` via the spin box
    assert window.config.modules.gac.gamma == pytest.approx(0.8)
    assert window.dirty
    assert (window.last_image != before).any()
    assert window.windowTitle().startswith("connan_raw14*")


def test_grey_world_freeze(qtbot: QtBot, window: MainWindow, connan_path: Path) -> None:
    _open(qtbot, window, connan_path)
    box = window.panel.boxes["awb"]
    box.set_expanded(True)
    assert window.config.modules.awb.mode == "grey_world"
    assert box.freeze_button.isEnabled()
    with qtbot.waitSignal(window.runner.preview_ready, timeout=TIMEOUT):
        box.freeze_button.click()
    awb = window.config.modules.awb
    assert awb.mode == "manual"
    assert awb.r_gain != 1.0
    assert awb.b_gain != 1.0
    _screenshot(window, "03_awb_frozen")


def test_split_view_and_modes(qtbot: QtBot, window: MainWindow, connan_path: Path) -> None:
    _open(qtbot, window, connan_path)
    window.set_mode(ViewMode.SPLIT)
    assert window.view.item.mode == ViewMode.SPLIT
    window.view.item.split = 0.4
    window.view.item.update()
    _screenshot(window, "04_split")
    window.view.set_temporary_before(True)
    assert window.view.item.mode == ViewMode.BEFORE
    window.view.set_temporary_before(False)
    assert window.view.item.mode == ViewMode.SPLIT


def test_export_png_and_jpg(
    qtbot: QtBot, window: MainWindow, connan_path: Path, tmp_path: Path
) -> None:
    _open(qtbot, window, connan_path)
    for name, save_cfg in (("out.png", True), ("out.jpg", False)):
        target = tmp_path / name
        with qtbot.waitSignal(window.runner.export_ready, timeout=TIMEOUT):
            window.start_export(ExportOptions(target, jpeg_quality=90, save_config=save_cfg))
        qtbot.waitUntil(target.exists, timeout=5000)
        image = cv2.imread(str(target))
        assert image is not None
        assert image.shape == (4044, 6080, 3)
    assert (tmp_path / "out.yaml").exists()


def test_open_headerless_raw(qtbot: QtBot, window: MainWindow, test_raw_path: Path) -> None:
    window.load_bundled_config("test")
    _open(qtbot, window, test_raw_path)
    assert window.raw is not None
    assert (window.raw.width, window.raw.height) == (1920, 1080)
    assert window.factor == 2


def test_raw_size_mismatch_is_rejected(
    window: MainWindow, test_raw_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "fast_openisp.gui.main_window.QMessageBox.warning",
        lambda _parent, _title, text: messages.append(text),
    )
    monkeypatch.setattr(
        window,
        "ask_import_settings",
        lambda path, defaults, headerless: ImportSettings(1000, 1000, 10, "rggb"),
    )
    assert not window.open_path(test_raw_path)
    assert messages
    assert "bytes" in messages[0]


def test_invalid_yaml_is_reported(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("modules:\n  awb:\n    r_gain: 99\n  typo_module: {}\n", encoding="utf-8")
    messages: list[str] = []
    monkeypatch.setattr(
        "fast_openisp.gui.main_window.QMessageBox.critical",
        lambda _parent, _title, text: messages.append(text),
    )
    assert not window.load_config_file(bad)
    assert "modules.awb.r_gain" in messages[0]
    assert "typo_module" in messages[0]

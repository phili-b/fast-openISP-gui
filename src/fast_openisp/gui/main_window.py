"""Main application window."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeyEvent,
    QKeySequence,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fast_openisp import __version__
from fast_openisp.config import (
    CcmRow,
    ConfigError,
    IspConfig,
    bundled_configs,
    load_config,
    save_config,
)
from fast_openisp.gui.ccm_dialog import CcmCalibration, CcmCalibrationDialog
from fast_openisp.gui.export_dialog import ExportDialog, ExportOptions
from fast_openisp.gui.image_view import CompareView, ViewMode, numpy_to_qimage
from fast_openisp.gui.import_dialog import ImportDialog, ImportSettings
from fast_openisp.gui.module_panel import ModulePanel
from fast_openisp.gui.worker import PipelineRunner, RunOutcome
from fast_openisp.imaging import (
    DEFAULT_PREVIEW_MAX_EDGE,
    downscale_bayer,
    preview_factor,
    render_before,
)
from fast_openisp.io.export import ExportError, save_image
from fast_openisp.io.loaders import (
    DNG_EXTENSIONS,
    IMAGE_EXTENSIONS,
    RAW_EXTENSIONS,
    TIFF_EXTENSIONS,
    LoadError,
    RawImage,
    load_dng,
    load_raw,
    load_tiff,
    probe_tiff,
)
from fast_openisp.modules.base import SaturationValues
from fast_openisp.pipeline import Pipeline, PipelineError

CONFIG_EXTENSIONS = (".yaml", ".yml")
DEBOUNCE_MS = 250
MAX_RECENT = 8
DEFAULT_CONFIG = "mikros110"
PROJECT_URL = "https://phili-b.github.io/fast-openISP-gui/"


class Banner(QFrame):
    """Dismissible error banner shown above the image."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        self.setStyleSheet(
            "#banner { background: #5a1e1e; border-radius: 4px; } #banner QLabel { color: white; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        close = QPushButton("Dismiss")
        close.clicked.connect(self.hide)
        layout.addWidget(self.label, 1)
        layout.addWidget(close)
        self.hide()

    def show_message(self, message: str) -> None:
        self.label.setText(message)
        self.show()


class DropOverlay(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__("Drop to open", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background: rgba(40, 110, 200, 90); color: white; font-size: 22pt;"
            "border: 3px dashed rgba(255, 255, 255, 200); border-radius: 12px;"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()


def _first_supported(urls: list[QUrl]) -> Path | None:
    for url in urls:
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_EXTENSIONS + CONFIG_EXTENSIONS:
                return path
    return None


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("fast-openISP", "fast-openISP")
        self.bundled = bundled_configs()

        self.raw: RawImage | None = None
        self.preview_bayer: np.ndarray | None = None
        self.factor = 1
        self.last_image: np.ndarray | None = None
        self.config_path: Path | None = None
        self.config_name = DEFAULT_CONFIG
        self.dirty = False

        initial = self.bundled.get(DEFAULT_CONFIG) or IspConfig()
        self.base_config = initial
        self.config = initial

        self.runner = PipelineRunner(self)
        self.runner.preview_ready.connect(self._on_preview_ready)
        self.runner.preview_failed.connect(self._on_preview_failed)
        self.runner.progress.connect(self._on_progress)
        self.runner.busy_changed.connect(self._on_busy_changed)
        self.runner.export_ready.connect(self._on_export_ready)
        self.runner.export_failed.connect(self._on_export_failed)
        self.runner.export_cancelled.connect(self._on_export_cancelled)
        self.runner.export_progress.connect(self._on_export_progress)
        self._export_options: ExportOptions | None = None
        self._export_config: IspConfig | None = None
        self._export_dialog: QProgressDialog | None = None

        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(DEBOUNCE_MS)
        self.debounce.timeout.connect(self.run_preview)

        self._build_ui()
        self._build_menus()
        self._restore_settings()
        self.config = self.panel.set_config(self.config)
        self._update_title()
        self._update_actions()
        self.setAcceptDrops(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.setWindowTitle("fast-openISP")
        self.resize(1400, 900)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(4)
        self.banner = Banner()
        layout.addWidget(self.banner)
        self.view = CompareView()
        self.view.hovered.connect(self._on_hover)
        self.view.zoom_changed.connect(self._on_zoom_changed)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(central)
        self.central = central
        self.overlay = DropOverlay(central)

        self.panel = ModulePanel(self.config)
        self.panel.config_changed.connect(self._on_config_edited)
        self.panel.sensor_changed.connect(self._on_sensor_changed)
        self.panel.reset_requested.connect(self._reset_module)
        self.panel.calibrate_requested.connect(self._calibrate_module)
        dock = QDockWidget("ISP modules", self)
        dock.setObjectName("modules_dock")
        dock.setWidget(self.panel)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.dock = dock

        status = self.statusBar()
        self.pixel_label = QLabel()
        self.info_label = QLabel("No image")
        self.progress_label = QLabel()
        self.timing_label = QLabel()
        self.zoom_label = QLabel()
        self.export_button = QPushButton("Export…")
        self.export_button.setObjectName("export_button")
        self.export_button.clicked.connect(self.export_image)
        status.addWidget(self.pixel_label, 1)
        status.addPermanentWidget(self.progress_label)
        status.addPermanentWidget(self.info_label)
        status.addPermanentWidget(self.timing_label)
        status.addPermanentWidget(self.zoom_label)
        status.addPermanentWidget(self.export_button)

    def _build_menus(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        self.open_action = self._action(file_menu, "&Open image…", self.open_dialog, "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Open &recent")
        file_menu.addSeparator()
        self.export_action = self._action(file_menu, "&Export…", self.export_image, "Ctrl+E")
        file_menu.addSeparator()
        self._action(file_menu, "Forget remembered import settings", self._forget_imports)
        file_menu.addSeparator()
        self._action(file_menu, "E&xit", self.close, QKeySequence.StandardKey.Quit)

        config_menu = menu.addMenu("&Config")
        bundled_menu = config_menu.addMenu("&Bundled configs")
        for name in self.bundled:
            self._action(bundled_menu, name, lambda _=False, n=name: self.load_bundled_config(n))
        self._action(config_menu, "&Load YAML…", self.load_config_dialog, "Ctrl+L")
        self._action(config_menu, "&Save YAML", self.save_config, QKeySequence.StandardKey.Save)
        self._action(config_menu, "Save YAML &as…", self.save_config_as, "Ctrl+Shift+S")
        self._action(config_menu, "&Revert to loaded config", self.revert_config)
        config_menu.addSeparator()
        self.as_shot_action = self._action(config_menu, "Use as-shot white balance for DNG")
        self.as_shot_action.setCheckable(True)
        config_menu.addSeparator()
        self.calibrate_action = self._action(
            config_menu,
            "Calibrate CCM on color checker…",
            lambda: self._calibrate_module("ccm"),
        )

        view_menu = menu.addMenu("&View")
        self._action(view_menu, "&Fit to window", self.view.fit, "Ctrl+0")
        self._action(view_menu, "&Actual size (100%)", self.view.actual_size, "Ctrl+1")
        self._action(
            view_menu,
            "Zoom &in",
            lambda: self.view.set_zoom(self.view.zoom() * 1.25),
            QKeySequence.StandardKey.ZoomIn,
        )
        self._action(
            view_menu,
            "Zoom o&ut",
            lambda: self.view.set_zoom(self.view.zoom() / 1.25),
            QKeySequence.StandardKey.ZoomOut,
        )
        view_menu.addSeparator()
        group = QActionGroup(self)
        self.mode_actions: dict[ViewMode, QAction] = {}
        for mode, label, shortcut in (
            (ViewMode.PROCESSED, "&Processed", "F2"),
            (ViewMode.BEFORE, "&Before", "F3"),
            (ViewMode.SPLIT, "&Split before/after", "F4"),
        ):
            action = self._action(
                view_menu, label, lambda _=False, m=mode: self.set_mode(m), shortcut
            )
            action.setCheckable(True)
            group.addAction(action)
            self.mode_actions[mode] = action
        self.mode_actions[ViewMode.PROCESSED].setChecked(True)
        hint = view_menu.addAction("Hold B to show Before temporarily")
        hint.setEnabled(False)
        view_menu.addSeparator()
        self.full_res_action = self._action(
            view_menu, "Full-resolution preview", self._on_full_res_toggled
        )
        self.full_res_action.setCheckable(True)
        self._action(view_menu, "Preview size…", self._choose_preview_size)

        help_menu = menu.addMenu("&Help")
        self._action(
            help_menu, "&Documentation", self._open_docs, QKeySequence.StandardKey.HelpContents
        )
        self._action(help_menu, "&About", self._about)

    def _action(self, menu, text, slot=None, shortcut=None) -> QAction:
        action = QAction(text, self)
        if slot is not None:
            action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    # ------------------------------------------------------------ settings
    @property
    def preview_max_edge(self) -> int:
        return self._int_setting("view/preview_max_edge", DEFAULT_PREVIEW_MAX_EDGE)

    def _int_setting(self, key: str, default: int) -> int:
        try:
            return int(str(self.settings.value(key, default)))
        except ValueError:
            return default

    def _restore_settings(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)  # type: ignore[arg-type]
        state = self.settings.value("window/state")
        if state is not None:
            self.restoreState(state)  # type: ignore[arg-type]
        self.as_shot_action.setChecked(
            str(self.settings.value("dng/use_as_shot_wb", "true")).lower() == "true"
        )
        self.as_shot_action.toggled.connect(
            lambda on: self.settings.setValue("dng/use_as_shot_wb", "true" if on else "false")
        )

        last_config = str(self.settings.value("config/last", ""))
        if last_config:
            try:
                if last_config.startswith("bundled:"):
                    name = last_config.removeprefix("bundled:")
                    if name in self.bundled:
                        self._set_base_config(self.bundled[name], name, None)
                elif Path(last_config).is_file():
                    path = Path(last_config)
                    self._set_base_config(load_config(path), path.stem, path)
            except ConfigError:
                pass
        self._rebuild_recent_menu()

    def _recent_files(self) -> list[str]:
        value = self.settings.value("files/recent", [])
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value] if isinstance(value, list) else []

    def _add_recent(self, path: Path) -> None:
        recent = [p for p in self._recent_files() if Path(p) != path]
        recent.insert(0, str(path))
        self.settings.setValue("files/recent", recent[:MAX_RECENT])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self._recent_files()
        for item in recent:
            self._action(self.recent_menu, item, lambda _=False, p=item: self.open_path(Path(p)))
        self.recent_menu.setEnabled(bool(recent))

    def _forget_imports(self) -> None:
        self.settings.beginGroup("import")
        self.settings.remove("")
        self.settings.endGroup()
        self.statusBar().showMessage("Import settings forgotten", 3000)

    # --------------------------------------------------------------- state
    def _update_title(self) -> None:
        parts = [f"{self.config_name}{'*' if self.dirty else ''}"]
        if self.raw is not None:
            parts.append(self.raw.source_path.name)
        parts.append("fast-openISP")
        self.setWindowTitle(" — ".join(parts))

    def _update_actions(self) -> None:
        has_image = self.raw is not None
        self.export_action.setEnabled(has_image)
        self.export_button.setEnabled(has_image)
        self.calibrate_action.setEnabled(has_image)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self._update_title()

    def _set_base_config(self, config: IspConfig, name: str, path: Path | None) -> None:
        if self.raw is not None:
            # The loaded image determines the sensor layout
            config = config.with_hardware(
                width=self.raw.width,
                height=self.raw.height,
                bit_depth=self.config.hardware.bit_depth,
                bayer_pattern=self.config.hardware.bayer_pattern,
            )
        self.base_config = config
        self.config_name = name
        self.config_path = path
        self.config = self.panel.set_config(config)
        self.settings.setValue("config/last", str(path) if path else f"bundled:{name}")
        self._set_dirty(False)
        self._refresh_before()
        self.schedule_preview(immediate=True)

    # ------------------------------------------------------------- config
    def load_bundled_config(self, name: str) -> None:
        if self._confirm_discard():
            self._set_base_config(self.bundled[name], name, None)

    def load_config_dialog(self) -> None:
        start = str(self.config_path.parent) if self.config_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load configuration", start, "YAML config (*.yaml *.yml)"
        )
        if path:
            self.load_config_file(Path(path))

    def load_config_file(self, path: Path) -> bool:
        if not self._confirm_discard():
            return False
        try:
            config = load_config(path)
        except ConfigError as error:
            QMessageBox.critical(
                self, "Invalid configuration", f"{path.name} could not be loaded:\n\n{error}"
            )
            return False
        self._set_base_config(config, path.stem, path)
        self.statusBar().showMessage(f"Loaded {path.name}", 3000)
        return True

    def save_config(self) -> bool:
        if self.config_path is None:
            return self.save_config_as()
        return self._write_config(self.config_path)

    def save_config_as(self) -> bool:
        start = str(
            self.config_path
            or Path(str(self.settings.value("dirs/config", ""))) / f"{self.config_name}.yaml"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save configuration", start, "YAML config (*.yaml *.yml)"
        )
        if not path:
            return False
        return self._write_config(Path(path))

    def _write_config(self, path: Path) -> bool:
        try:
            save_config(self.config, path)
        except OSError as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return False
        self.config_path = path
        self.config_name = path.stem
        self.base_config = self.config
        self.settings.setValue("config/last", str(path))
        self.settings.setValue("dirs/config", str(path.parent))
        self._set_dirty(False)
        self.statusBar().showMessage(f"Saved {path.name}", 3000)
        return True

    def revert_config(self) -> None:
        self.config = self.panel.set_config(self.base_config)
        self._set_dirty(False)
        self._refresh_before()
        self.schedule_preview(immediate=True)

    def _reset_module(self, name: str) -> None:
        base = self.base_config.modules.get(name)
        params = base.model_copy(update={"enabled": self.config.modules.get(name).enabled})
        self.panel.boxes[name].set_params(params)
        self.config = self.config.with_module(name, params)
        self.panel.config = self.config
        self._on_config_edited(self.config)

    def _calibrate_module(self, name: str) -> None:
        """Fit the CCM on a colour checker in the open image."""
        if name != "ccm":
            return
        if self.raw is None:
            self.banner.show_message("Open a raw image of a color checker first.")
            return
        if not self.config.modules.cfa.enabled:
            self.banner.show_message("Enable CFA (demosaicing) before calibrating the CCM.")
            return
        if not self.config.modules.awb.enabled:
            self.banner.show_message(
                "AWB is disabled: the fitted matrix will absorb the white balance."
            )

        ccm = self.config.modules.ccm
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage("Processing the image up to the CCM…")
        try:
            pipeline = Pipeline(self.config)
            data = pipeline.run_until(self.raw.bayer, "ccm")
            linear_rgb = data.require_rgb()
        except (ConfigError, PipelineError) as error:
            QMessageBox.warning(self, "Cannot calibrate", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()

        calibration = self.ask_ccm_calibration(linear_rgb, pipeline.saturation, ccm.ccm)
        if calibration is None:
            return
        self.panel.boxes["ccm"].apply_params({"ccm": calibration.matrix})
        self.panel.boxes["ccm"].set_report(
            f"Fitted on {calibration.used} patches: mean ΔE "
            f"{calibration.mean_before:.2f} → {calibration.mean_after:.2f}, "
            f"max {calibration.max_after:.2f}"
        )
        if not self.config.modules.ccm.enabled:
            self.banner.show_message("The matrix was applied, but CCM itself is switched off.")

    def ask_ccm_calibration(
        self,
        linear_rgb: np.ndarray,
        saturation: SaturationValues,
        current: tuple[CcmRow, CcmRow, CcmRow],
    ) -> CcmCalibration | None:
        """Show the calibration dialog (overridden in tests)."""
        stored = self.settings.value("ccm/settings")
        try:
            settings = json.loads(str(stored)) if stored else {}
        except ValueError:
            settings = {}
        dialog = CcmCalibrationDialog(linear_rgb, saturation, current, settings, parent=self)
        accepted = dialog.exec() == CcmCalibrationDialog.DialogCode.Accepted
        # Remember the outline and the fit settings even when the dialog is cancelled
        self.settings.setValue("ccm/settings", json.dumps(dialog.settings()))
        return dialog.result_calibration() if accepted else None

    def _on_config_edited(self, config: IspConfig) -> None:
        self.config = config
        self._set_dirty(True)
        self.schedule_preview()

    def _on_sensor_changed(self, bit_depth: int, pattern: str) -> None:
        try:
            config = self.config.with_hardware(bit_depth=bit_depth, bayer_pattern=pattern)
        except Exception as error:  # e.g. black level above the new maximum
            self.banner.show_message(str(error))
            hw = self.config.hardware
            self.panel.sensor.set_values(hw.width, hw.height, hw.bit_depth, hw.bayer_pattern)
            return
        self.config = config
        self.panel.config = config
        self._set_dirty(True)
        self._refresh_before()
        self.schedule_preview()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"The configuration '{self.config_name}' has unsaved changes. Save them?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_config()
        return answer == QMessageBox.StandardButton.Discard

    # -------------------------------------------------------------- images
    def open_dialog(self) -> None:
        start = str(self.settings.value("dirs/image", ""))
        patterns = " ".join(f"*{ext}" for ext in IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open image",
            start,
            f"Raw images ({patterns});;Headerless raw (*.raw);;TIFF (*.tif *.tiff);;DNG (*.dng)",
        )
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix in CONFIG_EXTENSIONS:
            return self.load_config_file(path)
        if suffix not in IMAGE_EXTENSIONS:
            self.statusBar().showMessage(f"Unsupported file type: {path.name}", 5000)
            return False
        try:
            raw = self._load(path)
        except (LoadError, OSError) as error:
            QMessageBox.warning(self, "Cannot open image", str(error))
            return False
        if raw is None:  # cancelled
            return False
        self.set_raw(raw)
        self.settings.setValue("dirs/image", str(path.parent))
        self._add_recent(path)
        return True

    def ask_import_settings(
        self, path: Path, defaults: ImportSettings, *, headerless: bool
    ) -> ImportSettings | None:
        """Show the import dialog (overridden in tests)."""
        dialog = ImportDialog(path, defaults, headerless=headerless, parent=self)
        if dialog.exec() != ImportDialog.DialogCode.Accepted:
            return None
        return dialog.settings()

    def _remembered(self, key: str) -> ImportSettings | None:
        value = self.settings.value(key)
        if not value:
            return None
        try:
            return ImportSettings(**json.loads(str(value)))
        except (TypeError, ValueError):
            return None

    def _remember(self, key: str, settings: ImportSettings) -> None:
        data = {
            "width": settings.width,
            "height": settings.height,
            "bit_depth": settings.bit_depth,
            "bayer_pattern": settings.bayer_pattern,
            "byte_order": settings.byte_order,
        }
        self.settings.setValue(key, json.dumps(data))

    def _load(self, path: Path) -> RawImage | None:
        suffix = path.suffix.lower()
        hw = self.config.hardware
        if suffix in DNG_EXTENSIONS:
            return load_dng(path)
        if suffix in TIFF_EXTENSIONS:
            info = probe_tiff(path)
            key = f"import/tiff_{info.width}x{info.height}"
            settings = self._remembered(key)
            if settings is None:
                defaults = ImportSettings(info.width, info.height, info.bit_depth, hw.bayer_pattern)
                settings = self.ask_import_settings(path, defaults, headerless=False)
                if settings is None:
                    return None
                if settings.remember:
                    self._remember(key, settings)
            return load_tiff(path, settings.bayer_pattern, settings.bit_depth)
        if suffix in RAW_EXTENSIONS:
            key = f"import/raw_{path.stat().st_size}"
            settings = self._remembered(key)
            if settings is None:
                defaults = ImportSettings(hw.width, hw.height, hw.bit_depth, hw.bayer_pattern)
                settings = self.ask_import_settings(path, defaults, headerless=True)
                if settings is None:
                    return None
                if settings.remember:
                    self._remember(key, settings)
            return load_raw(
                path,
                settings.width,
                settings.height,
                settings.bit_depth,
                settings.bayer_pattern,
                settings.byte_order,
            )
        raise LoadError(f"Unsupported file type {suffix!r}")

    def set_raw(self, raw: RawImage) -> None:
        """Show a newly loaded image and apply its metadata to the configuration."""
        self.raw = raw
        config = self.config.with_hardware(
            width=raw.width,
            height=raw.height,
            bit_depth=raw.bit_depth,
            bayer_pattern=raw.bayer_pattern,
        )
        if raw.black_level is not None:
            r, gr, gb, b = raw.black_level
            blc = config.modules.blc.model_copy(
                update={"bl_r": r, "bl_gr": gr, "bl_gb": gb, "bl_b": b}
            )
            config = config.with_module("blc", blc)
        if raw.as_shot_wb is not None and self.as_shot_action.isChecked():
            r, gr, gb, b = raw.as_shot_wb
            awb = config.modules.awb.model_copy(
                update={"mode": "manual", "r_gain": r, "gr_gain": gr, "gb_gain": gb, "b_gain": b}
            )
            config = config.with_module("awb", awb)
        if config != self.config:
            self.config = self.panel.set_config(config)
            self._set_dirty(True)
        self.last_image = None
        self.view.set_size(raw.width, raw.height)
        self.view.set_processed(None)
        self._refresh_before()
        self.view.fit()
        self.banner.hide()
        self._update_info()
        self._update_title()
        self._update_actions()
        self.schedule_preview(immediate=True)

    def _update_info(self) -> None:
        if self.raw is None:
            self.info_label.setText("No image")
            return
        hw = self.config.hardware
        scale = "full-res" if self.factor == 1 else f"preview 1:{self.factor}"
        self.info_label.setText(
            f"{self.raw.source_path.name} · {self.raw.width}×{self.raw.height} · "
            f"{hw.bit_depth}-bit {hw.bayer_pattern.upper()} · {scale}"
        )

    def _refresh_before(self) -> None:
        """Recompute the preview Bayer data and the 'before' rendering."""
        if self.raw is None:
            return
        hw = self.config.hardware
        full = self.full_res_action.isChecked()
        self.factor = 1 if full else preview_factor(self.raw.bayer.shape, self.preview_max_edge)
        self.preview_bayer = downscale_bayer(self.raw.bayer, self.factor, hw.bayer_pattern)
        before = render_before(self.preview_bayer, hw.bit_depth, hw.bayer_pattern)
        self.view.set_before(numpy_to_qimage(before))
        self._update_info()

    # ------------------------------------------------------------- preview
    def schedule_preview(self, *, immediate: bool = False) -> None:
        if self.raw is None:
            return
        if immediate:
            self.debounce.stop()
            self.run_preview()
        else:
            self.debounce.start()

    def run_preview(self) -> None:
        if self.preview_bayer is None:
            return
        self.runner.request_preview(self.config, self.preview_bayer, self.factor)

    def _on_preview_ready(self, outcome: RunOutcome) -> None:
        self.last_image = outcome.result.image
        self.view.set_processed(numpy_to_qimage(outcome.result.image))
        self.panel.set_awb_gains(outcome.result.awb_gains)
        self.panel.set_stats(outcome.result.stats, preview_factor=outcome.preview_factor)
        self.timing_label.setText(f"{outcome.wall_time * 1000:.0f} ms")
        slowest = sorted(outcome.result.timings.items(), key=lambda kv: -kv[1])[:5]
        self.timing_label.setToolTip(
            "Slowest modules:\n" + "\n".join(f"{k.upper()}: {v * 1000:.0f} ms" for k, v in slowest)
        )
        self.banner.hide()

    def _on_preview_failed(self, message: str) -> None:
        self.banner.show_message(f"Processing failed: {message}")

    def _on_progress(self, name: str, index: int, total: int) -> None:
        if name:
            self.progress_label.setText(f"Running {name.upper()} ({index + 1}/{total})…")

    def _on_busy_changed(self, busy: bool) -> None:
        if not busy:
            self.progress_label.clear()

    def _on_full_res_toggled(self) -> None:
        self._refresh_before()
        self.schedule_preview(immediate=True)

    def _choose_preview_size(self) -> None:
        value, ok = QInputDialog.getInt(
            self,
            "Preview size",
            "Maximum preview edge (pixels):",
            self.preview_max_edge,
            256,
            8192,
            128,
        )
        if ok:
            self.settings.setValue("view/preview_max_edge", value)
            self._refresh_before()
            self.schedule_preview(immediate=True)

    def set_mode(self, mode: ViewMode) -> None:
        self.view.set_mode(mode)
        self.mode_actions[mode].setChecked(True)

    def _on_hover(self, x: int, y: int) -> None:
        if x < 0 or self.raw is None:
            self.pixel_label.clear()
            return
        text = f"x {x}, y {y}"
        image = self.last_image
        if image is not None:
            px = min(image.shape[1] - 1, x * image.shape[1] // self.raw.width)
            py = min(image.shape[0] - 1, y * image.shape[0] // self.raw.height)
            value = image[py, px]
            text += f"   RGB {tuple(int(v) for v in np.atleast_1d(value))}"
        self.pixel_label.setText(text)

    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")

    # --------------------------------------------------------------- export
    def export_image(self) -> None:
        if self.raw is None:
            return
        last_dir = Path(str(self.settings.value("dirs/export", self.raw.source_path.parent)))
        suffix = str(self.settings.value("export/format", ".png"))
        default = last_dir / f"{self.raw.source_path.stem}{suffix}"
        dialog = ExportDialog(
            default,
            jpeg_quality=self._int_setting("export/jpeg_quality", 95),
            save_config=str(self.settings.value("export/save_config", "false")) == "true",
            parent=self,
        )
        if dialog.exec() != ExportDialog.DialogCode.Accepted:
            return
        self.start_export(dialog.options())

    def start_export(self, options: ExportOptions) -> None:
        """Process at full resolution in the background, then write the file."""
        assert self.raw is not None
        self.settings.setValue("dirs/export", str(options.path.parent))
        self.settings.setValue("export/format", options.path.suffix.lower())
        self.settings.setValue("export/jpeg_quality", options.jpeg_quality)
        self.settings.setValue("export/save_config", "true" if options.save_config else "false")

        self._export_options = options
        self._export_config = self.config
        progress = QProgressDialog("Processing at full resolution…", "Cancel", 0, 100, self)
        progress.setWindowTitle("Export")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self.runner.cancel_export)
        progress.setValue(0)
        self._export_dialog = progress
        self.runner.start_export(self.config, self.raw.bayer)

    def _close_export_dialog(self) -> None:
        if self._export_dialog is not None:
            self._export_dialog.canceled.disconnect()
            self._export_dialog.close()
            self._export_dialog.deleteLater()
            self._export_dialog = None

    def _on_export_progress(self, name: str, index: int, total: int) -> None:
        if self._export_dialog is not None and total:
            self._export_dialog.setValue(int(100 * index / total))
            if name:
                self._export_dialog.setLabelText(f"Processing at full resolution: {name.upper()}…")

    def _on_export_ready(self, outcome: RunOutcome) -> None:
        self._close_export_dialog()
        options = self._export_options
        if options is None:
            return
        try:
            path = save_image(outcome.result.image, options.path, jpeg_quality=options.jpeg_quality)
            if options.save_config and self._export_config is not None:
                save_config(self._export_config, path.with_suffix(".yaml"))
        except (ExportError, OSError) as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return
        self.statusBar().showMessage(f"Exported {path} ({outcome.wall_time:.1f} s)", 8000)

    def _on_export_failed(self, message: str) -> None:
        self._close_export_dialog()
        QMessageBox.critical(self, "Export failed", message)

    def _on_export_cancelled(self) -> None:
        self._close_export_dialog()
        self.statusBar().showMessage("Export cancelled", 3000)

    # ------------------------------------------------------------ drag/drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and _first_supported(event.mimeData().urls()):
            event.acceptProposedAction()
            self.overlay.setGeometry(self.central.rect().adjusted(12, 12, -12, -12))
            self.overlay.show()
            self.overlay.raise_()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.overlay.hide()
        path = _first_supported(event.mimeData().urls())
        if path is None:
            self.statusBar().showMessage("Unsupported file type", 5000)
            event.ignore()
            return
        event.acceptProposedAction()
        # Open after the drop completes so dialogs don't block the drag source
        QTimer.singleShot(0, lambda: self.open_path(path))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.central.rect().adjusted(12, 12, -12, -12))

    # -------------------------------------------------------- hold-B compare
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        is_b_key = (
            event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_B
            and not event.isAutoRepeat()
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        )
        typing_text = isinstance(QApplication.focusWidget(), (QAbstractSpinBox, QLineEdit))
        if is_b_key and self.isActiveWindow() and not typing_text:
            self.view.set_temporary_before(event.type() == QEvent.Type.KeyPress)
            return True
        return super().eventFilter(watched, event)

    # ----------------------------------------------------------------- misc
    def _open_docs(self) -> None:
        QDesktopServices.openUrl(QUrl(PROJECT_URL))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About fast-openISP",
            f"<h3>fast-openISP {__version__}</h3>"
            "<p>A fast software image signal processor with a desktop GUI.</p>"
            "<p><b>Author:</b> Philippe Baetens</p>"
            "<p><b>Based on the original work:</b><br>"
            '<a href="https://github.com/QiuJueqin/fast-openISP">fast-openISP</a> '
            "by Qiu Jueqin (MIT license), a fast reimplementation of "
            '<a href="https://github.com/cruxopen/openISP">openISP</a>.</p>'
            "<p>GUI built with Qt for Python (PySide6, LGPLv3).</p>",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.runner.shutdown()
        event.accept()

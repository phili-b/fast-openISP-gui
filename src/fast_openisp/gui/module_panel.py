"""Left dock: sensor settings and the list of ISP modules with their parameters."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fast_openisp.config import (
    BAYER_PATTERNS,
    MODULE_INFO,
    MODULE_ORDER,
    GACParams,
    IspConfig,
    ModuleParams,
    format_validation_error,
    resolve_enabled,
)
from fast_openisp.gui.param_widgets import ParamEditor, create_editor, field_label

_GAC_PRESETS: dict[str, dict[str, Any]] = {
    "1.0": {"curve": "power", "gamma": 1.0},
    "2.2": {"curve": "power", "gamma": 0.4545},
    "sRGB": {"curve": "srgb", "gamma": 0.4545},
}
"""Gamma-and-gain curve presets; the exponent is the inverse of the displayed gamma."""


class ModuleBox(QFrame):
    """Header (enable checkbox, name, expand, reset) plus a parameter form."""

    toggled = Signal(str, bool)
    params_edited = Signal(str, object)  # name, ModuleParams
    reset_requested = Signal(str)
    calibrate_requested = Signal(str)

    def __init__(self, name: str, params: ModuleParams, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.params = params
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName(f"module_{name}")

        info = MODULE_INFO[name]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.enable_box = QCheckBox()
        self.enable_box.setObjectName(f"enable_{name}")
        self.enable_box.toggled.connect(lambda checked: self.toggled.emit(self.name, checked))
        header.addWidget(self.enable_box)
        self.title = QLabel(f"<b>{name.upper()}</b>&nbsp;&nbsp;{info.full_name}")
        self.title.setToolTip(info.full_name)
        header.addWidget(self.title, 1)

        self.editors: dict[str, ParamEditor] = {}
        fields = {k: v for k, v in type(params).model_fields.items() if k != "enabled"}

        self.reset_button = QToolButton()
        self.reset_button.setText("↺")
        self.reset_button.setToolTip("Reset parameters to the loaded configuration")
        self.reset_button.clicked.connect(lambda: self.reset_requested.emit(self.name))
        self.expand_button = QToolButton()
        self.expand_button.setCheckable(True)
        self.expand_button.setArrowType(Qt.ArrowType.RightArrow)
        self.expand_button.setToolTip("Show parameters")
        self.expand_button.toggled.connect(self._set_expanded)
        if fields:
            header.addWidget(self.reset_button)
            header.addWidget(self.expand_button)
        layout.addLayout(header)

        self.body = QWidget()
        form = QFormLayout(self.body)
        form.setContentsMargins(22, 0, 0, 4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for field_name, field_info in fields.items():
            editor = create_editor(field_name, field_info)
            editor.setObjectName(f"{name}.{field_name}")
            if field_info.description:
                editor.setToolTip(field_info.description)
            editor.changed.connect(lambda f=field_name: self._on_edited(f))
            label = QLabel(field_label(field_name))
            label.setToolTip(field_info.description or "")
            form.addRow(label, editor)
            self.editors[field_name] = editor

        self.awb_label: QLabel | None = None
        if name == "awb":
            self.awb_label = QLabel("Estimated gains: –")
            freeze = QPushButton("Freeze as manual")
            freeze.setObjectName("awb_freeze")
            freeze.setToolTip("Copy the estimated grey-world gains into manual mode")
            freeze.clicked.connect(self._freeze_awb)
            form.addRow(self.awb_label)
            form.addRow(freeze)
            self.freeze_button = freeze
            self._last_gains: tuple[float, float, float, float] | None = None

        self.report_label: QLabel | None = None
        if name in ("ccm", "dpc"):
            self.report_label = QLabel("–")
            self.report_label.setObjectName(f"{name}_report")
            self.report_label.setWordWrap(True)
            self.report_label.setEnabled(False)
            form.addRow(self.report_label)

        if name == "ccm":
            calibrate = QPushButton("Calibrate on color checker…")
            calibrate.setObjectName("ccm_calibrate")
            calibrate.setToolTip("Fit this matrix on a photographed ColorChecker chart")
            calibrate.clicked.connect(lambda: self.calibrate_requested.emit(self.name))
            form.addRow(calibrate)
            self.calibrate_button = calibrate

        if name == "gac":
            form.setRowVisible(self.editors["curve"], False)
            self._gac_custom = False
            self.gac_curve = QComboBox()
            self.gac_curve.setObjectName("gac_curve_preset")
            self.gac_curve.setToolTip("Transfer curve: gamma 1.0 (linear), gamma 2.2 or sRGB")
            for label in _GAC_PRESETS:
                self.gac_curve.addItem(label)
            self.gac_curve.addItem("Custom")
            self.gac_curve.activated.connect(self._on_gac_preset)
            form.insertRow(0, "Curve", self.gac_curve)
            self.gac_gain_label = QLabel("Applied gain: –")
            form.addRow(self.gac_gain_label)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #d04040;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        form.addRow(self.error_label)

        self.body.hide()
        layout.addWidget(self.body)
        self.set_params(params)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        arrow = Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        self.expand_button.setArrowType(arrow)

    def set_expanded(self, expanded: bool) -> None:
        self.expand_button.setChecked(expanded)

    def set_params(self, params: ModuleParams) -> None:
        self.params = params
        for field_name, editor in self.editors.items():
            editor.blockSignals(True)
            editor.set_value(getattr(params, field_name))
            editor.blockSignals(False)
        self.error_label.hide()
        self._update_awb_state()
        self._update_gac_state()

    def set_effective(self, enabled: bool, blocked_by: list[str]) -> None:
        """Show the effective enabled state; greyed out when a prerequisite is disabled."""
        self.enable_box.blockSignals(True)
        self.enable_box.setChecked(enabled)
        self.enable_box.blockSignals(False)
        self.enable_box.setEnabled(not blocked_by)
        self.title.setEnabled(not blocked_by)
        if blocked_by:
            needs = ", ".join(req.upper() for req in blocked_by)
            self.enable_box.setToolTip(f"Requires {needs} (disabled)")
        else:
            self.enable_box.setToolTip("Enable or disable this module")

    def _on_edited(self, field_name: str) -> None:
        value = self.editors[field_name].value()
        data = self.params.model_dump()
        data[field_name] = value
        try:
            params = type(self.params).model_validate(data)
        except ValidationError as error:
            self.error_label.setText(format_validation_error(error))
            self.error_label.show()
            return
        self.error_label.hide()
        self.params = params
        self._update_awb_state()
        self._update_gac_state()
        self.params_edited.emit(self.name, params)

    def _update_awb_state(self) -> None:
        if self.name != "awb":
            return
        grey_world = getattr(self.params, "mode", "manual") == "grey_world"
        for field_name in ("r_gain", "gr_gain", "gb_gain", "b_gain"):
            self.editors[field_name].setEnabled(not grey_world)
        self.freeze_button.setEnabled(grey_world and self._last_gains is not None)
        assert self.awb_label is not None
        self.awb_label.setVisible(grey_world)

    def set_awb_gains(self, gains: tuple[float, float, float, float]) -> None:
        if self.awb_label is None:
            return
        self._last_gains = gains
        r, _, _, b = gains
        self.awb_label.setText(f"Estimated gains: R {r:.3f}   B {b:.3f}")
        self._update_awb_state()

    def _freeze_awb(self) -> None:
        if self._last_gains is None:
            return
        r, gr, gb, b = self._last_gains
        data = self.params.model_dump() | {
            "mode": "manual",
            "r_gain": round(r, 4),
            "gr_gain": round(gr, 4),
            "gb_gain": round(gb, 4),
            "b_gain": round(b, 4),
        }
        params = type(self.params).model_validate(data)
        self.set_params(params)
        self.params_edited.emit(self.name, params)

    def set_gac_gain(self, gain: float) -> None:
        if self.name == "gac":
            self.gac_gain_label.setText(f"Applied gain: {gain:.3f}")

    def _on_gac_preset(self, index: int) -> None:
        label = self.gac_curve.itemText(index)
        self._gac_custom = label == "Custom"
        if self._gac_custom:
            self._update_gac_state()
            return
        self.apply_params(_GAC_PRESETS[label])

    def _update_gac_state(self) -> None:
        if self.name != "gac":
            return
        params = self.params
        assert isinstance(params, GACParams)
        curve, gamma = params.curve, params.gamma
        label = "Custom"
        if not self._gac_custom:
            for name, values in _GAC_PRESETS.items():
                if values["curve"] == curve and (curve == "srgb" or values["gamma"] == gamma):
                    label = name
        self._gac_custom = label == "Custom"
        self.gac_curve.blockSignals(True)
        self.gac_curve.setCurrentText(label)
        self.gac_curve.blockSignals(False)
        self.editors["gamma"].setEnabled(self._gac_custom)
        self.editors["gain"].setEnabled(not params.auto_gain)
        self.editors["auto_gain_clip"].setEnabled(params.auto_gain)

    def set_report(self, text: str) -> None:
        """Show a per-run measurement, such as the pixels DPC corrected."""
        if self.report_label is not None:
            self.report_label.setText(text)

    def apply_params(self, updates: dict[str, Any]) -> None:
        """Replace parameter values programmatically and report the change like an edit."""
        params = type(self.params).model_validate(self.params.model_dump() | updates)
        self.set_params(params)
        self.params_edited.emit(self.name, params)


class SensorBox(QGroupBox):
    """Hardware settings that affect processing: bit depth and Bayer pattern."""

    changed = Signal(int, str)  # bit depth, pattern

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Sensor", parent)
        form = QFormLayout(self)
        self.size_label = QLabel("–")
        self.bit_depth = QSpinBox()
        self.bit_depth.setObjectName("sensor_bit_depth")
        self.bit_depth.setRange(8, 16)
        self.bit_depth.setSuffix(" bit")
        self.bit_depth.setKeyboardTracking(False)
        self.pattern = QComboBox()
        self.pattern.setObjectName("sensor_pattern")
        for pattern in BAYER_PATTERNS:
            self.pattern.addItem(pattern.upper(), pattern)
        form.addRow("Size", self.size_label)
        form.addRow("Bit depth", self.bit_depth)
        form.addRow("Bayer pattern", self.pattern)
        self.bit_depth.valueChanged.connect(self._emit)
        self.pattern.currentIndexChanged.connect(self._emit)

    def _emit(self) -> None:
        self.changed.emit(self.bit_depth.value(), str(self.pattern.currentData()))

    def set_values(self, width: int, height: int, bit_depth: int, pattern: str) -> None:
        self.size_label.setText(f"{width} × {height}")
        for widget in (self.bit_depth, self.pattern):
            widget.blockSignals(True)
        self.bit_depth.setValue(bit_depth)
        self.pattern.setCurrentIndex(max(0, self.pattern.findData(pattern)))
        for widget in (self.bit_depth, self.pattern):
            widget.blockSignals(False)


class ModulePanel(QScrollArea):
    """Scrollable list of all modules. Emits the updated configuration on every change."""

    config_changed = Signal(object)  # IspConfig
    sensor_changed = Signal(int, str)
    reset_requested = Signal(str)
    calibrate_requested = Signal(str)

    def __init__(self, config: IspConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(360)
        self.config = config
        self._requested: dict[str, bool] = {}

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.sensor = SensorBox()
        self.sensor.changed.connect(self.sensor_changed)
        layout.addWidget(self.sensor)

        buttons = QHBoxLayout()
        expand_all = QPushButton("Expand all")
        collapse_all = QPushButton("Collapse all")
        expand_all.clicked.connect(lambda: self._expand_all(True))
        collapse_all.clicked.connect(lambda: self._expand_all(False))
        buttons.addWidget(expand_all)
        buttons.addWidget(collapse_all)
        layout.addLayout(buttons)

        self.boxes: dict[str, ModuleBox] = {}
        for name in MODULE_ORDER:
            box = ModuleBox(name, config.modules.get(name))
            box.toggled.connect(self._on_toggled)
            box.params_edited.connect(self._on_params_edited)
            box.reset_requested.connect(self.reset_requested)
            box.calibrate_requested.connect(self.calibrate_requested)
            layout.addWidget(box)
            self.boxes[name] = box
        layout.addStretch(1)
        self.setWidget(content)
        self.set_config(config)

    def _expand_all(self, expanded: bool) -> None:
        for box in self.boxes.values():
            if box.editors:
                box.set_expanded(expanded)

    def set_config(self, config: IspConfig) -> IspConfig:
        """Show ``config``. Returns it with module dependencies applied."""
        self._requested = {name: config.modules.get(name).enabled for name in MODULE_ORDER}
        for name, box in self.boxes.items():
            box.set_params(config.modules.get(name))
        hw = config.hardware
        self.sensor.set_values(hw.width, hw.height, hw.bit_depth, hw.bayer_pattern)
        self.config = self._apply_enabled(config)
        return self.config

    def _apply_enabled(self, config: IspConfig) -> IspConfig:
        effective = resolve_enabled(self._requested)
        modules: dict[str, Any] = {}
        for name, box in self.boxes.items():
            blocked = [req for req in MODULE_INFO[name].requires if not effective[req]]
            box.set_effective(effective[name], blocked)
            params = config.modules.get(name)
            if params.enabled != effective[name]:
                params = params.model_copy(update={"enabled": effective[name]})
            modules[name] = params
        return config.model_copy(update={"modules": config.modules.model_copy(update=modules)})

    def _on_toggled(self, name: str, checked: bool) -> None:
        self._requested[name] = checked
        self.config = self._apply_enabled(self.config)
        self.config_changed.emit(self.config)

    def _on_params_edited(self, name: str, params: ModuleParams) -> None:
        params = params.model_copy(update={"enabled": self.config.modules.get(name).enabled})
        self.config = self.config.with_module(name, params)
        self.config_changed.emit(self.config)

    def set_awb_gains(self, gains: tuple[float, float, float, float]) -> None:
        self.boxes["awb"].set_awb_gains(gains)

    def set_stats(self, stats: dict[str, object], *, preview_factor: int = 1) -> None:
        """Show the measurements a run reported (currently the DPC pixel count)."""
        gain = stats.get("gac_gain")
        if isinstance(gain, float):
            self.boxes["gac"].set_gac_gain(gain)
        corrected = stats.get("dpc_corrected")
        total = stats.get("dpc_total")
        if isinstance(corrected, int) and isinstance(total, int) and total:
            percent = 100 * corrected / total
            scale = "" if preview_factor == 1 else f" · preview 1:{preview_factor}"
            self.boxes["dpc"].set_report(f"Corrected {corrected:,} pixels ({percent:.3f} %){scale}")
        else:
            self.boxes["dpc"].set_report("–")

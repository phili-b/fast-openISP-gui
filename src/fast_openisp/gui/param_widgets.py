"""Editor widgets generated from Pydantic field definitions."""

from __future__ import annotations

import typing
from typing import Any

import annotated_types as at
from pydantic.fields import FieldInfo
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from fast_openisp.config import IDENTITY_CCM


class ParamEditor(QWidget):
    """Base class: an editor for a single parameter value."""

    changed = Signal()

    def value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError


def _bounds(info: FieldInfo, default_min: float, default_max: float) -> tuple[float, float]:
    low, high = default_min, default_max
    for item in info.metadata:
        if isinstance(item, at.Ge):
            low = float(item.ge)  # type: ignore[arg-type]
        elif isinstance(item, at.Gt):
            low = float(item.gt)  # type: ignore[arg-type]
        elif isinstance(item, at.Le):
            high = float(item.le)  # type: ignore[arg-type]
        elif isinstance(item, at.Lt):
            high = float(item.lt)  # type: ignore[arg-type]
    return low, high


def _extra(info: FieldInfo, key: str) -> Any:
    extra = info.json_schema_extra
    return extra.get(key) if isinstance(extra, dict) else None


class BoolEditor(ParamEditor):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.box = QCheckBox()
        layout.addWidget(self.box)
        self.box.toggled.connect(self.changed)

    def value(self) -> bool:
        return self.box.isChecked()

    def set_value(self, value: Any) -> None:
        self.box.setChecked(bool(value))


class IntEditor(ParamEditor):
    def __init__(self, low: float, high: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.spin = QSpinBox()
        self.spin.setRange(int(low), int(min(high, 2**31 - 1)))
        self.spin.setKeyboardTracking(False)
        self.spin.setAccelerated(True)
        layout.addWidget(self.spin)
        self.spin.valueChanged.connect(self.changed)

    def value(self) -> int:
        return self.spin.value()

    def set_value(self, value: Any) -> None:
        self.spin.setValue(int(value))


class FloatEditor(ParamEditor):
    def __init__(
        self,
        low: float,
        high: float,
        *,
        decimals: int,
        step: float,
        exclusive_low: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        minimum = low + 10**-decimals if exclusive_low else low
        self.spin.setRange(minimum, high)
        self.spin.setSingleStep(step)
        self.spin.setKeyboardTracking(False)
        self.spin.setAccelerated(True)
        layout.addWidget(self.spin)
        self.spin.valueChanged.connect(self.changed)

    def value(self) -> float:
        return self.spin.value()

    def set_value(self, value: Any) -> None:
        self.spin.setValue(float(value))


class ChoiceEditor(ParamEditor):
    def __init__(self, choices: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        for choice in choices:
            self.combo.addItem(choice.replace("_", " "), choice)
        layout.addWidget(self.combo)
        self.combo.currentIndexChanged.connect(self.changed)

    def value(self) -> str:
        return str(self.combo.currentData())

    def set_value(self, value: Any) -> None:
        index = self.combo.findData(value)
        if index >= 0:
            self.combo.setCurrentIndex(index)


class IntPairEditor(ParamEditor):
    """Two integers, e.g. CLAHE tile counts (rows, columns)."""

    def __init__(self, low: int, high: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.spins = []
        for label in ("rows", "cols"):
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setSuffix(f" {label}")
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self.changed)
            layout.addWidget(spin)
            self.spins.append(spin)

    def value(self) -> tuple[int, int]:
        return self.spins[0].value(), self.spins[1].value()

    def set_value(self, value: Any) -> None:
        for spin, item in zip(self.spins, value, strict=True):
            spin.setValue(int(item))


class MatrixEditor(ParamEditor):
    """3×4 color correction matrix with an identity reset and row-sum indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        for col, title in enumerate(("R in", "G in", "B in", "offset", "Σ")):
            header = QLabel(title)
            header.setEnabled(False)
            grid.addWidget(header, 0, col + 1)
        self.cells: list[list[QDoubleSpinBox]] = []
        self.sums: list[QLabel] = []
        for row, name in enumerate(("R", "G", "B")):
            grid.addWidget(QLabel(f"{name} out"), row + 1, 0)
            cells = []
            for col in range(4):
                spin = QDoubleSpinBox()
                offset_column = col == 3
                # Offsets are in HDR code values, so they need a much wider range than the gains
                spin.setDecimals(2 if offset_column else 4)
                spin.setRange(*((-4096.0, 4096.0) if offset_column else (-8.0, 8.0)))
                spin.setSingleStep(1.0 if offset_column else 0.01)
                spin.setKeyboardTracking(False)
                spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
                spin.setMinimumWidth(56)
                spin.valueChanged.connect(self._on_changed)
                grid.addWidget(spin, row + 1, col + 1)
                cells.append(spin)
            total = QLabel()
            total.setToolTip("Row sum of the 3×3 part; 1.0 keeps neutral greys neutral")
            grid.addWidget(total, row + 1, 5)
            self.sums.append(total)
            self.cells.append(cells)
        identity = QPushButton("Reset to identity")
        identity.clicked.connect(lambda: self.set_value(IDENTITY_CCM, emit=True))
        grid.addWidget(identity, 4, 1, 1, 5)
        self._update_sums()

    def _on_changed(self) -> None:
        self._update_sums()
        self.changed.emit()

    def _update_sums(self) -> None:
        for cells, label in zip(self.cells, self.sums, strict=True):
            total = sum(cell.value() for cell in cells[:3])
            label.setText(f"{total:.3f}")
            label.setStyleSheet("" if abs(total - 1.0) < 1e-3 else "color: #d08000;")

    def value(self) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(cell.value() for cell in row) for row in self.cells)

    def set_value(self, value: Any, *, emit: bool = False) -> None:
        for row_cells, row_values in zip(self.cells, value, strict=True):
            for cell, item in zip(row_cells, row_values, strict=True):
                cell.blockSignals(True)
                cell.setValue(float(item))
                cell.blockSignals(False)
        self._update_sums()
        if emit:
            self.changed.emit()


def create_editor(name: str, info: FieldInfo) -> ParamEditor:
    """Build an editor for a model field based on its type and constraints."""
    annotation = info.annotation
    origin = typing.get_origin(annotation)

    if annotation is bool:
        return BoolEditor()
    if origin is typing.Literal:
        return ChoiceEditor(tuple(str(arg) for arg in typing.get_args(annotation)))
    if annotation is int:
        low, high = _bounds(info, -(2**31), 2**31 - 1)
        return IntEditor(low, high)
    if annotation is float:
        low, high = _bounds(info, -1e6, 1e6)
        exclusive_low = any(isinstance(item, at.Gt) for item in info.metadata)
        wide = high - low >= 50
        decimals = _extra(info, "decimals") or (1 if wide else 4)
        step = _extra(info, "step") or (1.0 if wide else 0.01)
        return FloatEditor(low, high, decimals=decimals, step=step, exclusive_low=exclusive_low)
    if origin is tuple:
        args = typing.get_args(annotation)
        if args and all(arg is int for arg in args) and len(args) == 2:
            return IntPairEditor(2, 64)
        if name == "ccm":
            return MatrixEditor()
    raise TypeError(f"No editor for field {name!r} of type {annotation!r}")


def field_label(name: str) -> str:
    return name.replace("_", " ").capitalize()

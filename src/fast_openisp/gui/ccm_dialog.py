"""Colour checker calibration dialog: locate the chart, fit the CCM, show the CIEDE2000 error."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fast_openisp.calibration import (
    DEFAULT_PATCH_SCALE,
    CalibrationError,
    CcmFit,
    ChartQuad,
    detect_chart,
    fit_ccm,
    sample_patches,
)
from fast_openisp.color import CAT_METHODS, ILLUMINANTS, WEIGHT_PRESETS, ReferenceTargets
from fast_openisp.color import reference_targets as build_targets
from fast_openisp.config import IDENTITY_CCM, CcmRow
from fast_openisp.gui.image_view import numpy_to_qimage
from fast_openisp.imaging import render_linear
from fast_openisp.modules.base import SaturationValues

CAT_LABELS: dict[str, str] = {
    "bradford": "Bradford",
    "cat02": "CAT02",
    "von_kries": "von Kries",
    "none": "None",
}
CUSTOM_PRESET = "Custom"
HANDLE_RADIUS = 7.0


@dataclass(frozen=True)
class CcmCalibration:
    """What the dialog returns: the fitted matrix plus the settings that produced it."""

    matrix: tuple[CcmRow, CcmRow, CcmRow]
    mean_before: float
    mean_after: float
    max_after: float
    used: int
    settings: dict[str, object]
    """Dialog state to restore next time (preset, illuminant, CAT, scale, ...)."""


def render_preview(linear_rgb: np.ndarray, saturation: SaturationValues) -> np.ndarray:
    """Gamma-encoded 8-bit view of the linear RGB the CCM module receives."""
    return render_linear(linear_rgb, saturation.hdr)


class _CornerHandle(QGraphicsEllipseItem):
    """Draggable chart corner. Reports its new position to the overlay."""

    def __init__(self, index: int, overlay: ChartOverlay) -> None:
        super().__init__(-HANDLE_RADIUS, -HANDLE_RADIUS, 2 * HANDLE_RADIUS, 2 * HANDLE_RADIUS)
        self.index = index
        self.overlay = overlay
        self.setBrush(QBrush(QColor(255, 210, 60, 220)))
        self.setPen(QPen(QColor(30, 30, 30), 1))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(3)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemScenePositionHasChanged:
            self.overlay.corner_moved(self.index, self.scenePos())
        return super().itemChange(change, value)


class ChartOverlay(QGraphicsItem):
    """Draws the sampling grid; owns the four corner handles."""

    def __init__(self, scene: QGraphicsScene, on_change) -> None:
        super().__init__()
        self.setZValue(2)
        self._scene = scene
        self._on_change = on_change
        self._quad = ChartQuad.centred(100, 100)
        self._scale = DEFAULT_PATCH_SCALE
        self._polygons: list[QGraphicsPolygonItem] = []
        self._labels: list[QGraphicsSimpleTextItem] = []
        self._handles = [_CornerHandle(index, self) for index in range(4)]
        for handle in self._handles:
            scene.addItem(handle)
        self._updating = False

    # QGraphicsItem needs these; the drawing is done by the child items
    def boundingRect(self) -> QRectF:
        return QRectF()

    def paint(self, painter, option, widget=None) -> None:  # pragma: no cover - nothing to draw
        return

    @property
    def quad(self) -> ChartQuad:
        return self._quad

    def set_quad(self, quad: ChartQuad, *, notify: bool = True) -> None:
        self._quad = quad
        self._updating = True
        for handle, corner in zip(self._handles, quad.corners, strict=True):
            handle.setPos(QPointF(*corner))
        self._updating = False
        self.refresh()
        if notify:
            self._on_change()

    def set_patch_scale(self, scale: float) -> None:
        self._scale = scale
        self.refresh()
        self._on_change()

    def corner_moved(self, index: int, position: QPointF) -> None:
        if self._updating:
            return
        self._quad = self._quad.moved(index, (position.x(), position.y()))
        self.refresh()
        self._on_change()

    def refresh(self) -> None:
        for item in self._polygons + self._labels:
            self._scene.removeItem(item)
        self._polygons.clear()
        self._labels.clear()

        outline = QGraphicsPolygonItem(QPolygonF([QPointF(*c) for c in self._quad.corners]))
        outline.setPen(QPen(QColor(255, 210, 60), 0))
        outline.setZValue(2)
        self._scene.addItem(outline)
        self._polygons.append(outline)

        centres = self._quad.patch_centres()
        font = QFont()
        font.setPointSizeF(8.0)
        for index, polygon in enumerate(self._quad.patch_polygons(self._scale)):
            item = QGraphicsPolygonItem(QPolygonF([QPointF(x, y) for x, y in polygon]))
            item.setPen(QPen(QColor(0, 255, 160), 0))
            item.setZValue(2)
            self._scene.addItem(item)
            self._polygons.append(item)
            label = QGraphicsSimpleTextItem(str(index + 1))
            label.setBrush(QBrush(QColor(255, 255, 255)))
            label.setFont(font)
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setPos(QPointF(*centres[index]))
            label.setZValue(3)
            self._scene.addItem(label)
            self._labels.append(label)


class ChartView(QGraphicsView):
    """Zoomable view of the image; handles drag the chart corners."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


class CcmCalibrationDialog(QDialog):
    """Fit the colour correction matrix on a ColorChecker Classic."""

    fit_changed = Signal()

    def __init__(
        self,
        linear_rgb: np.ndarray,
        saturation: SaturationValues,
        current: tuple[CcmRow, CcmRow, CcmRow] = IDENTITY_CCM,
        settings: dict[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate CCM on a color checker")
        self.setObjectName("ccm_calibration_dialog")
        self.resize(1180, 760)

        self.linear_rgb = linear_rgb
        self.saturation = saturation
        self.current = current
        self.fit: CcmFit | None = None
        self._weights = list(WEIGHT_PRESETS["All patches"])
        self._targets: ReferenceTargets = build_targets()
        self._busy = False

        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        layout.addLayout(body, 1)

        self.scene = QGraphicsScene(self)
        self.view = ChartView()
        self.view.setObjectName("ccm_chart_view")
        self.view.setScene(self.scene)
        self.preview = render_preview(linear_rgb, saturation)
        self.scene.addPixmap(QPixmap.fromImage(numpy_to_qimage(self.preview)))
        body.addWidget(self.view, 3)
        body.addLayout(self._build_controls(), 2)

        self.error_label = QLabel()
        self.error_label.setObjectName("ccm_error")
        self.error_label.setStyleSheet("color: #d04040;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox()
        self.apply_button = self.buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName("ccm_apply")
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._restore(settings or {})
        self.overlay = ChartOverlay(self.scene, self._on_overlay_changed)
        self.scene.addItem(self.overlay)
        quad, message = self._initial_quad(settings or {})
        self.overlay.set_quad(quad, notify=False)
        self.view.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.status_label.setText(message)
        self._refit()

    def _initial_quad(self, settings: dict[str, object]) -> tuple[ChartQuad, str]:
        """Where to put the outline: the remembered position, a detection, or the default."""
        height, width = self.linear_rgb.shape[:2]
        stored = ChartQuad.from_normalised(settings.get("quad"), width, height)
        if stored is not None and stored.area() > 0.01 * width * height:
            return stored, "Outline restored from the last calibration."
        detected = detect_chart(self.preview)
        if detected is not None:
            return detected, "Chart detected."
        return ChartQuad.centred(width, height), "No chart detected - drag the corners onto it."

    # ----------------------------------------------------------------- UI
    def _build_controls(self) -> QVBoxLayout:
        column = QVBoxLayout()

        chart_box = QGroupBox("Chart")
        chart_form = QFormLayout(chart_box)
        buttons = QHBoxLayout()
        detect = QPushButton("Detect chart")
        detect.setObjectName("ccm_detect")
        detect.clicked.connect(self._detect)
        rotate = QPushButton("Rotate 90°")
        rotate.setObjectName("ccm_rotate")
        rotate.setToolTip("Patch 1 (dark skin) must be in the corner marked by the outline")
        rotate.clicked.connect(lambda: self.overlay.set_quad(self.overlay.quad.rotated()))
        reset = QPushButton("Reset outline")
        reset.clicked.connect(self._reset_quad)
        for button in (detect, rotate, reset):
            buttons.addWidget(button)
        chart_form.addRow(buttons)

        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setObjectName("ccm_patch_scale")
        self.scale_slider.setRange(20, 90)
        self.scale_slider.setValue(int(DEFAULT_PATCH_SCALE * 100))
        self.scale_label = QLabel()
        self.scale_slider.valueChanged.connect(self._on_scale)
        scale_row = QHBoxLayout()
        scale_row.addWidget(self.scale_slider, 1)
        scale_row.addWidget(self.scale_label)
        chart_form.addRow("Patch scale", scale_row)
        column.addWidget(chart_box)

        fit_box = QGroupBox("Fit")
        fit_form = QFormLayout(fit_box)
        self.preset_combo = QComboBox()
        self.preset_combo.setObjectName("ccm_preset")
        for name in WEIGHT_PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.addItem(CUSTOM_PRESET)
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        fit_form.addRow("Optimise for", self.preset_combo)

        fit_form.addRow("Chart data", QLabel("ColorChecker Classic 24, Lab D50"))

        self.illuminant_combo = QComboBox()
        self.illuminant_combo.setObjectName("ccm_illuminant")
        for name in ILLUMINANTS:
            self.illuminant_combo.addItem(name)
        self.illuminant_combo.setCurrentText("D65")
        self.illuminant_combo.setToolTip("White point the corrected image should be rendered for")
        self.illuminant_combo.currentTextChanged.connect(self._refit)
        fit_form.addRow("Adapt to", self.illuminant_combo)

        self.cat_combo = QComboBox()
        self.cat_combo.setObjectName("ccm_cat")
        for key in CAT_METHODS:
            self.cat_combo.addItem(CAT_LABELS[key], key)
        self.cat_combo.setToolTip("Chromatic adaptation transform applied to the reference data")
        self.cat_combo.currentIndexChanged.connect(self._refit)
        fit_form.addRow("Adaptation (CAT)", self.cat_combo)

        self.matrix_combo = QComboBox()
        self.matrix_combo.setObjectName("ccm_matrix_type")
        self.matrix_combo.addItem("3×3 linear", False)
        self.matrix_combo.addItem("3×4 with offsets", True)
        self.matrix_combo.currentIndexChanged.connect(self._refit)
        fit_form.addRow("Matrix", self.matrix_combo)

        self.metric_combo = QComboBox()
        self.metric_combo.setObjectName("ccm_metric")
        self.metric_combo.addItem("ΔE2000", "cie2000")
        self.metric_combo.addItem("ΔE76", "cie76")
        self.metric_combo.currentIndexChanged.connect(self._refit)
        fit_form.addRow("Minimise", self.metric_combo)

        self.neutral_box = QCheckBox("Preserve neutral (row sums = 1)")
        self.neutral_box.setObjectName("ccm_preserve_neutral")
        self.neutral_box.toggled.connect(self._refit)
        fit_form.addRow(self.neutral_box)
        column.addWidget(fit_box)

        self.summary_label = QLabel("–")
        self.summary_label.setObjectName("ccm_summary")
        self.summary_label.setWordWrap(True)
        column.addWidget(self.summary_label)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("ccm_table")
        self.table.setHorizontalHeaderLabels(["#", "Patch", "Weight", "ΔE before", "ΔE after"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_weight_edited)
        column.addWidget(self.table, 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("ccm_status")
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)
        return column

    # ------------------------------------------------------------ actions
    def _reset_quad(self) -> None:
        height, width = self.linear_rgb.shape[:2]
        self.overlay.set_quad(ChartQuad.centred(width, height))

    def _detect(self) -> None:
        quad = detect_chart(self.preview)
        if quad is None:
            self.status_label.setText("No chart detected - drag the corners onto the chart.")
            return
        self.overlay.set_quad(quad)
        self.status_label.setText("Chart detected.")

    def _on_overlay_changed(self) -> None:
        """The outline moved: the detection hint no longer applies."""
        self.status_label.clear()
        self._refit()

    def _on_scale(self, value: int) -> None:
        self.scale_label.setText(f"{value} %")
        if self._busy:  # restoring stored settings, before the overlay exists
            return
        self.overlay.set_patch_scale(value / 100)

    def _on_preset(self, name: str) -> None:
        if name in WEIGHT_PRESETS:
            self._weights = list(WEIGHT_PRESETS[name])
            self._refit()

    def _on_weight_edited(self, item: QTableWidgetItem) -> None:
        if self._busy or item.column() != 2:
            return
        try:
            value = max(0.0, float(item.text()))
        except ValueError:
            self._refresh_table()
            return
        self._weights[item.row()] = value
        if self.preset_combo.currentText() != CUSTOM_PRESET:
            self._busy = True
            self.preset_combo.setCurrentText(CUSTOM_PRESET)
            self._busy = False
        self._refit()

    def _refit(self, *_args: object) -> None:
        if self._busy:
            return
        self._targets = build_targets(
            adapt_to=self.illuminant_combo.currentText(),
            cat=str(self.cat_combo.currentData()),
        )
        samples = sample_patches(
            self.linear_rgb,
            self.overlay.quad,
            scale=self.scale_slider.value() / 100,
            saturation=self.saturation,
            targets=self._targets,
        )
        try:
            self.fit = fit_ccm(
                samples,
                targets=self._targets,
                weights=np.array(self._weights),
                saturation=self.saturation,
                affine=bool(self.matrix_combo.currentData()),
                distance=str(self.metric_combo.currentData()),
                preserve_neutral=self.neutral_box.isChecked(),
                current=self.current,
            )
        except CalibrationError as error:
            self.fit = None
            self.error_label.setText(str(error))
            self.error_label.show()
            self.apply_button.setEnabled(False)
            self.summary_label.setText("–")
            return
        self.error_label.hide()
        self.apply_button.setEnabled(True)
        clipped = int(np.count_nonzero(samples.saturated))
        note = f" · {clipped} clipped patch(es) excluded" if clipped else ""
        self.summary_label.setText(
            f"<b>mean ΔE {self.fit.mean_before:.2f} → {self.fit.mean_after:.2f}</b>"
            f" · max {self.fit.max_after:.2f} · neutrals {self.fit.mean_neutral_after:.2f}"
            f" · {self.fit.used} patches{note}"
        )
        self._refresh_table()
        self.fit_changed.emit()

    def _refresh_table(self) -> None:
        if self.fit is None:
            return
        self._busy = True
        self.table.setRowCount(len(self.fit.patches))
        for row, patch in enumerate(self.fit.patches):
            colour = self._targets.linear_rgb[row].clip(0, 1) ** (1 / 2.2)
            swatch = QTableWidgetItem(str(patch.index))
            swatch.setBackground(QBrush(QColor(*(int(255 * c) for c in colour))))
            swatch.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, swatch)

            name = QTableWidgetItem(patch.name + (" (clipped)" if patch.excluded else ""))
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, name)

            weight = QTableWidgetItem(f"{patch.weight:g}")
            self.table.setItem(row, 2, weight)

            for column, value in ((3, patch.delta_e_before), (4, patch.delta_e_after)):
                cell = QTableWidgetItem(f"{value:.2f}")
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if column == 4 and patch.delta_e_after > patch.delta_e_before:
                    cell.setForeground(QBrush(QColor("#d04040")))
                self.table.setItem(row, column, cell)
            if patch.excluded:
                name.setForeground(QBrush(QColor("#808080")))
        self.table.resizeColumnsToContents()
        self._busy = False

    # ------------------------------------------------------------- result
    def settings(self) -> dict[str, object]:
        height, width = self.linear_rgb.shape[:2]
        return {
            "quad": self.overlay.quad.normalised(width, height),
            "preset": self.preset_combo.currentText(),
            "illuminant": self.illuminant_combo.currentText(),
            "cat": str(self.cat_combo.currentData()),
            "scale": self.scale_slider.value(),
            "affine": bool(self.matrix_combo.currentData()),
            "metric": str(self.metric_combo.currentData()),
            "preserve_neutral": self.neutral_box.isChecked(),
        }

    def _restore(self, settings: dict[str, object]) -> None:
        self._busy = True
        preset = str(settings.get("preset", "All patches"))
        if preset in WEIGHT_PRESETS:
            self.preset_combo.setCurrentText(preset)
            self._weights = list(WEIGHT_PRESETS[preset])
        if (illuminant := str(settings.get("illuminant", "D65"))) in ILLUMINANTS:
            self.illuminant_combo.setCurrentText(illuminant)
        cat_index = self.cat_combo.findData(settings.get("cat", "bradford"))
        if cat_index >= 0:
            self.cat_combo.setCurrentIndex(cat_index)
        scale = settings.get("scale")
        self.scale_slider.setValue(
            int(scale) if isinstance(scale, int) else self.scale_slider.value()
        )
        self.matrix_combo.setCurrentIndex(1 if settings.get("affine") else 0)
        metric_index = self.metric_combo.findData(settings.get("metric", "cie2000"))
        if metric_index >= 0:
            self.metric_combo.setCurrentIndex(metric_index)
        self.neutral_box.setChecked(bool(settings.get("preserve_neutral", False)))
        self.scale_label.setText(f"{self.scale_slider.value()} %")
        self._busy = False

    def result_calibration(self) -> CcmCalibration | None:
        """The fitted matrix, or ``None`` when no usable fit was produced."""
        if self.fit is None:
            return None
        return CcmCalibration(
            matrix=self.fit.matrix,
            mean_before=self.fit.mean_before,
            mean_after=self.fit.mean_after,
            max_after=self.fit.max_after,
            used=self.fit.used,
            settings=self.settings(),
        )

"""Zoomable image view with processed / before / split comparison."""

from __future__ import annotations

from enum import StrEnum

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)


class ViewMode(StrEnum):
    PROCESSED = "processed"
    BEFORE = "before"
    SPLIT = "split"


def numpy_to_qimage(array: np.ndarray) -> QImage:
    """Copy a uint8 RGB ``(H, W, 3)`` or greyscale ``(H, W)`` array into a QImage."""
    array = np.ascontiguousarray(array)
    height, width = array.shape[:2]
    if array.ndim == 2:
        image = QImage(array.data, width, height, width, QImage.Format.Format_Grayscale8)
    else:
        image = QImage(array.data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return image.copy()


class CompareItem(QGraphicsItem):
    """Draws the processed and/or before image into the full-resolution image rectangle."""

    def __init__(self) -> None:
        super().__init__()
        self.rect = QRectF()
        self.processed: QPixmap | None = None
        self.before: QPixmap | None = None
        self.mode = ViewMode.PROCESSED
        self.split = 0.5
        self.overlay: list[tuple[QPolygonF, QColor]] = []

    def boundingRect(self) -> QRectF:
        return self.rect

    def set_size(self, width: int, height: int) -> None:
        self.prepareGeometryChange()
        self.rect = QRectF(0, 0, width, height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        scale = painter.worldTransform().m11()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1.0)
        rect = self.rect
        if self.mode == ViewMode.BEFORE:
            self._draw(painter, self.before or self.processed, rect)
        elif self.mode == ViewMode.PROCESSED or self.before is None:
            self._draw(painter, self.processed or self.before, rect)
        else:
            split_x = rect.width() * self.split
            painter.save()
            painter.setClipRect(QRectF(0, 0, split_x, rect.height()))
            self._draw(painter, self.before, rect)
            painter.setClipRect(QRectF(split_x, 0, rect.width() - split_x, rect.height()))
            self._draw(painter, self.processed or self.before, rect)
            painter.restore()
            pen = QPen(QColor(255, 255, 255, 220))
            pen.setCosmetic(True)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(QPointF(split_x, 0), QPointF(split_x, rect.height()))
        if self.overlay:
            outline = QPen(QColor(0, 0, 0, 200))
            outline.setCosmetic(True)
            painter.setPen(outline)
            for polygon, colour in self.overlay:
                painter.setBrush(colour)
                painter.drawPolygon(polygon)

    @staticmethod
    def _draw(painter: QPainter, pixmap: QPixmap | None, rect: QRectF) -> None:
        if pixmap is not None:
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))


class CompareView(QGraphicsView):
    """Image viewer: wheel zoom, drag to pan, draggable split divider."""

    hovered = Signal(int, int)  # full-resolution image coordinates, (-1, -1) when outside
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.item = CompareItem()
        self.scene().addItem(self.item)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(self.palette().window().color().darker(115))
        self.setMouseTracking(True)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._mode = ViewMode.PROCESSED
        self._temporary_before = False
        self._dragging_split = False
        self._fit_mode = True
        self.placeholder = "Drop a .raw, .tif/.tiff or .dng file here\nor use File → Open…"

    # ----- content -----
    @property
    def has_image(self) -> bool:
        return self.item.processed is not None or self.item.before is not None

    def set_size(self, width: int, height: int) -> None:
        self.item.set_size(width, height)
        self.scene().setSceneRect(self.item.rect)

    def set_processed(self, image: QImage | None) -> None:
        self.item.processed = QPixmap.fromImage(image) if image is not None else None
        self.item.update()
        self.viewport().update()

    def set_chart_overlay(self, patches: list[tuple[np.ndarray, tuple[int, int, int]]]) -> None:
        """Draw small filled polygons (full-resolution pixels, colour) over the image."""
        self.item.overlay = [
            (QPolygonF([QPointF(float(x), float(y)) for x, y in polygon]), QColor(*colour))
            for polygon, colour in patches
        ]
        self.item.update()
        self.viewport().update()

    def set_before(self, image: QImage | None) -> None:
        self.item.before = QPixmap.fromImage(image) if image is not None else None
        self.item.update()
        self.viewport().update()

    def clear(self) -> None:
        self.item.processed = None
        self.item.before = None
        self.set_size(0, 0)
        self.viewport().update()

    # ----- modes -----
    @property
    def mode(self) -> ViewMode:
        return self._mode

    def set_mode(self, mode: ViewMode) -> None:
        self._mode = mode
        self._apply_mode()

    def set_temporary_before(self, active: bool) -> None:
        self._temporary_before = active
        self._apply_mode()

    def _apply_mode(self) -> None:
        self.item.mode = ViewMode.BEFORE if self._temporary_before else self._mode
        self.item.update()

    # ----- zoom -----
    def zoom(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        self._fit_mode = True
        if not self.item.rect.isEmpty():
            self.fitInView(self.item.rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self.zoom())

    def actual_size(self) -> None:
        self._fit_mode = False
        self.resetTransform()
        self.zoom_changed.emit(self.zoom())

    def set_zoom(self, factor: float) -> None:
        self._fit_mode = False
        factor = min(max(factor, 0.02), 64.0)
        self.setTransform(self.transform().fromScale(factor, factor))
        self.zoom_changed.emit(self.zoom())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self.has_image:
            return
        steps = event.angleDelta().y() / 120
        if steps:
            self._fit_mode = False
            factor = 1.25**steps
            new_zoom = self.zoom() * factor
            if 0.02 <= new_zoom <= 64:
                self.scale(factor, factor)
                self.zoom_changed.emit(self.zoom())
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()

    # ----- split divider & hover -----
    def _split_x_in_view(self) -> float:
        scene_x = self.item.rect.width() * self.item.split
        return self.mapFromScene(QPointF(scene_x, 0)).x()

    def _near_divider(self, event: QMouseEvent) -> bool:
        return (
            self.item.mode == ViewMode.SPLIT
            and self.has_image
            and abs(event.position().x() - self._split_x_in_view()) <= 6
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._near_divider(event):
            self._dragging_split = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        if self._dragging_split and self.item.rect.width() > 0:
            self.item.split = min(max(scene_pos.x() / self.item.rect.width(), 0.0), 1.0)
            self.item.update()
            event.accept()
            return
        if self._near_divider(event):
            self.viewport().setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        if self.item.rect.contains(scene_pos):
            self.hovered.emit(int(scene_pos.x()), int(scene_pos.y()))
        else:
            self.hovered.emit(-1, -1)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging_split:
            self._dragging_split = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered.emit(-1, -1)
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.has_image:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().placeholderText().color())
            font = painter.font()
            font.setPointSizeF(font.pointSizeF() * 1.3)
            painter.setFont(font)
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
            painter.end()

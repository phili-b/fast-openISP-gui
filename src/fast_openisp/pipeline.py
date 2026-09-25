"""The fast-openISP pipeline: runs the enabled modules in order on a Bayer array."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from fast_openisp.config import MODULE_ORDER, ConfigError, IspConfig
from fast_openisp.imaging import ycbcr_to_rgb
from fast_openisp.modules import (
    MODULE_CLASSES,
    Context,
    ISPModule,
    PipelineData,
    PipelineError,
    SaturationValues,
)

__all__ = [
    "Pipeline",
    "PipelineCancelled",
    "PipelineError",
    "PipelineResult",
    "compute_saturation_values",
    "render_output",
]

ProgressCallback = Callable[[str, int, int], None]
CancelCallback = Callable[[], bool]


class PipelineCancelled(Exception):
    """Raised when a run is cancelled between modules."""


@dataclass
class PipelineResult:
    image: np.ndarray
    """Displayable RGB result, ``(H, W, 3)`` uint8 (or ``(H, W)`` when CFA is disabled)."""
    awb_gains: tuple[float, float, float, float]
    """White-balance gains (R, Gr, Gb, B) that AWB applied."""
    timings: dict[str, float] = field(default_factory=dict)
    """Seconds spent per module."""
    stats: dict[str, object] = field(default_factory=dict)
    """Per-module measurements reported by the run, such as ``dpc_corrected``."""

    @property
    def elapsed(self) -> float:
        return sum(self.timings.values())


def compute_saturation_values(config: IspConfig) -> SaturationValues:
    """Saturation pixel values in the pipeline stages.

    Raw: before BLC. HDR: after BLC, before gamma. SDR: after gamma.
    """
    raw_max_value = 2**config.hardware.bit_depth - 1
    blc = config.modules.blc
    if blc.enabled:
        alpha = round(blc.alpha * 1024)
        beta = round(blc.beta * 1024)
        hdr_max_r = raw_max_value - blc.bl_r
        hdr_max_b = raw_max_value - blc.bl_b
        hdr_max_gr = int(raw_max_value - blc.bl_gr + hdr_max_r * alpha / 1024)
        hdr_max_gb = int(raw_max_value - blc.bl_gb + hdr_max_b * beta / 1024)
        hdr_max_value = max(hdr_max_r, hdr_max_b, hdr_max_gr, hdr_max_gb)
    else:
        hdr_max_value = raw_max_value
    return SaturationValues(raw=raw_max_value, hdr=hdr_max_value)


def render_output(data: PipelineData, saturation: SaturationValues) -> np.ndarray:
    """Convert the pipeline's final data to a displayable uint8 image."""
    if data.y_image is not None and data.cbcr_image is not None:
        ycbcr_image = np.dstack([data.y_image[..., None], data.cbcr_image])
        return ycbcr_to_rgb(ycbcr_image)
    if data.rgb_image is not None:
        output = data.rgb_image
        if output.dtype != np.uint8:
            output = (255 * output.astype(np.float32) / saturation.hdr).astype(np.uint8)
        return output
    # Not an RGB image; looks very dark for most cameras
    return (255 * data.bayer.astype(np.float32) / saturation.raw).astype(np.uint8)


class Pipeline:
    """Core fast-openISP pipeline built from an :class:`IspConfig`."""

    def __init__(self, config: IspConfig, *, preview_factor: int = 1) -> None:
        missing = config.missing_dependencies()
        if missing:
            details = ", ".join(f"{m.upper()} requires {r.upper()}" for m, r in missing)
            raise ConfigError(f"Unsatisfied module dependencies: {details}")

        self.config = config
        self.saturation = compute_saturation_values(config)
        self.ctx = Context(
            bayer_pattern=config.hardware.bayer_pattern,
            bit_depth=config.hardware.bit_depth,
            saturation=self.saturation,
            preview_factor=preview_factor,
        )
        self.modules: dict[str, ISPModule] = {
            name: MODULE_CLASSES[name](config.modules.get(name), self.ctx)
            for name in config.modules.enabled_names()
        }

    def _run_modules(
        self,
        bayer: np.ndarray,
        *,
        stop_before: str | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> tuple[PipelineData, dict[str, float]]:
        if bayer.ndim != 2:
            raise PipelineError(f"Expected a 2-D Bayer array, got shape {bayer.shape}")
        if bayer.shape[0] % 2 or bayer.shape[1] % 2:
            raise PipelineError(f"Bayer dimensions must be even, got {bayer.shape}")

        # Compare pipeline positions, not names: a disabled module is not in self.modules
        limit = MODULE_ORDER.index(stop_before) if stop_before is not None else len(MODULE_ORDER)
        data = PipelineData(bayer=bayer)
        timings: dict[str, float] = {}
        total = len(self.modules)

        for index, (name, module) in enumerate(self.modules.items()):
            if MODULE_ORDER.index(name) >= limit:
                break
            if cancel is not None and cancel():
                raise PipelineCancelled
            if progress is not None:
                progress(name, index, total)
            start = time.perf_counter()
            try:
                module.execute(data)
            except PipelineError:
                raise
            except Exception as error:
                raise PipelineError(f"{name.upper()} failed: {error}") from error
            timings[name] = time.perf_counter() - start

        if progress is not None:
            progress("", total, total)
        return data, timings

    def execute(
        self,
        bayer: np.ndarray,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> PipelineResult:
        """Run all enabled modules on ``bayer`` (``(H, W)`` integer array, even dimensions)."""
        data, timings = self._run_modules(bayer, progress=progress, cancel=cancel)
        return PipelineResult(
            image=render_output(data, self.saturation),
            awb_gains=data.awb_gains,
            timings=timings,
            stats=data.extras,
        )

    def run_until(self, bayer: np.ndarray, module: str) -> PipelineData:
        """Run only the enabled modules that come before ``module`` in pipeline order.

        Used by the colour checker calibration, which needs the data the CCM module receives.
        """
        if module not in MODULE_ORDER:
            raise PipelineError(f"Unknown module {module!r}")
        data, _ = self._run_modules(bayer, stop_before=module)
        return data

"""Smoke test used by the packaged executable (``fast-openISP.exe --self-test``)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from fast_openisp.config import bundled_configs
from fast_openisp.imaging import downscale_bayer, preview_factor, render_before
from fast_openisp.io.export import save_image
from fast_openisp.pipeline import Pipeline


def synthetic_bayer(width: int = 1090, height: int = 1096, bit_depth: int = 10) -> np.ndarray:
    """A deterministic gradient test pattern with some noise."""
    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:height, 0:width]
    base = (x / width * 0.6 + y / height * 0.3) * (2**bit_depth - 1)
    noise = rng.normal(0, 4, size=(height, width))
    return np.clip(base + noise + 32, 0, 2**bit_depth - 1).astype(np.uint16)


def run_self_test(output_dir: Path | None = None) -> Path:
    """Process synthetic connan-sized data at preview and full size; write a PNG."""
    config = bundled_configs()["connan_raw14"]
    bayer = synthetic_bayer(bit_depth=config.hardware.bit_depth)
    factor = preview_factor(bayer.shape)
    small = downscale_bayer(bayer, factor, config.hardware.bayer_pattern)
    render_before(small, config.hardware.bit_depth, config.hardware.bayer_pattern)
    Pipeline(config, preview_factor=factor).execute(small)
    result = Pipeline(config).execute(bayer)
    if result.image.shape != (1096, 1090, 3):
        raise RuntimeError(f"Unexpected output shape {result.image.shape}")

    folder = output_dir or Path(tempfile.gettempdir())
    return save_image(result.image, folder / "fast-openisp-self-test.png")

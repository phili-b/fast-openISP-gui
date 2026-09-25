"""Image utilities: color conversion, CFA-preserving downscaling and the "before" rendering."""

from __future__ import annotations

import math

import cv2
import numpy as np

from fast_openisp.config import BayerPattern
from fast_openisp.modules.helpers import reconstruct_bayer, split_bayer

DEFAULT_PREVIEW_MAX_EDGE = 1024


def ycbcr_to_rgb(ycbcr_array: np.ndarray) -> np.ndarray:
    """Convert a uint8 YCbCr (BT.601 limited range) image to uint8 RGB."""
    assert ycbcr_array.dtype == np.uint8

    matrix = np.array([[298, 0, 409], [298, -100, -208], [298, 516, 0]], dtype=np.int32).T  # x256
    bias = np.array([-56992, 34784, -70688], dtype=np.int32).reshape(1, 1, 3)  # x256

    rgb_array = np.right_shift(ycbcr_array.astype(np.int32) @ matrix + bias, 8)
    return np.clip(rgb_array, 0, 255).astype(np.uint8)


def preview_factor(shape: tuple[int, ...], max_edge: int = DEFAULT_PREVIEW_MAX_EDGE) -> int:
    """Smallest integer downscale factor so the longest edge is at most ``max_edge``."""
    return max(1, math.ceil(max(shape[:2]) / max_edge))


def downscale_bayer(bayer: np.ndarray, factor: int, pattern: BayerPattern) -> np.ndarray:
    """Downscale a Bayer array by an integer factor, keeping its CFA layout.

    Each color plane is block-averaged over ``factor × factor`` blocks and re-mosaicked, so
    per-channel means (and thus grey-world white balance) are preserved.
    """
    if factor <= 1:
        return bayer
    planes = split_bayer(bayer, pattern)
    ph = planes[0].shape[0] // factor
    pw = planes[0].shape[1] // factor
    small = []
    for plane in planes:
        blocks = plane[: ph * factor, : pw * factor].reshape(ph, factor, pw, factor)
        small.append(blocks.mean(axis=(1, 3)).round().astype(bayer.dtype))
    return reconstruct_bayer(small, pattern)


def render_before(bayer: np.ndarray, bit_depth: int, pattern: BayerPattern) -> np.ndarray:
    """Quick reference rendering of the unprocessed input.

    Linear scaling to 8 bit, half-resolution demosaic upsampled to full size, and a 1/2.2
    display gamma. No black level, white balance or color correction.
    """
    r, gr, gb, b = (p.astype(np.float32) for p in split_bayer(bayer, pattern))
    rgb = np.dstack([r, (gr + gb) / 2, b]) / float(2**bit_depth - 1)
    rgb = np.clip(rgb, 0.0, 1.0) ** (1 / 2.2)
    rgb8 = (rgb * 255 + 0.5).astype(np.uint8)
    height, width = bayer.shape[:2]
    return np.asarray(cv2.resize(rgb8, (width, height), interpolation=cv2.INTER_LINEAR))


def render_linear(linear_rgb: np.ndarray, saturation: int) -> np.ndarray:
    """Gamma-encoded 8-bit view of a linear RGB image, for display and chart detection."""
    normalised = np.clip(linear_rgb.astype(np.float32) / saturation, 0.0, 1.0)
    return (255 * normalised ** (1 / 2.2) + 0.5).astype(np.uint8)

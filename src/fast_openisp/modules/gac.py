"""Gamma correction: linear gain followed by a power-law LUT to 8 bit."""

import numpy as np

from fast_openisp.config import GACParams

from .base import Context, ISPModule, PipelineData, to_fixed

MAX_GAIN = 16.0
"""Upper limit of ``GACParams.gain``, also applied to the automatic gain."""


class GAC(ISPModule[GACParams]):
    name = "gac"

    def __init__(self, params: GACParams, ctx: Context) -> None:
        super().__init__(params, ctx)
        hdr = ctx.saturation.hdr
        self.gain = np.array(to_fixed(params.gain, 256), dtype=np.uint32)  # x256
        x = np.arange(hdr + 1)
        linear = x / hdr
        if params.curve == "srgb":
            encoded = np.where(
                linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055
            )
        else:
            encoded = linear**params.gamma
        lut = encoded * ctx.saturation.sdr
        self.lut = lut.astype(np.uint8)

    def _auto_gain(self, rgb_image: np.ndarray) -> float:
        """Gain that saturates the brightest pixels, letting ``auto_gain_clip`` % of them clip."""
        values = rgb_image.ravel()
        rank = min(values.size - 1, int(values.size * (1 - self.params.auto_gain_clip / 100)))
        level = int(np.partition(values, rank)[rank])
        if level <= 0:
            return 1.0
        return min(self.ctx.saturation.hdr / level, MAX_GAIN)

    def execute(self, data: PipelineData) -> None:
        rgb_image = data.require_rgb().astype(np.uint32)
        gain = self.gain
        if self.params.auto_gain:
            gain = np.array(to_fixed(self._auto_gain(rgb_image), 256), dtype=np.uint32)
        data.extras["gac_gain"] = float(gain) / 256

        gac_rgb_image = np.right_shift(gain * rgb_image, 8)
        gac_rgb_image = np.clip(gac_rgb_image, 0, self.ctx.saturation.hdr)

        data.rgb_image = self.lut[gac_rgb_image]

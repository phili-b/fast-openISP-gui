"""Dead pixel correction: replaces pixels that differ strongly from all neighbours."""

import numpy as np

from fast_openisp.config import DPCParams

from .base import ISPModule, PipelineData
from .helpers import pad, reconstruct_bayer, shift_array, split_bayer


class DPC(ISPModule[DPCParams]):
    name = "dpc"

    def execute(self, data: PipelineData) -> None:
        bayer = data.bayer.astype(np.int32)
        threshold = self.params.diff_threshold

        padded_bayer = pad(bayer, pads=2)
        padded_sub_arrays = split_bayer(padded_bayer, self.ctx.bayer_pattern)

        dpc_sub_arrays = []
        corrected = 0
        for padded_array in padded_sub_arrays:
            s = shift_array(padded_array, window_size=3)
            center = s[4]

            mask = np.ones(center.shape, dtype=bool)
            for i in (1, 7, 3, 5, 0, 2, 6, 8):
                mask &= np.abs(center - s[i]) > threshold

            dv = np.abs(2 * center - s[1] - s[7])
            dh = np.abs(2 * center - s[3] - s[5])
            ddl = np.abs(2 * center - s[0] - s[8])
            ddr = np.abs(2 * center - s[6] - s[2])
            indices = np.argmin(np.dstack([dv, dh, ddl, ddr]), axis=2)[..., None]

            neighbor_stack = np.right_shift(
                np.dstack([s[1] + s[7], s[3] + s[5], s[0] + s[8], s[6] + s[2]]), 1
            )
            dpc_array = np.take_along_axis(neighbor_stack, indices, axis=2).squeeze(2)
            dpc_sub_arrays.append(mask * dpc_array + ~mask * center)
            corrected += int(np.count_nonzero(mask))

        dpc_bayer = reconstruct_bayer(dpc_sub_arrays, self.ctx.bayer_pattern)

        data.bayer = dpc_bayer.astype(np.uint16)
        data.extras["dpc_corrected"] = corrected
        data.extras["dpc_total"] = int(bayer.size)

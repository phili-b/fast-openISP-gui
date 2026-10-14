"""The refactored pipeline must reproduce the outputs of the original fast-openISP code.

Golden images in ``tests/golden`` were generated with the original (pre-refactor) code.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from fast_openisp.config import IspConfig, bundled_configs
from fast_openisp.io.loaders import load_raw, load_tiff
from fast_openisp.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
TEST_RAW = ROOT / "raw" / "test.RAW"
MIKROS = ROOT / "raw" / "mikros110.tiff"


def _golden(name: str) -> np.ndarray:
    image = cv2.imread(str(GOLDEN / f"{name}.png"), cv2.IMREAD_COLOR)
    assert image is not None
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _update(config: IspConfig, **modules: dict) -> IspConfig:
    data = config.model_dump()
    for name, changes in modules.items():
        data["modules"][name].update(changes)
    return IspConfig.model_validate(data)


@pytest.fixture(scope="module")
def test_config() -> IspConfig:
    return bundled_configs()["test"]


@pytest.fixture(scope="module")
def test_bayer(test_config: IspConfig) -> np.ndarray:
    hw = test_config.hardware
    return load_raw(TEST_RAW, hw.width, hw.height, hw.bit_depth, hw.bayer_pattern).bayer


def test_default(test_config: IspConfig, test_bayer: np.ndarray) -> None:
    result = Pipeline(test_config).execute(test_bayer)
    np.testing.assert_array_equal(result.image, _golden("test_default"))


def test_variant(test_config: IspConfig, test_bayer: np.ndarray) -> None:
    config = _update(
        test_config,
        ceh={"enabled": False},
        cfa={"mode": "bilinear"},
        blc={
            "bl_r": 16,
            "bl_gr": 12,
            "bl_gb": 14,
            "bl_b": 18,
            "alpha": 128 / 1024,
            "beta": 64 / 1024,
        },
        awb={
            "r_gain": 1700 / 1024,
            "b_gain": 1400 / 1024,
            "gr_gain": 1000 / 1024,
            "gb_gain": 1040 / 1024,
        },
        cnf={"diff_threshold": 4},
        ccm={
            "ccm": [
                [v / 1024 for v in row]
                for row in [[1280, -128, -128, 0], [-256, 1536, -256, 8], [0, -512, 1536, -4]]
            ]
        },
        gac={"auto_gain": False, "gain": 300 / 256, "gamma": 0.45},
        nlm={"h": 6},
        bnf={"intensity_sigma": 0.5, "spatial_sigma": 0.5},
        eeh={"edge_gain": 2.0},
        hsc={"hue_offset": 10, "saturation_gain": 300 / 256},
        bcc={"brightness_offset": 10, "contrast_gain": 280 / 256},
    )
    result = Pipeline(config).execute(test_bayer)
    np.testing.assert_array_equal(result.image, _golden("test_variant"))


def test_rgb_scaled(test_config: IspConfig, test_bayer: np.ndarray) -> None:
    disabled = {"enabled": False}
    config = _update(
        test_config,
        **{m: disabled for m in ("csc", "nlm", "bnf", "ceh", "eeh", "fcs", "hsc", "bcc")},
        scl={"enabled": True, "width": 960, "height": 540},
    )
    result = Pipeline(config).execute(test_bayer)
    np.testing.assert_array_equal(result.image, _golden("test_rgb_scaled"))


def test_mikros110_manual_wb(test_config: IspConfig) -> None:
    # The golden image used the `test` parameters with the mikros110 sensor settings. Build that
    # here rather than using the bundled mikros110 config, which is meant to be tuned freely.
    config = test_config.with_hardware(width=1090, height=1096, bit_depth=10, bayer_pattern="bggr")
    config = _update(
        config,
        blc={"bl_r": 32, "bl_gr": 32, "bl_gb": 32, "bl_b": 32},
        awb={"mode": "manual", "r_gain": 1.5, "b_gain": 1.75},
    )
    raw = load_tiff(MIKROS, bayer_pattern="bggr")
    assert raw.bit_depth == 10
    result = Pipeline(config).execute(raw.bayer)
    np.testing.assert_array_equal(result.image, _golden("mikros110_manual"))

"""Typed ISP configuration, validated with Pydantic.

A configuration consists of the sensor ``hardware`` description and one entry per ISP
module (in fixed pipeline order). Every module entry carries an ``enabled`` flag plus the
module's parameters. Fixed-point parameters of the original openISP (``×1024`` / ``×256``)
are expressed as real numbers here; modules convert them to fixed point internally.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "MODULE_INFO",
    "MODULE_ORDER",
    "BayerPattern",
    "ConfigError",
    "HardwareConfig",
    "IspConfig",
    "ModuleInfo",
    "ModuleParams",
    "ModulesConfig",
    "bundled_configs",
    "format_validation_error",
    "load_config",
    "save_config",
]

BayerPattern = Literal["rggb", "bggr", "grbg", "gbrg"]
BAYER_PATTERNS: tuple[BayerPattern, ...] = ("rggb", "bggr", "grbg", "gbrg")


class ConfigError(ValueError):
    """Raised when a configuration cannot be loaded or is inconsistent."""


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    full_name: str
    requires: tuple[str, ...] = ()


MODULE_INFO: dict[str, ModuleInfo] = {
    info.name: info
    for info in (
        ModuleInfo("dpc", "Dead Pixel Correction"),
        ModuleInfo("blc", "Black Level Compensation"),
        ModuleInfo("aaf", "Anti-Aliasing Filter"),
        ModuleInfo("awb", "Auto White Balance"),
        ModuleInfo("cnf", "Chroma Noise Filter"),
        ModuleInfo("cfa", "Demosaicing (CFA interpolation)"),
        ModuleInfo("ccm", "Color Correction Matrix", ("cfa",)),
        ModuleInfo("gac", "Gamma and Gain", ("cfa",)),
        ModuleInfo("csc", "Color Space Conversion", ("gac",)),
        ModuleInfo("nlm", "Non-Local Means Denoising", ("csc",)),
        ModuleInfo("bnf", "Bilateral Noise Filter", ("csc",)),
        ModuleInfo("ceh", "Contrast Enhancement (CLAHE)", ("csc",)),
        ModuleInfo("eeh", "Edge Enhancement", ("csc",)),
        ModuleInfo("fcs", "False Color Suppression", ("csc", "eeh")),
        ModuleInfo("hsc", "Hue / Saturation Control", ("csc",)),
        ModuleInfo("bcc", "Brightness / Contrast Control", ("csc",)),
        ModuleInfo("scl", "Scaler", ("cfa",)),
    )
}
MODULE_ORDER: tuple[str, ...] = tuple(MODULE_INFO)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=False)


def _unit(unit: str, *, decimals: int | None = None, step: float | None = None) -> dict[str, Any]:
    extra: dict[str, Any] = {"unit": unit}
    if decimals is not None:
        extra["decimals"] = decimals
    if step is not None:
        extra["step"] = step
    return extra


class HardwareConfig(_Model):
    """Sensor description. ``width``/``height`` are only used to read headerless raw files."""

    width: int = Field(1920, gt=0, description="Raw image width in pixels")
    height: int = Field(1080, gt=0, description="Raw image height in pixels")
    bit_depth: int = Field(10, ge=8, le=16, description="Raw data bit depth")
    bayer_pattern: BayerPattern = Field("rggb", description="Color filter array layout")


class ModuleParams(_Model):
    enabled: bool = Field(True, description="Whether the module runs in the pipeline")


class DPCParams(ModuleParams):
    diff_threshold: int = Field(
        30, ge=0, le=65535, description="Min difference to all 8 neighbours to flag a dead pixel"
    )


class BLCParams(ModuleParams):
    bl_r: int = Field(0, ge=0, le=65535, description="Black level of R pixels (subtracted)")
    bl_gr: int = Field(0, ge=0, le=65535, description="Black level of Gr pixels (subtracted)")
    bl_gb: int = Field(0, ge=0, le=65535, description="Black level of Gb pixels (subtracted)")
    bl_b: int = Field(0, ge=0, le=65535, description="Black level of B pixels (subtracted)")
    alpha: float = Field(
        0.0, ge=-1, le=1, description="R→Gr crosstalk compensation", json_schema_extra=_unit("")
    )
    beta: float = Field(
        0.0, ge=-1, le=1, description="B→Gb crosstalk compensation", json_schema_extra=_unit("")
    )


class AAFParams(ModuleParams):
    pass


class AWBParams(ModuleParams):
    mode: Literal["manual", "grey_world"] = Field(
        "manual", description="manual: use the gains below; grey_world: estimate gains per image"
    )
    r_gain: float = Field(1.0, ge=0, le=8, description="Gain for R pixels")
    gr_gain: float = Field(1.0, ge=0, le=8, description="Gain for Gr pixels")
    gb_gain: float = Field(1.0, ge=0, le=8, description="Gain for Gb pixels")
    b_gain: float = Field(1.0, ge=0, le=8, description="Gain for B pixels")


class CNFParams(ModuleParams):
    diff_threshold: int = Field(
        0, ge=0, le=65535, description="Chroma noise detection threshold (uses the AWB gains)"
    )


class CFAParams(ModuleParams):
    mode: Literal["malvar", "bilinear"] = Field("malvar", description="Demosaicing algorithm")


CcmRow = tuple[float, float, float, float]
IDENTITY_CCM: tuple[CcmRow, CcmRow, CcmRow] = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
)


class CCMParams(ModuleParams):
    ccm: tuple[CcmRow, CcmRow, CcmRow] = Field(
        IDENTITY_CCM,
        description="3×4 matrix: rows are output R, G, B; columns are input R, G, B and offset",
    )

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        for row in self.ccm:
            for value in row[:3]:
                if not -8 <= value <= 8:
                    raise ValueError("CCM coefficients must be within [-8, 8]")
        return self


class GACParams(ModuleParams):
    auto_gain: bool = Field(
        True, description="Choose the gain automatically from the image (overrides gain)"
    )
    auto_gain_clip: float = Field(
        1.0,
        ge=0,
        le=50,
        description="Percentage of the brightest pixels allowed to clip with automatic gain",
        json_schema_extra=_unit("%", decimals=2, step=0.1),
    )
    gain: float = Field(1.0, ge=0, le=16, description="Linear gain applied before the gamma curve")
    curve: Literal["power", "srgb"] = Field(
        "power", description="Transfer curve: power law with the gamma exponent, or the sRGB curve"
    )
    gamma: float = Field(
        0.42, gt=0, le=4, description="Gamma exponent (output = input^gamma); unused for sRGB"
    )


class CSCParams(ModuleParams):
    pass


class NLMParams(ModuleParams):
    search_window_size: int = Field(9, ge=3, le=21, description="Search window size (odd)")
    patch_size: int = Field(3, ge=1, le=9, description="Patch size (odd)")
    h: float = Field(10, gt=0, le=100, description="Filter strength; larger smooths more")

    @model_validator(mode="after")
    def _check_odd(self) -> Self:
        if self.search_window_size % 2 == 0 or self.patch_size % 2 == 0:
            raise ValueError("search_window_size and patch_size must be odd")
        return self


class BNFParams(ModuleParams):
    intensity_sigma: float = Field(0.8, gt=0, le=10, description="Range sigma; larger smooths more")
    spatial_sigma: float = Field(0.8, gt=0, le=10, description="Spatial sigma; larger smooths more")


class CEHParams(ModuleParams):
    tiles: tuple[int, int] = Field((4, 6), description="Number of tiles (rows, columns), each ≥ 2")
    clip_limit: float = Field(0.01, ge=0, le=1, description="Histogram clip limit (fraction)")

    @model_validator(mode="after")
    def _check_tiles(self) -> Self:
        if min(self.tiles) < 2 or max(self.tiles) > 64:
            raise ValueError("tiles must be within [2, 64]")
        return self


class EEHParams(ModuleParams):
    edge_gain: float = Field(1.5, ge=0, le=16, description="Gain applied to strong edges")
    flat_threshold: int = Field(4, ge=0, le=255, description="Deltas ≤ this are set to 0")
    edge_threshold: int = Field(8, ge=0, le=255, description="Deltas > this get the full gain")
    delta_threshold: int = Field(64, ge=0, le=255, description="Maximum enhancement delta")

    @model_validator(mode="after")
    def _check_thresholds(self) -> Self:
        if self.flat_threshold > self.edge_threshold:
            raise ValueError("flat_threshold must be ≤ edge_threshold")
        return self


class FCSParams(ModuleParams):
    delta_min: int = Field(8, ge=0, le=255, description="Edge strength where suppression starts")
    delta_max: int = Field(32, ge=0, le=255, description="Edge strength of full suppression")

    @model_validator(mode="after")
    def _check_deltas(self) -> Self:
        if self.delta_min >= self.delta_max:
            raise ValueError("delta_min must be < delta_max")
        return self


class HSCParams(ModuleParams):
    hue_offset: float = Field(0, ge=-180, le=180, description="Hue rotation in degrees")
    saturation_gain: float = Field(1.0, ge=0, le=4, description="Saturation multiplier")


class BCCParams(ModuleParams):
    brightness_offset: int = Field(0, ge=-255, le=255, description="Added to luma")
    contrast_gain: float = Field(1.0, ge=0, le=4, description="Contrast multiplier around median")


class SCLParams(ModuleParams):
    enabled: bool = Field(False, description="Whether the module runs in the pipeline")
    width: int = Field(1920, gt=0, le=32768, description="Output width in pixels")
    height: int = Field(1080, gt=0, le=32768, description="Output height in pixels")


class ModulesConfig(_Model):
    """All ISP modules in pipeline order (field order is the execution order)."""

    dpc: DPCParams = Field(default_factory=DPCParams)
    blc: BLCParams = Field(default_factory=BLCParams)
    aaf: AAFParams = Field(default_factory=AAFParams)
    awb: AWBParams = Field(default_factory=AWBParams)
    cnf: CNFParams = Field(default_factory=CNFParams)
    cfa: CFAParams = Field(default_factory=CFAParams)
    ccm: CCMParams = Field(default_factory=CCMParams)
    gac: GACParams = Field(default_factory=GACParams)
    csc: CSCParams = Field(default_factory=CSCParams)
    nlm: NLMParams = Field(default_factory=NLMParams)
    bnf: BNFParams = Field(default_factory=BNFParams)
    ceh: CEHParams = Field(default_factory=CEHParams)
    eeh: EEHParams = Field(default_factory=EEHParams)
    fcs: FCSParams = Field(default_factory=FCSParams)
    hsc: HSCParams = Field(default_factory=HSCParams)
    bcc: BCCParams = Field(default_factory=BCCParams)
    scl: SCLParams = Field(default_factory=SCLParams)

    def get(self, name: str) -> ModuleParams:
        if name not in MODULE_INFO:
            raise KeyError(name)
        value = getattr(self, name)
        assert isinstance(value, ModuleParams)
        return value

    def enabled_names(self) -> list[str]:
        return [name for name in MODULE_ORDER if self.get(name).enabled]


class IspConfig(_Model):
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    modules: ModulesConfig = Field(default_factory=ModulesConfig)

    @model_validator(mode="after")
    def _check_black_levels(self) -> Self:
        max_value = 2**self.hardware.bit_depth - 1
        blc = self.modules.blc
        for name in ("bl_r", "bl_gr", "bl_gb", "bl_b"):
            if getattr(blc, name) > max_value:
                raise ValueError(
                    f"modules.blc.{name} exceeds the {self.hardware.bit_depth}-bit maximum "
                    f"({max_value})"
                )
        return self

    def missing_dependencies(self) -> list[tuple[str, str]]:
        """Return ``(module, missing_prerequisite)`` pairs for enabled modules."""
        enabled = set(self.modules.enabled_names())
        return [
            (name, req)
            for name in MODULE_ORDER
            if name in enabled
            for req in MODULE_INFO[name].requires
            if req not in enabled
        ]

    def with_module(self, name: str, params: ModuleParams) -> IspConfig:
        """Return a copy with one module's parameters replaced."""
        modules = self.modules.model_copy(update={name: params})
        return self.model_copy(update={"modules": modules})

    def with_hardware(self, **changes: Any) -> IspConfig:
        hardware = HardwareConfig.model_validate(self.hardware.model_dump() | changes)
        return IspConfig.model_validate(
            {"hardware": hardware.model_dump(), "modules": self.modules.model_dump()}
        )


def resolve_enabled(requested: dict[str, bool]) -> dict[str, bool]:
    """Apply module dependencies: a module is effectively enabled only when it is requested
    and all its (transitive) prerequisites are effectively enabled."""
    effective: dict[str, bool] = {}
    for name in MODULE_ORDER:  # prerequisites always precede dependents
        effective[name] = requested.get(name, False) and all(
            effective[req] for req in MODULE_INFO[name].requires
        )
    return effective


def format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        lines.append(f"{location}: {item['msg']}" if location else item["msg"])
    return "\n".join(lines)


_LEGACY_SCALES: dict[tuple[str, str], int] = {
    ("blc", "alpha"): 1024,
    ("blc", "beta"): 1024,
    ("awb", "r_gain"): 1024,
    ("awb", "gr_gain"): 1024,
    ("awb", "gb_gain"): 1024,
    ("awb", "b_gain"): 1024,
    ("gac", "gain"): 256,
    ("eeh", "edge_gain"): 256,
    ("hsc", "saturation_gain"): 256,
    ("bcc", "contrast_gain"): 256,
}
_LEGACY_DROPPED = {("cnf", "r_gain"), ("cnf", "b_gain")}  # CNF now follows the AWB gains


def is_legacy_config(data: Any) -> bool:
    """True for configs in the original fast-openISP layout (``module_enable_status`` etc.)."""
    return isinstance(data, dict) and "module_enable_status" in data


def migrate_legacy_config(data: dict[str, Any]) -> dict[str, Any]:
    """Convert an original fast-openISP config (fixed-point integers) to the current layout."""
    hardware = data.get("hardware") or {}
    new_hardware = {
        "width": hardware.get("raw_width", hardware.get("width")),
        "height": hardware.get("raw_height", hardware.get("height")),
        "bit_depth": hardware.get("raw_bit_depth", hardware.get("bit_depth")),
        "bayer_pattern": hardware.get("bayer_pattern"),
    }
    modules: dict[str, Any] = {}
    for name, enabled in (data.get("module_enable_status") or {}).items():
        entry: dict[str, Any] = {"enabled": bool(enabled)}
        for key, value in (data.get(name) or {}).items():
            if (name, key) in _LEGACY_DROPPED:
                continue
            if (name, key) in _LEGACY_SCALES:
                value = value / _LEGACY_SCALES[name, key]
            elif name == "ccm" and key == "ccm":
                value = [[item / 1024 for item in row] for row in value]
            entry[key] = value
        modules[name] = entry
    return {
        "hardware": {k: v for k, v in new_hardware.items() if v is not None},
        "modules": modules,
    }


def config_from_dict(data: Any) -> IspConfig:
    if is_legacy_config(data):
        data = migrate_legacy_config(data)
    try:
        return IspConfig.model_validate(data if data is not None else {})
    except ValidationError as error:
        raise ConfigError(format_validation_error(error)) from error


def load_config(source: str | Path) -> IspConfig:
    """Load and validate a YAML configuration file."""
    path = Path(source)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Cannot read {path.name}: {error}") from error
    return config_from_dict(data)


class _YamlDumper(yaml.SafeDumper):
    """Block style for mappings; flow style (``[1, 2]``) for lists of scalars."""


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.Node:
    flow = all(not isinstance(item, (list, dict)) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_YamlDumper.add_representer(list, _represent_list)


def config_to_yaml(config: IspConfig) -> str:
    data = config.model_dump(mode="json")
    return yaml.dump(
        data, Dumper=_YamlDumper, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def save_config(config: IspConfig, path: str | Path) -> None:
    Path(path).write_text(config_to_yaml(config), encoding="utf-8")


def bundled_configs() -> dict[str, IspConfig]:
    """Configurations shipped with the package, keyed by file stem.

    Invalid files are skipped (with a warning) so one bad file cannot break the app.
    """
    configs: dict[str, IspConfig] = {}
    folder = resources.files("fast_openisp") / "configs"
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith((".yaml", ".yml")):
            continue
        name = entry.name.rsplit(".", 1)[0]
        try:
            data = yaml.safe_load(entry.read_text(encoding="utf-8"))
            configs[name] = config_from_dict(data)
        except (yaml.YAMLError, ConfigError) as error:
            warnings.warn(f"Skipping bundled config {entry.name}: {error}", stacklevel=2)
    return configs

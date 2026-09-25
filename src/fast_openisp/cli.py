"""Command-line interface (no Qt dependency)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from fast_openisp import __version__
from fast_openisp.config import (
    BAYER_PATTERNS,
    ConfigError,
    IspConfig,
    bundled_configs,
    load_config,
)
from fast_openisp.io.export import ExportError, save_image
from fast_openisp.io.loaders import LoadError, load_image
from fast_openisp.pipeline import Pipeline, PipelineError


def resolve_config(value: str) -> IspConfig:
    """A YAML path, or the name of a bundled configuration."""
    path = Path(value)
    if path.suffix.lower() in (".yaml", ".yml") or path.exists():
        return load_config(path)
    configs = bundled_configs()
    if value in configs:
        return configs[value]
    raise ConfigError(f"No config file or bundled config named {value!r}")


def apply_raw_metadata(config: IspConfig, raw_bit_depth: int, raw_pattern: str) -> IspConfig:
    return config.with_hardware(bit_depth=raw_bit_depth, bayer_pattern=raw_pattern)


def cmd_run(args: argparse.Namespace) -> int:
    config = resolve_config(args.config)
    hw = config.hardware
    is_headerless = Path(args.input).suffix.lower() == ".raw"
    raw = load_image(
        args.input,
        width=args.width or hw.width,
        height=args.height or hw.height,
        bit_depth=args.bit_depth or (hw.bit_depth if is_headerless else None),
        bayer_pattern=args.pattern or hw.bayer_pattern,
        byte_order=args.byte_order,
    )
    config = apply_raw_metadata(config, raw.bit_depth, raw.bayer_pattern)
    if raw.black_level is not None:
        r, gr, gb, b = raw.black_level
        blc = config.modules.blc.model_copy(update={"bl_r": r, "bl_gr": gr, "bl_gb": gb, "bl_b": b})
        config = config.with_module("blc", blc)

    result = Pipeline(config).execute(raw.bayer)
    output = Path(args.output) if args.output else Path(args.input).with_suffix(".png")
    save_image(result.image, output, jpeg_quality=args.quality)
    if not args.quiet:
        timings = ", ".join(f"{k}={v * 1000:.0f}ms" for k, v in result.timings.items())
        print(f"Wrote {output} ({result.elapsed:.2f}s: {timings})")
        corrected = result.stats.get("dpc_corrected")
        if isinstance(corrected, int):
            print(f"DPC corrected {corrected} pixels")
    return 0


def cmd_schema(_args: argparse.Namespace) -> int:
    print(json.dumps(IspConfig.model_json_schema(), indent=2))
    return 0


def cmd_configs(_args: argparse.Namespace) -> int:
    for name, config in bundled_configs().items():
        hw = config.hardware
        print(f"{name:14s} {hw.width}x{hw.height} {hw.bit_depth}-bit {hw.bayer_pattern.upper()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fast-openisp", description="fast-openISP software ISP")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Process a raw image to PNG/JPEG")
    run.add_argument("input", help="Input .raw, .tif/.tiff or .dng file")
    run.add_argument("-c", "--config", default="test", help="YAML file or bundled config name")
    run.add_argument("-o", "--output", help="Output .png/.jpg (default: input name + .png)")
    run.add_argument("-q", "--quality", type=int, default=95, help="JPEG quality (1-100)")
    run.add_argument("--width", type=int, help="Raw width (headerless .raw)")
    run.add_argument("--height", type=int, help="Raw height (headerless .raw)")
    run.add_argument("--bit-depth", type=int, help="Override the bit depth")
    run.add_argument("--pattern", choices=BAYER_PATTERNS, help="Override the Bayer pattern")
    run.add_argument("--byte-order", choices=("little", "big"), default="little")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=cmd_run)

    schema = sub.add_parser("schema", help="Print the JSON Schema of the YAML config")
    schema.set_defaults(func=cmd_schema)

    configs = sub.add_parser("configs", help="List bundled configurations")
    configs.set_defaults(func=cmd_configs)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, LoadError, PipelineError, ExportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

# Configuration files

A configuration describes the sensor and every ISP module. It is stored as YAML and
checked with Pydantic whenever it is loaded, from the GUI, the command line or Python.

## Structure

```yaml
hardware:
  width: 1090          # used to read headerless .raw files
  height: 1096
  bit_depth: 10        # 8–16
  bayer_pattern: bggr  # rggb | bggr | grbg | gbrg
modules:
  dpc:
    enabled: true
    diff_threshold: 30
  blc:
    enabled: true
    bl_r: 32
    bl_gr: 32
    bl_gb: 32
    bl_b: 32
    alpha: 0.0
    beta: 0.0
  awb:
    enabled: true
    mode: grey_world   # or: manual
    r_gain: 1.0
    gr_gain: 1.0
    gb_gain: 1.0
    b_gain: 1.0
  ccm:
    enabled: true
    ccm:
    - [1.0, 0.0, 0.0, 0.0]
    - [0.0, 1.0, 0.0, 0.0]
    - [0.0, 0.0, 1.0, 0.0]
  # ... one entry per module, see "ISP modules"
```

- Modules always run in the fixed pipeline order, whatever order they appear in the file.
- Modules or parameters left out get their default values.
- Gains and matrix entries are **real numbers** (for example `r_gain: 1.586`). The pipeline
  converts them to the fixed-point integers of the original openISP (×1024 or ×256)
  internally.

## Validation

Every configuration is checked when loaded:

- **Unknown keys are errors**, so a typo such as `r_gian` is reported rather than silently
  ignored.
- **Types and ranges** are enforced, for example gains 0–8, gamma > 0, bit depth 8–16.
- **Checks across fields**:
    - black levels must fit in the bit depth
    - NLM window sizes must be odd
    - EEH `flat_threshold ≤ edge_threshold`
    - FCS `delta_min < delta_max`
    - CEH tile counts 2–64
    - CCM coefficients within ±8

Error messages name the field that failed:

```text
modules.awb.r_gain: Input should be less than or equal to 8
modules.typo_module: Extra inputs are not permitted
```

**Module dependencies** (for example EEH needs CSC) are not checked on load: the GUI
switches dependent modules off for you. When running from the command line or Python,
unmet dependencies raise an error before processing starts.

## Editor autocompletion

Export the JSON Schema and point your editor at it to get validation and autocompletion
while editing YAML by hand:

```bash
uv run fast-openisp schema > config.schema.json
```

With the VS Code YAML extension, add this as the first line of the config:

```yaml
# yaml-language-server: $schema=./config.schema.json
```

## Bundled configurations

| Name | Sensor | Notes |
|---|---|---|
| `connan_raw14` | 6080 × 4044, 14-bit BGGR | Black level 32, manual white balance |
| `test` | 1920 × 1080, 10-bit RGGB | For `raw/test.RAW` |
| `nikon_d3x` | 6080 × 4044, 14-bit RGGB | Nikon D3x |
| `nikon_d3200` | 3000 × 2000, 12-bit RGGB | Nikon D3200 |

List them with `uv run fast-openisp configs`.

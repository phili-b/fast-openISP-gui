# fast-openISP GUI

**A software image signal processor (ISP) with a desktop GUI for Windows.** Load a raw Bayer
image, switch ISP blocks on and off, tune every parameter while watching a live preview,
compare before and after, and export a PNG or JPEG.

[![Documentation](https://img.shields.io/badge/docs-fiepfiep.github.io-3f51b5)](https://fiepfiep.github.io/fast-openISP-gui/)
[![Release](https://img.shields.io/github/v/release/fiepfiep/fast-openISP-gui)](https://github.com/fiepfiep/fast-openISP-gui/releases/latest)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
[![License](https://img.shields.io/badge/license-MIT-green)](#license)

![fast-openISP GUI processing a 12-bit GRBG DNG of a color checker](docs/assets/screenshot.png)

📖 **Documentation:** <https://phili-b.github.io/fast-openISP-gui/>

---

## Features

- **Raw inputs**: headerless `.raw`, single-channel Bayer `.tif`/`.tiff` (bit depth worked
  out from the data), and `.dng` (Bayer pattern, black level and white balance read from the
  file). Open them from the menu or **drag and drop** them onto the window.
- **17 ISP blocks**, from dead pixel correction to brightness/contrast. Each can be switched
  on or off; blocks that depend on another one are switched off automatically.
- **Every parameter can be edited**, with controls built from the configuration schema,
  including a 3 × 4 colour matrix editor.
- **Live preview**: processing runs in the background on a downscaled copy of the image that
  keeps its Bayer layout.
- **Before/after** comparison with a draggable split view (F2 / F3 / F4, or hold **B**).
- **Export** full-resolution PNG or JPEG, optionally with the YAML config alongside.
- **Grey-world auto white balance** for uncalibrated sensors, with *Freeze as manual*.
- **CCM calibration on a color checker**: the chart is detected automatically (or you drag
  its corners), the fit can be weighted towards neutrals or skin tones, the target illuminant
  is reached with a chromatic adaptation transform, and the ΔE2000 error is reported per patch
  before and after.
- **YAML configurations checked with Pydantic**: typos and out-of-range values are reported
  by field name. Configs in the original fast-openISP format are converted automatically.
- **Single-file `fast-openISP.exe`**: Python doesn't need to be installed. There is also a
  CLI and a typed Python API.

## Download

Get `fast-openISP-<version>-win64.zip` from the
[latest release](https://github.com/fiepfiep/fast-openISP-gui/releases/latest), unzip it and
run `fast-openISP.exe`. The zip includes sample configs and raw images:

| Sample | Format | Config |
|---|---|---|
| `raw/mikros110.tiff` | 1090 × 1096, 10-bit BGGR TIFF | `mikros110` (default) |
| `raw/test.RAW` | 1920 × 1080, 10-bit RGGB headerless raw | `test` |
| `raw/mira220_rgb.dng` | 1600 × 1400, 12-bit GRBG DNG | `mira220_rgb` |

> The exe is not code-signed, so Windows SmartScreen may warn; choose *More info → Run
> anyway*. The first start takes a few seconds while the exe unpacks itself.

See [Getting started](https://fiepfiep.github.io/fast-openISP-gui/getting-started/) for a
walkthrough.

## Run from source

Requires [uv](https://docs.astral.sh/uv/), which installs Python 3.13 and all dependencies.

```bash
git clone https://github.com/fiepfiep/fast-openISP-gui.git
cd fast-openISP-gui
uv sync
uv run fast-openisp-gui
```

Command line:

```bash
uv run fast-openisp run raw/mikros110.tiff -c mikros110 -o mikros110.png
uv run fast-openisp configs     # list bundled configs
uv run fast-openisp schema      # JSON Schema for editor autocompletion
```

Python:

```python
from fast_openisp.config import bundled_configs
from fast_openisp.io.loaders import load_tiff
from fast_openisp.pipeline import Pipeline

config = bundled_configs()["mikros110"]
raw = load_tiff("raw/mikros110.tiff", bayer_pattern="bggr")
result = Pipeline(config).execute(raw.bayer)
result.image        # (H, W, 3) uint8 RGB
result.awb_gains    # white-balance gains that were applied
result.timings      # seconds per module
```

## The ISP pipeline

```
Bayer ─► DPC ─► BLC ─► AAF ─► AWB ─► CNF ─► CFA ─► CCM ─► GAC ─► CSC ─► NLM ─► BNF ─► CEH ─► EEH ─► FCS ─► HSC ─► BCC ─► SCL
         └──────────── Bayer domain ────────────┘  └──── RGB domain ────┘  └───────────────── YCbCr domain ─────────────────┘
```

| Block | Purpose | Block | Purpose |
|---|---|---|---|
| **DPC** | Dead/hot pixel correction | **CSC** | RGB → YCbCr (BT.601) |
| **BLC** | Black level subtraction | **NLM** | Non-local means denoising |
| **AAF** | Anti-aliasing filter | **BNF** | Bilateral denoising |
| **AWB** | White balance (manual / grey world) | **CEH** | Local contrast (CLAHE) |
| **CNF** | Chroma noise filter | **EEH** | Edge enhancement |
| **CFA** | Demosaicing (Malvar / bilinear) | **FCS** | False color suppression |
| **CCM** | Color correction matrix | **HSC** | Hue / saturation |
| **GAC** | Gain + gamma curve | **BCC** | Brightness / contrast |
| | | **SCL** | Scaler |

Each block is explained with its equations in
[**ISP blocks explained**](https://fiepfiep.github.io/fast-openISP-gui/isp-blocks/). Its
parameters are listed in the
[module reference](https://fiepfiep.github.io/fast-openISP-gui/modules/).

## Performance

The ISP algorithms are fast-openISP's pure-NumPy implementations, which run **over 300 times
faster** than the original openISP. Measurements by the fast-openISP author on a
1920 × 1080 Bayer array (Ryzen 7 1700):

| | openISP | fast-openISP |
|---|---:|---:|
| NLM | 1600.95 s | 5.37 s |
| BNF | 801.24 s | 0.75 s |
| **End-to-end pipeline** | **2894.41 s** | **7.82 s** |

The GUI previews a downscaled image (longest edge 1024 px by default), so edits typically
show up within a fraction of a second. Export always runs at full resolution.

## Documentation

| | |
|---|---|
| [Getting started](https://fiepfiep.github.io/fast-openISP-gui/getting-started/) | Install, run, first image |
| [The GUI](https://fiepfiep.github.io/fast-openISP-gui/gui/) | Panels, dependencies, preview, compare, export |
| [Color calibration](https://fiepfiep.github.io/fast-openISP-gui/calibration/) | Fitting the CCM on a color checker |
| [ISP blocks explained](https://fiepfiep.github.io/fast-openISP-gui/isp-blocks/) | What each block does, with equations |
| [Input formats](https://fiepfiep.github.io/fast-openISP-gui/inputs/) | `.raw`, `.tif`, `.dng` details |
| [Configuration files](https://fiepfiep.github.io/fast-openISP-gui/configuration/) | YAML format and validation |
| [Command line](https://fiepfiep.github.io/fast-openISP-gui/cli/) | `fast-openisp run / configs / schema` |
| [Developer guide](https://fiepfiep.github.io/fast-openISP-gui/development/) | Layout, tooling, adding a module |
| [Building the exe](https://fiepfiep.github.io/fast-openISP-gui/building/) | PyInstaller build and releases |
| [Release notes](https://fiepfiep.github.io/fast-openISP-gui/release-notes/) | What's new |

## Development

```bash
uv run ruff format && uv run ruff check   # format + lint
uv run ty check                           # type check
uv run pytest tests/test_regression.py    # bit-exact regression tests
uv run mkdocs serve                       # docs at http://127.0.0.1:8000
.\scripts\build_exe.ps1                   # build dist\fast-openISP.exe (PowerShell)
```

The regression tests check that the refactored pipeline reproduces the original
fast-openISP output **bit for bit**.

## Credits

Author of the GUI, packaging, typed configuration, grey-world AWB and TIFF/DNG support:
**Philippe Baetens**.

This project builds on:

- [**fast-openISP**](https://github.com/QiuJueqin/fast-openISP) by Qiu Jueqin: the NumPy ISP
  algorithms (MIT license).
- [**openISP**](https://github.com/cruxopen/openISP): the original open-source ISP pipeline
  and its design document.

The GUI uses [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) (LGPLv3).

## License

MIT. The original fast-openISP code is Copyright 2021 Qiu Jueqin, licensed under
[MIT](http://opensource.org/licenses/MIT). The GUI and later changes are Copyright 2026
Philippe Baetens, under the same license.

# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A software ISP (image signal processor) with a **Windows desktop GUI**, built on
[fast-openISP](https://github.com/QiuJueqin/fast-openISP) by Qiu Jueqin (MIT), which is
itself a NumPy reimplementation of [openISP](https://github.com/cruxopen/openISP).

Author of the GUI/packaging work: Philippe Baetens. Docs:
<https://fiepfiep.github.io/fast-openISP-gui/>.

Remotes: `origin` and `mygithub` both point at github.com/phili-b/fast-openISP-gui (the
repository was renamed from fiepfiep), `gitlab` is the ams-OSRAM internal mirror, and
`upstream` is QiuJueqin/fast-openISP — **never push or open pull requests there**.
Branch: `master`; work is committed straight to it and pushed to `mygithub` and `gitlab`.

## Commands

```bash
uv sync                                   # set up .venv (Python 3.13)
uv run fast-openisp-gui                   # GUI
uv run fast-openisp run IMG -c CONFIG -o OUT.png
uv run ruff format && uv run ruff check   # format + lint (must pass)
uv run ty check                           # type check (must pass, whole src/)
uv run pytest tests/test_regression.py    # bit-exact regression tests
uv run mkdocs build --strict              # docs (must pass)
```

PowerShell only: `.\scripts\build_exe.ps1` (single-file exe + self-test),
`.\scripts\package_release.ps1` (release zip).

## Layout

```
src/fast_openisp/
  config.py     Pydantic config models, YAML load/save, MODULE_INFO (order + dependencies),
                legacy-format migration
  pipeline.py   Pipeline, PipelineResult, saturation values, final RGB rendering;
                run_until(bayer, module) gives the calibration the pre-CCM data
  color.py      illuminants, CAT matrices, Lab, CIEDE2000, ColorChecker reference data
  calibration.py chart geometry, patch sampling and the cv2.ccm fit (no Qt)
  imaging.py    YCbCr→RGB, CFA-preserving preview downscale, "before" rendering
  modules/      one file per ISP block; base.py has ISPModule/PipelineData/Context/to_fixed
  configs/      bundled YAML (package data)
  io/           loaders.py (.raw/.tif/.dng → RawImage), export.py (PNG/JPEG)
  cli.py, selftest.py
  gui/          app.py, main_window.py, module_panel.py, param_widgets.py, image_view.py,
                worker.py, import_dialog.py, export_dialog.py, ccm_dialog.py, resources/
tests/          test_regression.py + golden/ reference PNGs; gui/ (pytest-qt)
docs/           mkdocs-material site; hooks.py generates the module reference
scripts/        build_exe.ps1, package_release.ps1, make_icon.py, release_notes.py
fast_openisp.spec   PyInstaller (one-file, windowed, splash)
```

## Rules that matter here

- **Numerical output must not change.** Module code keeps the original integer/fixed-point
  arithmetic. `tests/golden/*.png` were produced with the pre-refactor code; any change to a
  module must keep those tests bit-identical. Convert real-valued params with
  `to_fixed(value, scale)` inside the module, never in the config.
- **Config is the single source of truth.** Pydantic models in `config.py` drive the GUI
  controls (`param_widgets.create_editor`), the docs tables (`docs/hooks.py`) and the JSON
  schema. Add a parameter there, not in the GUI.
- **Module dependencies** live in `MODULE_INFO[...].requires`; the GUI resolves them with
  `resolve_enabled()`. Never hard-code them in the GUI.
- **Bundled configs are user-tunable.** Tests must not depend on their parameter values
  (`mikros110` in particular); build the config explicitly in the test instead.
- **Image size comes from the data**, not from `hardware.width/height` (those are only for
  headerless `.raw`), so one config works for preview and full resolution.
- **GUI threading**: previews run on a single-thread `QThreadPool`; a newer request cancels
  the running one (checked between modules), stale results are dropped by generation counter.
- **OpenCV does the colour maths** where it has an API (`cv2.ccm` for the fit, `cv2.mcc` for
  chart detection, `cv2.cvtColor`/`cv2.transform`, `cv2.getPerspectiveTransform`). Only the
  CAT matrices and CIEDE2000 are hand-written, in `color.py`. Note OpenCV 5.0's weighted
  `CCM_AFFINE` path crashes, so weights are applied by repeating patches.
- Per-run measurements (AWB gains, DPC pixel count) go through `PipelineData.extras` ->
  `PipelineResult.stats` -> `ModulePanel.set_stats`, never through the config.
- Adding a module: params model → `MODULE_INFO` → `ModulesConfig` field → `modules/xxx.py`
  (`ISPModule[XxxParams]`, `name`, `execute`) → register in `modules/__init__.py`. Keep
  pipeline order consistent everywhere.

## Gotchas

- Bash heredocs with multiple files fail in this environment; use the Write tool.
- Windows console is cp1252: avoid `×`, `≥` etc. in printed CLI output (docs are fine).
- The GUI test suite **hangs when all GUI tests run together** (they pass individually), so
  CI runs only `tests/test_regression.py`.
- A windowed exe has no console: `--self-test` also writes
  `%TEMP%\fast-openisp-self-test.log`.
- PowerShell 5.1: `$ErrorActionPreference = "Stop"` turns native-tool stderr into failures;
  the build scripts use `Continue` plus exit-code checks.
- Offscreen Qt screenshots have no fonts — useless for docs.
- `.gitignore` excludes `/site/`, `/dist/`, `/build/`, `/output/`.

## Releases

Add a `## vX.Y.Z` section to `docs/release-notes.md`, bump `__version__`
(`src/fast_openisp/__init__.py`) and `version` in `pyproject.toml`, then push a tag
(`git tag vX.Y.Z && git push mygithub vX.Y.Z`). `.github/workflows/release.yml` builds the
exe, packages the zip (exe + configs + sample raws + README.txt), and publishes the release
with those notes and a SHA-256. `ci.yml` runs lint/type/tests/docs/exe on pushes;
`docs.yml` publishes GitHub Pages.

## Reference data

| File | Format |
|---|---|
| `raw/mikros110.tiff` | 1090 × 1096, 10-bit in 16-bit TIFF, BGGR, black level 32 |
| `raw/test.RAW` | 1920 × 1080, 10-bit headerless, RGGB |
| `raw/mira220_rgb.dng` | 1600 × 1400, 12-bit DNG, GRBG, black level 156 |
| `raw/color_checker.pgm` | PGM — **not a supported input** |

`raw/mira220_rgb.dng` is a ColorChecker shot: use it to test the CCM calibration.

## Open items

- No `LICENSE` file yet (README states MIT); the exe bundles LGPL components (PySide6/Qt,
  LibRaw via rawpy) that need license texts in the release. Employer (ams OSRAM) copyright
  question is unresolved — ask before changing license headers.
- `spec.md` is the original design spec and is partly outdated (no `gui/state.py`;
  `imaging.py`/`selftest.py` were added).
- The CLI does not apply a DNG's as-shot white balance (the GUI does, via the Config menu).

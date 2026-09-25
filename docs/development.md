# Developer guide

## Setup

```bash
uv sync
```

This creates `.venv` with Python 3.13, the runtime dependencies and the `dev` and `docs`
dependency groups.

## Project layout

```text
src/fast_openisp/
├── config.py          # Pydantic config models, YAML load/save, module info and dependencies
├── pipeline.py        # Pipeline, PipelineResult, saturation values, output rendering
├── imaging.py         # YCbCr→RGB, Bayer-preserving preview downscale, "before" rendering
├── modules/           # one file per ISP module + base.py (ISPModule, PipelineData) + helpers.py
├── configs/           # bundled YAML configs (package data)
├── io/
│   ├── loaders.py     # .raw / .tif / .dng → RawImage
│   └── export.py      # PNG / JPEG writers
├── cli.py             # fast-openisp command
├── selftest.py        # smoke test used by `fast-openISP.exe --self-test`
└── gui/
    ├── app.py         # entry point (fast-openisp-gui)
    ├── main_window.py # window, menus, drag & drop, import/export flow
    ├── module_panel.py# sensor box + module list (enable, dependencies, params)
    ├── param_widgets.py # editor controls built from the Pydantic field definitions
    ├── image_view.py  # zoom/pan view with processed / before / split modes
    ├── worker.py      # background pipeline runs (latest preview wins, cancellable export)
    ├── import_dialog.py, export_dialog.py
    └── resources/     # icon.png, icon.ico
tests/                 # pytest (+ pytest-qt for the GUI), golden reference images
docs/                  # this documentation (mkdocs-material)
scripts/               # icon generator, exe build script
```

## Common tasks

| Task | Command |
|---|---|
| Run the GUI | `uv run fast-openisp-gui` |
| Format | `uv run ruff format` |
| Lint | `uv run ruff check` (add `--fix` for automatic fixes) |
| Type check | `uv run ty check` |
| Tests | `uv run pytest` |
| Docs preview | `uv run mkdocs serve` |
| Docs build | `uv run mkdocs build --strict` |
| Windows exe | see [Building the Windows exe](building.md) |

## Continuous integration and GitHub Pages

Two GitHub Actions workflows live in `.github/workflows/`:

| Workflow | Runs on | Does |
|---|---|---|
| `ci.yml` | pushes to `master`, pull requests | ruff, ty, regression tests, `mkdocs build --strict`, builds the exe and runs its self-test, uploads `fast-openISP.exe` as a build artifact |
| `docs.yml` | pushes to `master`, manual | builds this site and publishes it to **GitHub Pages** |

To turn on GitHub Pages (one-time setup):

1. Push to the GitHub remote (`git push mygithub master`).
2. In the repository, go to **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Push to `master`, or run the *docs* workflow by hand from the **Actions** tab.
4. The site is published at <https://phili-b.github.io/fast-openISP-gui/> (`site_url` in
   `mkdocs.yml`).

## Tests

- `tests/test_regression.py` checks that the pipeline still produces **bit-identical**
  output. The reference images in `tests/golden/` were generated with the original
  fast-openISP code before the refactor, covering: the default `test` config, a variant
  that changes almost every parameter (including bilinear demosaicing), an RGB-only
  pipeline with scaling, and `mikros110.tiff` with manual white balance.
- `tests/gui/` drives the main window offscreen (`QT_QPA_PLATFORM=offscreen`) with
  pytest-qt: opening and dropping files, module dependencies, parameter edits, grey-world
  freeze, view modes, export, and error handling.

Set `FAST_OPENISP_SCREENSHOTS=<folder>` to save window screenshots during the GUI tests.

## Adding a module

1. Add a `XxxParams(ModuleParams)` model in `config.py` with `Field(...)` bounds and
   descriptions. The GUI controls and the docs are generated from it.
2. Add an entry to `MODULE_INFO` (in pipeline order) with its full name and `requires`.
3. Add the field to `ModulesConfig`, in the same position.
4. Create `modules/xxx.py` with `class XXX(ISPModule[XxxParams])`, set `name = "xxx"`, and
   implement `execute(data)` so it modifies `PipelineData` in place.
5. Register the class in `modules/__init__.py`.

Parameters stored as fixed point should be real numbers in the config; convert them with
`to_fixed(value, scale)` in the module.

## Design notes

- **Numerical parity**: module code keeps the integer arithmetic of the original, so
  refactoring cannot change results without a regression test failing.
- **Sensor size comes from the image**: modules read the size from the array, so the same
  config works on full-resolution and preview data. `hardware.width/height` are only used
  to read `.raw` files.
- **Preview downscaling** averages blocks within each color channel. The channel means stay
  the same, so grey-world gains estimated on the preview match the full-resolution export.
- **Threads**: the GUI runs previews on a single-thread pool. A new request cancels the
  running one (checked between modules), and results from older requests are discarded.

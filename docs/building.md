# Building the Windows exe

The GUI is packaged with [PyInstaller](https://pyinstaller.org) as a **single-file**
`fast-openISP.exe` that runs without Python installed.

## Build

In PowerShell, from the repository root:

```powershell
.\scripts\build_exe.ps1
```

The script:

1. syncs the environment (`uv sync`),
2. runs `uv run pyinstaller fast_openisp.spec --noconfirm --clean`,
3. runs the smoke test `dist\fast-openISP.exe --self-test`, which processes synthetic
   connan-sized data at preview and full size and writes a PNG to `%TEMP%`.

The result is `dist\fast-openISP.exe`.

## What the spec file does

`fast_openisp.spec`:

- **One file, windowed**: no console window. The app icon is `gui/resources/icon.ico` and
  the version resource comes from the package version.
- **Data files**: bundles `fast_openisp/configs/*.yaml` and `gui/resources/*`. They are
  read with `importlib.resources`, which works both from source and from the exe.
- **Smaller size**: leaves out Qt modules the app doesn't use (QML/Quick, WebEngine,
  Multimedia, 3D, PDF) and unused translations. UPX compression is off because it can
  corrupt Qt DLLs and triggers antivirus false positives.
- **Splash screen**: shown while the exe unpacks to `%TEMP%`, and closed once the main
  window is visible (`pyi_splash.close()`).

## Troubleshooting

| Problem | Fix |
|---|---|
| Exe starts slowly | Expected for a one-file build (it unpacks on every start). The splash screen covers it. |
| SmartScreen / Defender warning | The exe is unsigned. Code signing (`signtool`) can be added to the build script. |
| `ModuleNotFoundError` at startup | Add the module to `hiddenimports` in `fast_openisp.spec`. |
| Missing Qt plugin (for example image formats) | Remove it from the exclusion list in the spec. |
| Check the exe works | `dist\fast-openISP.exe --self-test`; exit code 0 means it passed. |

## Releases

A GitHub release is created automatically when a version tag is pushed:

```bash
git tag v0.2.0
git push mygithub v0.2.0
```

The `release` workflow (`.github/workflows/release.yml`) builds the exe on Windows, runs
`scripts/package_release.ps1`, and attaches `fast-openISP-<version>-win64.zip` to the
release. The zip contains:

| Path | Content |
|---|---|
| `fast-openISP.exe` | The application |
| `README.txt` | Quick start |
| `configs/*.yaml` | The bundled configurations |
| `raw/connan_raw14.raw`, `raw/test.RAW`, `raw/mira220_rgb.dng` | Sample images |

To build the same zip locally, run `.\scripts\build_exe.ps1` and then
`.\scripts\package_release.ps1`; the zip is written to `dist\`. Update `__version__` in
`src/fast_openisp/__init__.py` (and `version` in `pyproject.toml`) before tagging a new
version.

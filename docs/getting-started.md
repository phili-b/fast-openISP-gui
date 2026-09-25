# Getting started

## Option 1: the Windows executable

1. Download `fast-openISP.exe`.
2. Double-click it. Python does not need to be installed.
3. Drag a `.raw`, `.tif` or `.dng` file onto the window.

!!! note "First launch"
    The exe is a single file that unpacks itself to a temporary folder each time it starts,
    so startup takes a few seconds. A splash screen is shown meanwhile.

!!! warning "Windows SmartScreen"
    The exe is not code-signed, so Windows Defender SmartScreen may warn about an
    "unrecognized app". Choose **More info → Run anyway** if you trust the source.

You can also open a file directly by dragging it onto the exe icon, or from a terminal:

```bash
fast-openISP.exe raw\mikros110.tiff
```

## Option 2: run from source with uv

You need [uv](https://docs.astral.sh/uv/). uv installs Python 3.13 and all dependencies
for you.

```bash
git clone https://github.com/phili-b/fast-openISP-gui.git
```

```bash
cd fast-openISP-gui
```

```bash
uv sync
```

Start the GUI:

```bash
uv run fast-openisp-gui
```

Or process a file from the command line:

```bash
uv run fast-openisp run raw/mikros110.tiff -c mikros110 -o mikros110.png
```

## Your first image

1. Start the GUI. The `mikros110` configuration is active by default.
2. Drag `raw/mikros110.tiff` onto the window.
3. The import dialog shows **1090 × 1096**, **10 bit** (worked out from the pixel values)
   and **BGGR**. Click **OK**.
4. The processed preview appears after a moment. White balance uses the grey-world estimate.
5. Press **F4** for the split view and drag the divider to compare with the unprocessed
   input.
6. Click **Export…** in the status bar to save a full-resolution PNG or JPEG.

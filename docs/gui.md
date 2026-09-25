# The GUI

![The main window with a 12-bit GRBG DNG loaded](assets/screenshot.png)

The window has three areas:

- **ISP modules** (left): sensor settings, then every ISP module in pipeline order.
- **Image view** (centre): the processed image, the unprocessed input, or both side by side.
- **Status bar** (bottom): pixel under the cursor, processing progress, image information,
  processing time, zoom level and the **Export…** button.

## Opening images

- Drag and drop a file onto the window, use **File → Open image…** (++ctrl+o++), or pick
  one from **File → Open recent**.
- Supported: `.raw` (headerless), `.tif`/`.tiff` (single-channel Bayer) and `.dng`. See
  [Input formats](inputs.md).
- Dropping a `.yaml` file loads it as the active configuration.
- If you drop several files, only the first supported one is opened.

For `.raw` and `.tif` files an **import dialog** asks you to confirm the sensor layout:

| Field | `.raw` | `.tif` |
|---|---|---|
| Width / height | Editable, pre-filled from the active config | Read from the file |
| Bit depth | Pre-filled from the active config | Worked out from the pixel values |
| Bayer pattern | Pre-filled from the active config | Pre-filled from the active config |
| Byte order | Editable | – |

For `.raw` files the dialog checks that width × height × 2 bytes matches the file size, and
lists common resolutions that fit. Tick **Don't ask again…** to reuse the answers for files
of the same size. **File → Forget remembered import settings** clears them.

DNG files need no dialog: size, Bayer pattern, bit depth and black levels come from the file.
When **Config → Use as-shot white balance for DNG** is on, the camera's white balance is
copied into AWB as manual gains.

## Sensor settings

The **Sensor** box at the top of the panel shows the image size and lets you change the
**bit depth** and **Bayer pattern** at any time, for example when the colors come out
swapped because the pattern was wrong.

## Turning modules on and off

Each module row has a checkbox. Unticking it removes the module from the pipeline and the
preview updates.

Some modules need another module to run first. When you turn off a module, every module
that depends on it is switched off and greyed out. Hover over the checkbox to see why, for
example *Requires CSC (disabled)*. When you turn the module back on, the dependent modules
go back to how you had them.

| Module | Requires |
|---|---|
| CCM, GAC, SCL | CFA |
| CSC | GAC |
| NLM, BNF, CEH, EEH, HSC, BCC | CSC |
| FCS | CSC and EEH |

With CFA off, the Bayer data is shown as a greyscale image.

## Editing parameters

Click the arrow on a module row to show its parameters, or use **Expand all**. Each
parameter has a control that matches its type (number box, drop-down list, checkbox or
matrix grid) and is limited to the valid range. Hover over a label for a description.

- Every change updates the preview after a short pause (250 ms).
- A value that fails a check spanning several fields (for example FCS `delta_min ≥
  delta_max`) is shown in red below the parameters and is not applied.
- The **↺** button resets one module to the loaded configuration.
- The title bar shows `*` when the configuration has unsaved changes.

### White balance (AWB)

- **manual**: the R, Gr, Gb and B gains are applied as entered.
- **grey world**: the gains are estimated from each image so that the average red and blue
  match the average green. Nearly saturated pixels are ignored. The estimated gains are shown
  below the parameters. **Freeze as manual** copies them into manual mode, which is useful
  once you have a good reference image.

The chroma noise filter (CNF) automatically uses the gains that AWB applied.

### Color correction matrix (CCM)

A 3 × 4 grid: each row is an output channel, the first three columns are the input R, G, B
weights and the last column is an offset in code values. The **Σ** column shows each row's sum,
in orange when it is not 1.0 (a row sum of 1.0 keeps neutral greys neutral). **Reset to
identity** restores the identity matrix.

**Calibrate on color checker…** measures the matrix from a photograph of a ColorChecker chart:
the chart is detected (or you drag its four corners), you choose what the fit should optimise
for, and the dialog reports the ΔE2000 error before and after. See
[Color calibration](calibration.md).

![The colour checker calibration dialog](assets/calibration-dialog.png)

### Dead pixel correction (DPC)

When DPC is expanded it reports how many pixels the last run corrected, for example
*Corrected 1,284 pixels (0.110 %) · preview 1:2*. The count is measured on the data that was
processed, so it refers to the downscaled preview unless **View → Full-resolution preview** is
on. Lower `diff_threshold` to catch more defects and watch the count rise.

## Preview and performance

For speed, the preview is processed at reduced size. The image is shrunk by a whole-number
factor so its longest edge is at most **1024 px** (change this in **View → Preview size…**).
Shrinking keeps the Bayer layout, so every module runs on real mosaic data. The status bar
shows the factor, for example *preview 1:2*.

!!! note "Spatial filters at preview scale"
    Filters that work on a pixel neighbourhood (NLM, BNF, EEH, DPC, AAF) cover a larger area
    of the scene at preview scale, so their effect looks stronger in the preview than in the
    export. Turn on **View → Full-resolution preview** to see the exact result (slower).

Processing runs in the background, so the window stays responsive. If you change something
while a run is in progress, that run is cancelled and a new one starts.

## Before/after comparison

| Mode | Shortcut | Shows |
|---|---|---|
| Processed | ++f2++ | The ISP output |
| Before | ++f3++ | The unprocessed input |
| Split | ++f4++ | Before on the left, processed on the right; drag the white divider |

Hold ++b++ to show the *Before* image temporarily in any mode.

*Before* is a quick rendering of the input without ISP processing: linear scaling to 8 bit,
simple demosaicing and a display gamma of 1/2.2. There is no black level, white balance or
color correction.

## Navigating the image

- Mouse wheel: zoom around the cursor.
- Drag: pan.
- ++ctrl+0++ fit to window, ++ctrl+1++ 100 %, ++ctrl+plus++ / ++ctrl+minus++ zoom.
- The status bar shows the pixel position (in full-resolution coordinates) and the
  processed RGB value under the cursor.

## Configurations

| Action | Menu |
|---|---|
| Load a bundled configuration | **Config → Bundled configs** |
| Load a YAML file | **Config → Load YAML…** (++ctrl+l++) or drop it on the window |
| Save | **Config → Save YAML** (++ctrl+s++), **Save YAML as…** (++ctrl+shift+s++) |
| Undo all edits | **Config → Revert to loaded config** |

Loading a configuration while an image is open keeps the image's size, bit depth and Bayer
pattern. Invalid YAML files are rejected with a message naming the wrong field, for example
`modules.awb.r_gain: Input should be less than or equal to 8`.

The app remembers the last configuration, recent files, folders and window layout between
sessions.

## Exporting

Click **Export…** in the status bar, or use **File → Export…** (++ctrl+e++):

- **Format**: PNG or JPEG. The file extension follows the format.
- **JPEG quality**: 50–100 (default 95). JPEGs are saved without chroma subsampling (4:4:4).
- **Save the configuration alongside**: also writes `<name>.yaml` next to the image, so you
  can reproduce the result later.

The image is processed again at **full resolution** before saving. A progress dialog shows
the current module and can be cancelled.

# Input formats

All inputs are loaded as a single-channel Bayer mosaic of 16-bit integers with the data in
the low bits. Odd image sizes are cropped by one row or column, because the pipeline works
on 2 × 2 Bayer cells.

## Headerless raw (`.raw`)

Plain 16-bit pixels with no header. The width, height, bit depth, Bayer pattern and byte
order cannot be read from the file, so they come from the active configuration and are
confirmed in the import dialog. The file size must equal width × height × 2 bytes.

Example: `raw/test.RAW` is 1920 × 1080, 10-bit, RGGB (the bundled `test` config).

## Bayer TIFF (`.tif`, `.tiff`)

A single-page, single-channel, integer TIFF holding the Bayer mosaic.

- **Bit depth** is worked out from the pixel values, not from the TIFF header. Sensors often
  store 10- or 12-bit data in a 16-bit TIFF. A TIFF may say 16 bits
  per sample while its values only go up to 1023, so it is treated as 10-bit.
- **High-bit data**: if every value is a multiple of 4 or more (the data sits in the high
  bits), it is shifted down automatically.
- The **Bayer pattern** is not stored in a plain TIFF; it comes from the active
  configuration and is confirmed in the import dialog.
- Color (RGB) TIFFs, TIFFs with several images, and floating-point TIFFs are rejected with
  an explanation.

## DNG (`.dng`)

DNG files are read with [rawpy](https://github.com/letmaik/rawpy) (LibRaw). The following
are taken from the file:

| Metadata | Used for |
|---|---|
| Bayer (CFA) pattern | Sensor Bayer pattern |
| White level | Bit depth |
| Black level for each channel | BLC `bl_r`, `bl_gr`, `bl_gb`, `bl_b` |
| As-shot white balance | AWB manual gains (optional, see **Config** menu) |

DNGs that are already demosaiced, and X-Trans or other non-Bayer sensors, are not supported.

## Reference image: `connan_raw14.raw`

| Property | Value |
|---|---|
| Size | 6080 × 4044 |
| Data | 14-bit in a 16-bit container, headerless |
| Bayer pattern | BGGR |
| Black level | 32 DN |
| Configuration | `connan_raw14` (manual white balance) |

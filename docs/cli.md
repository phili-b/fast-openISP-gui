# Command line

The `fast-openisp` command processes images without the GUI. It does not load Qt, so it
starts quickly and works on headless machines.

## Process an image

```bash
uv run fast-openisp run raw/connan_raw14.raw -c connan_raw14 -o connan_raw14.png
```

| Option | Meaning |
|---|---|
| `input` | `.raw`, `.tif`/`.tiff` or `.dng` file |
| `-c`, `--config` | YAML file, or the name of a bundled config (default `test`) |
| `-o`, `--output` | Output `.png` or `.jpg` (default: input name with `.png`) |
| `-q`, `--quality` | JPEG quality 1–100 (default 95) |
| `--width`, `--height` | Size of a headerless `.raw` (default from the config) |
| `--bit-depth` | Override the bit depth (TIFF default: worked out from the data) |
| `--pattern` | Override the Bayer pattern |
| `--byte-order` | `little` (default) or `big`, for `.raw` |
| `--quiet` | Don't print timings |

It prints the time spent in each module:

```text
Wrote connan_raw14.png (4.14s: dpc=160ms, blc=15ms, ..., nlm=2937ms, ...)
```

## Other commands

```bash
uv run fast-openisp configs
```

lists the bundled configurations.

```bash
uv run fast-openisp schema
```

prints the JSON Schema of the configuration format.

## Exit codes

`0` on success, `1` when the configuration, input file, processing or export fails. The
reason is printed to stderr.

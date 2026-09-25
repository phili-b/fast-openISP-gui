# Package dist\fast-openISP.exe with the bundled configs and sample raw images into a zip.
# Usage (after build_exe.ps1):  .\scripts\package_release.ps1
# Output: dist\fast-openISP-<version>-win64.zip
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

$init = Get-Content "src\fast_openisp\__init__.py" -Raw
if ($init -notmatch '__version__ = "([^"]+)"') { throw "Version not found" }
$version = $Matches[1]
$name = "fast-openISP-$version-win64"

$exe = "dist\fast-openISP.exe"
if (-not (Test-Path $exe)) { throw "Build the exe first: .\scripts\build_exe.ps1" }

$stage = Join-Path "dist" $name
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force "$stage\configs", "$stage\raw" | Out-Null

Copy-Item $exe $stage
Copy-Item "src\fast_openisp\configs\*.yaml" "$stage\configs"
# Sample images in supported formats
Copy-Item "raw\mikros110.tiff", "raw\test.RAW", "raw\mira220_rgb.dng" "$stage\raw"

$readme = @"
fast-openISP $version
=====================

Software image signal processor with a desktop GUI.
Author: Philippe Baetens
Based on fast-openISP by Qiu Jueqin (MIT license) and openISP.

Documentation: https://phili-b.github.io/fast-openISP-gui/
Source:        https://github.com/phili-b/fast-openISP-gui

Quick start
-----------
1. Run fast-openISP.exe (no Python needed). The first start takes a few seconds.
2. Drag raw\mikros110.tiff onto the window and confirm the import dialog
   (1090 x 1096, 10 bit, BGGR). The bundled 'mikros110' config is active by default.
3. For raw\test.RAW, first choose Config > Bundled configs > test
   (1920 x 1080, 10 bit, RGGB).
4. Drop raw\mira220_rgb.dng: size, pattern, black level and white balance
   come from the DNG itself (no dialog).
5. Toggle modules and edit parameters in the left panel; press F4 for the
   before/after split view; click Export... to save a full-resolution PNG/JPEG.

Contents
--------
fast-openISP.exe   the application
configs\           YAML configurations (also built into the exe; load via Config > Load YAML)
raw\               sample images: mikros110.tiff (10-bit BGGR), test.RAW (10-bit RGGB),
                   mira220_rgb.dng (12-bit GRBG DNG)

Windows SmartScreen may warn because the exe is not code-signed:
choose "More info" > "Run anyway".
"@
Set-Content -Path "$stage\README.txt" -Value $readme -Encoding utf8

$zip = "dist\$name.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
$sizeMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "==> OK: $zip ($sizeMb MB)"

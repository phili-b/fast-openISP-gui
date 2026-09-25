# ISP blocks explained

This page explains **what each ISP block does and why**, with the equations this project
implements. The structure and background follow the design document of the original
[openISP](https://github.com/cruxopen/openISP) project
([*Image Signal Processor*, PDF](https://github.com/cruxopen/openISP/blob/master/docs/Image%20Signal%20Processor.pdf)).
The formulas describe the [fast-openISP](https://github.com/QiuJueqin/fast-openISP)
implementation used here. Places where it differs from openISP are marked
**Differs from openISP**.

For parameter names, ranges and defaults, see the [module reference](modules.md).

## What an ISP does

An image sensor does not produce a color image. Every photosite sits behind a single color
filter, so it measures only red, green *or* blue, and its output also includes an electrical
offset, noise and defects. The **image signal processor** turns this raw mosaic into an image
for display in three stages:

1. **Bayer domain**: clean up the mosaic (defects, black level, aliasing, white balance, chroma
   noise).
2. **RGB domain**: reconstruct full color (demosaicing), correct the colors to a standard color
   space, and apply the display gamma.
3. **YCbCr domain**: separate brightness (Y) from color (Cb, Cr) so each can be filtered on its
   own: denoising, contrast, sharpening, false-color suppression, hue, saturation and
   brightness.

```
 Bayer domain                         RGB domain           YCbCr domain
┌─────┬─────┬─────┬─────┬─────┐    ┌─────┬─────┬─────┐    ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
│ DPC │ BLC │ AAF │ AWB │ CNF │ ─► │ CFA │ CCM │ GAC │ ─► │ CSC │ NLM │ BNF │ CEH │ EEH │ FCS │ HSC │ BCC │ ─► SCL
└─────┴─────┴─────┴─────┴─────┘    └─────┴─────┴─────┘    └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘
```

!!! note "Notation"
    $p$ is a raw (Bayer) pixel value. $R, G_r, G_b, B$ are the four Bayer channels (the two
    greens sit on red rows and blue rows respectively). Bayer-domain filters work on
    **same-color neighbours**, which are 2 pixels apart in the mosaic. $\operatorname{clip}(x,
    a, b)$ limits $x$ to $[a, b]$. $\text{hdr}$ is the largest pixel value after black level
    correction; $\text{sdr} = 255$.

!!! info "Fixed-point arithmetic"
    Like a hardware ISP, the modules compute with integers: a gain of 1.5 is applied as
    $(1536 \cdot p) \gg 10$. The configuration uses real numbers and converts them
    internally, so the equations below are written in real-number form.

---

## Bayer domain

### DPC — Dead Pixel Correction

**Why.** Manufacturing faults (dust, crystal defects) leave some photosites *dead* (always
dark), *hot* (always bright) or *stuck* (fixed in between). Some defects are **static**, so
their positions can be measured once from dark or white frames and stored. Others are
**dynamic**, depending on exposure or temperature, and must be detected in every image.

**Detection.** For a pixel $p_0$ and its 8 same-color neighbours $p_1 \dots p_8$ in a 3 × 3
same-channel window, $p_0$ is defective when it differs from *all* of them by more than a
threshold $t$:

$$
\text{defect} \iff |p_0 - p_i| > t \quad \forall i \in \{1,\dots,8\}
$$

A real edge or texture always has at least one similar neighbour, so it isn't flagged.

**Correction (gradient-based).** Compute the second-order gradient in four directions
(vertical, horizontal and the two diagonals), for example

$$
d_v = |2p_0 - p_\uparrow - p_\downarrow|, \qquad d_h = |2p_0 - p_\leftarrow - p_\rightarrow|
$$

and replace $p_0$ by the mean of the two neighbours in the direction with the **smallest**
gradient. This interpolates along edges rather than across them. openISP also describes a
plain mean filter; the gradient method preserves edges better.

| Parameter | Effect |
|---|---|
| `diff_threshold` | Lower catches more defects, but risks smoothing fine texture |

The GUI shows how many pixels were corrected in the last run when the DPC module is expanded.

### BLC — Black Level Compensation

**Why.** Even with no light, zero exposure time and minimum gain, the sensor's readout circuit
outputs a small positive voltage: the **black level**, also called optical black. If it is not
removed, blacks look grey and every later multiplicative step (white balance, color matrix)
tints the shadows.

**How.** Subtract a per-channel offset. Optional crosstalk terms $\alpha$ and $\beta$ correct
leakage from red into the neighbouring green (and blue into green):

$$
\begin{aligned}
R' &= \max(R - bl_r,\ 0) \\
B' &= \max(B - bl_b,\ 0) \\
G_r' &= G_r - bl_{gr} + \alpha R' \\
G_b' &= G_b - bl_{gb} + \beta B'
\end{aligned}
$$

The black level can be measured by capturing a frame with the lens covered and averaging
each channel. For `mikros110` it is 32 DN.

### LSC — Lens Shading Correction (openISP only)

Light reaching the sensor edges arrives at a steeper angle, so the corners are darker
(**luma shading**). Because refraction depends on wavelength, the color can drift between the
centre and the corners (**color shading**). openISP describes the correction: capture a flat,
evenly lit field, store a gain for each corner of an $N \times M$ grid of blocks, and
interpolate a gain for every pixel from its block's four corners.

**This block is not implemented** in fast-openISP or in this GUI.

### AAF — Anti-Aliasing Filter

**Why.** Fine detail near the sensor's sampling limit folds into false patterns (aliasing,
moiré), which demosaicing then turns into colored artifacts. A mild low-pass filter before
demosaicing reduces this.

**How.** A 3 × 3 same-channel filter that weights the centre strongly:

$$
p' = \frac{1}{16}
\begin{bmatrix} 1 & 1 & 1 \\ 1 & 8 & 1 \\ 1 & 1 & 1 \end{bmatrix} * p
$$

No parameters.

### AWB — Auto White Balance gain

**Why.** A white object reflects the color of the light that falls on it. Candlelight is
around 1 500 K, household tungsten 2 500–3 500 K, daylight 5 000–6 500 K and overcast sky or
shade 6 500–10 000 K. Our eyes adapt to this; a sensor does not, so an uncorrected image has a
color cast. The ISP hardware only **multiplies each Bayer channel by a gain**. Estimating
those gains (the color temperature) is left to firmware or software, based on image
statistics.

**How.**

$$
R' = g_r R, \quad G_r' = g_{gr} G_r, \quad G_b' = g_{gb} G_b, \quad B' = g_b B
\qquad (\text{clipped to hdr})
$$

**Estimating the gains.** In `manual` mode the gains come from the configuration (for
example a calibration under known light). In `grey_world` mode this project estimates them
from each image, assuming the scene averages to neutral grey:

$$
g_r = \frac{\bar G}{\bar R}, \qquad g_b = \frac{\bar G}{\bar B}, \qquad g_{gr} = g_{gb} = 1,
\qquad \bar G = \tfrac{1}{2}(\bar G_r + \bar G_b)
$$

The averages are taken after black level correction, ignoring pixels within 2 % of
saturation (clipped highlights have lost their color). openISP also mentions a global
**digital gain** that keeps brightness constant; here that role is taken by the GAC `gain`.

### CNF — Chroma Noise Filter

**Why.** In dark areas, red and blue pixels are noisy, and white balance **amplifies** that
noise when it multiplies them by gains greater than 1. The result is colored speckles.

**Detection.** Using 5 × 5 same-channel means $\bar R, \bar G, \bar B$ and a threshold $t$, a
red pixel is treated as noise when it stands out from both the green and blue neighbourhoods,
while its own neighbourhood is red-dominant but not above blue by more than $t$:

$$
R - \bar G > t,\quad R - \bar B > t,\quad \bar R - \bar G > t,\quad \bar R - \bar B < t
$$

Blue is handled the same way.

**Correction.** Pull the pixel towards the brighter of the other two means. How hard it is
pulled depends on the white-balance gain, since stronger gain means more amplified noise:

$$
m = \max(\bar G, \bar B), \qquad R_c = m + k\,(R - m), \qquad
k = \begin{cases} 1 & g \le 1 \\ 0.5 & g \le 1.2 \\ 0.3 & \text{otherwise} \end{cases}
$$

The corrected value is blended with the original using a fade factor that is 1 in dark,
low-chroma areas and falls to 0 in bright ones. It is the product of a lookup on the local
luma $Y = 0.299\bar R + 0.587\bar G + 0.114\bar B$ and a lookup on $\bar R$:

$$
R' = f\,R_c + (1 - f)\,R
$$

In this project $g$ is the gain that AWB actually applied, so CNF follows grey-world
estimates automatically.

### CFA — Demosaicing

**Why.** Each photosite measures one color, but every output pixel needs three. The **Bayer
pattern** samples green on a checkerboard (half of all pixels) and red and blue on
quarter-density grids. Green gets more samples because human vision is most sensitive in the
middle of the spectrum. The two missing colors at each pixel must be interpolated.

**Bilinear.** Average the nearest same-color neighbours, for example green at a red site:

$$
\hat G = \tfrac{1}{4}\left(G_{\uparrow} + G_{\downarrow} + G_{\leftarrow} + G_{\rightarrow}\right)
$$

This is simple, but it ignores how strongly the three channels correlate, so it produces
zipper patterns and colored fringes on edges.

**Malvar-He-Cutler (default).** A gradient-corrected bilinear filter: add a fraction of the
local **Laplacian of the known channel**, which estimates how brightness changes across the
edge. For green at a red site:

$$
\hat G = \hat G_{\text{bilinear}} + \alpha\,\Delta_R, \qquad
\Delta_R = R_0 - \tfrac{1}{4}\left(R_{-2,0} + R_{2,0} + R_{0,-2} + R_{0,2}\right)
$$

Red at green sites and red at blue sites use the same idea with 9-point and 5-point regions.
The gains are optimised in the Wiener (minimum mean-square error) sense and rounded to
hardware-friendly powers of two: $\alpha = 1/2$, $\beta = 5/8$, $\gamma = 3/4$. The result is
the familiar 5 × 5 integer kernels with a divisor of 8. The algorithm is cheap, linear, and
used widely in ISPs (MATLAB's `demosaic` uses it too).

---

## RGB domain

### CCM — Color Correction Matrix

**Why.** The sensor's color filters don't match the color-matching functions of the eye or
of any display standard. Optics, IR-cut filters and the light source shift the colors
further. A CCM maps **sensor RGB** into a standard color space such as sRGB. Each light
source (color temperature) ideally has its own matrix.

**How.** A 3 × 3 matrix plus an offset column:

$$
\begin{bmatrix} R' \\ G' \\ B' \end{bmatrix} =
\begin{bmatrix}
c_{rr} & c_{rg} & c_{rb} \\ c_{gr} & c_{gg} & c_{gb} \\ c_{br} & c_{bg} & c_{bb}
\end{bmatrix}
\begin{bmatrix} R \\ G \\ B \end{bmatrix} +
\begin{bmatrix} o_r \\ o_g \\ o_b \end{bmatrix}
$$

If each row sums to 1, neutral greys stay neutral. Off-diagonal entries are usually negative:
they subtract the crosstalk between channels and increase saturation. A CCM is normally
fitted by least squares on a color checker photographed after white balance, which the GUI can
do for you - see [Color calibration](calibration.md). For an uncalibrated sensor, such as
`mikros110`, start from the identity matrix.

openISP also mentions **3D lookup tables** (for example 33 × 33 × 33 with trilinear
interpolation) as a more flexible alternative. They are not implemented here.

### GAC — Gamma Correction

**Why.** The sensor responds linearly to light, but human perception of brightness is roughly
logarithmic, and displays expect gamma-encoded signals. Gamma compression gives more code
values to dark tones, so 8 bits look smooth. It also acts as a tone curve that compresses the
dynamic range.

**How.** A digital gain, then a power law, implemented as a lookup table (as in hardware):

$$
Y = \text{sdr}\cdot\left(\frac{\operatorname{clip}(g\,x,\ 0,\ \text{hdr})}{\text{hdr}}\right)^{\gamma}
$$

$\gamma < 1$ compresses (encoding for display); $\gamma > 1$ expands. The default 0.42 is
close to 1/2.2 (sRGB). After GAC the data is 8 bit.

!!! note "Differs from openISP"
    openISP lists gamma before the CCM. fast-openISP applies the CCM in linear light first
    (CFA → CCM → GAC), which is colorimetrically correct.

### CSC — Color Space Conversion

**Why.** YCbCr separates **luma** (Y) from **chroma** (Cb, Cr). Later filters can then
sharpen and denoise luma, where detail lives, without shifting colors, and smooth chroma
without blurring detail. Standard matrices exist for BT.601 (SD), BT.709 (HD) and BT.2020
(UHD).

**How.** BT.601, limited range, 8-bit fixed point:

$$
\begin{aligned}
Y  &= \tfrac{1}{256}(\ \ 66R + 129G +\ \ 25B) + 16 \\
Cb &= \tfrac{1}{256}(-38R -\ \ 74G + 112B) + 128 \\
Cr &= \tfrac{1}{256}(112R -\ \ 94G -\ \ 18B) + 128
\end{aligned}
$$

At the end of the pipeline, the inverse BT.601 matrix converts YCbCr back to RGB for
display and export.

---

## YCbCr domain

### NLM — Non-Local Means denoising (luma)

**Why.** Noise is random but image structure repeats. NLM averages each pixel with *similar
patches* from a search window, not just nearby pixels, so it removes noise while keeping edges
and texture.

**How.** For every offset $q$ in a $W \times W$ search window, compare the $P \times P$
patches around $p$ and $p+q$:

$$
d^2(p, q) = \frac{1}{P^2}\sum_{k \in \text{patch}} \big(Y(p+k) - Y(p+q+k)\big)^2,
\qquad w = e^{-d^2 / h^2}
$$

$$
Y'(p) = \frac{\sum_q w(p,q)\,Y(p+q)}{\sum_q w(p,q)}
$$

A larger $h$ smooths more. Cost grows with $W^2$: it is the slowest module (≈ 3 s for
mikros110 at full size).

### BNF — Bilateral Noise Filter (luma)

**Why.** Edge-preserving smoothing: neighbours are weighted both by **distance** and by
**similarity in value**, so pixels on the other side of an edge contribute little.

**How.** 5 × 5 window:

$$
w = \underbrace{e^{-\frac{\|k\|^2}{2\sigma_s^2}}}_{\text{spatial}}\cdot
\underbrace{e^{-\frac{(Y(p+k) - Y(p))^2}{2(255\,\sigma_r)^2}}}_{\text{range}},
\qquad
Y'(p) = \frac{\sum_k w\,Y(p+k)}{\sum_k w}
$$

$\sigma_s$ is `spatial_sigma`; $\sigma_r$ is `intensity_sigma` as a fraction of full scale.

### CEH — Contrast Enhancement (CLAHE)

!!! note "Differs from openISP"
    CEH is not part of openISP; fast-openISP adds it.

**Why.** Global tone curves can't lift shadows in one part of the frame and keep highlights
elsewhere. Adaptive histogram equalisation works on tiles; the *contrast limit* stops it from
amplifying noise in flat regions.

**How.** Split the luma into `tiles` = (rows, cols). For each tile, compute the histogram,
clip each bin to `clip_limit` × (largest bin) and spread the excess evenly over all bins. The
normalised cumulative histogram is the tile's lookup table. Each pixel is mapped through the
lookup tables of the (up to) four nearest tiles and **bilinearly interpolated**, so tile edges
don't show.

### EEH — Edge Enhancement (luma)

**Why.** Sharpening increases *acutance*, the apparent sharpness, by adding a small overshoot
and undershoot on each side of an edge. The classic method is **unsharp masking**, controlled
by a gain, a radius and a threshold (below which detail is treated as noise).

**How.** Extract the high-frequency detail as the difference between luma and a Gaussian blur
(5 × 5, σ = 1.2):

$$
\delta = Y - G_{\sigma} * Y
$$

Then shape it with a piecewise-linear curve using thresholds $t_1$ (`flat_threshold`) and
$t_2$ (`edge_threshold`) and gain $k$ (`edge_gain`):

$$
e(\delta) = \operatorname{sign}(\delta)\cdot\min\!\Big(
\begin{cases}
0 & |\delta| \le t_1 \quad\text{(noise, left alone)}\\[2pt]
k\,t_2\,\frac{|\delta| - t_1}{t_2 - t_1} & t_1 < |\delta| \le t_2\\[2pt]
k\,|\delta| & |\delta| > t_2
\end{cases},\ \ \Delta_{\max}\Big)
$$

$$
Y' = \operatorname{clip}(Y + e(\delta),\ 0,\ 255)
$$

$\Delta_{\max}$ (`delta_threshold`) limits the overshoot to avoid halos. The edge map $\delta$
is passed on to FCS.

!!! note "Differs from openISP"
    openISP extracts edges with a fixed asymmetric kernel and a lookup table.
    fast-openISP uses the difference from a Gaussian blur, which gives fewer artifacts at high
    gains.

### FCS — False Color Suppression (chroma)

**Why.** Demosaicing, lens aberrations and compression can leave colored speckles and fringes
on fine, high-contrast detail. The luma there is fine, but the chroma is wrong. Reducing
saturation along strong edges hides this. The same mechanism can also limit chroma noise in
low light.

**How.** Using the EEH edge map, a chroma gain falls linearly from 1 to 0 between `delta_min`
and `delta_max`:

$$
g = \operatorname{clip}\!\left(\frac{\Delta_{\max} - |\delta|}{\Delta_{\max} - \Delta_{\min}},\ 0,\ 1\right),
\qquad
Cb' = 128 + g\,(Cb - 128), \quad Cr' = 128 + g\,(Cr - 128)
$$

Luma is not changed. Requires EEH.

### HSC — Hue / Saturation Control (chroma)

**Why.** Hue is the angle of a color around the neutral axis; saturation is its distance from
grey, i.e. its purity. In the CbCr plane both are simple geometric operations.

**How.** Rotate by the hue offset $\theta$ and scale by the saturation gain $s$:

$$
\begin{bmatrix} Cb' - 128 \\ Cr' - 128 \end{bmatrix} =
s
\begin{bmatrix} \cos\theta & -\sin\theta \\ \sin\theta & \cos\theta \end{bmatrix}
\begin{bmatrix} Cb - 128 \\ Cr - 128 \end{bmatrix}
$$

The result is clipped to [0, 255]. $s = 0$ gives greyscale; $s > 1$ boosts color.

### BCC — Brightness / Contrast Control (luma)

**How.** Add a brightness offset $b$, then stretch around a pivot with the contrast gain $c$:

$$
Y_1 = \operatorname{clip}(Y + b,\ 0,\ 255), \qquad
Y' = \operatorname{clip}\big(m + c\,(Y_1 - m),\ 0,\ 255\big)
$$

!!! note "Differs from openISP"
    openISP uses the fixed pivot $m = 128$. fast-openISP uses the **median** luma of the
    frame, so changing the contrast doesn't shift the overall brightness of dark or bright
    images.

### SCL — Scaler

Resizes the final YCbCr (or RGB) image to `width` × `height` with bilinear interpolation.
In the GUI preview, the target size is divided by the preview factor.

---

## Beyond this pipeline

openISP also plans ISP **tuning**: deriving LSC grids and CCMs from calibration shots,
auto-exposure and auto-white-balance algorithms, HDR tone mapping, and temporal noise
filtering. Of these, this project implements a simple **grey-world AWB**. The GUI covers the
"tuning tool" part of the openISP roadmap: load raw images and configurations, run each block
on its own, and run the whole pipeline.

## References

- openISP design document: [*Image Signal Processor*](https://github.com/cruxopen/openISP/blob/master/docs/Image%20Signal%20Processor.pdf), cruxopen/openISP.
- Q. Jueqin, [fast-openISP](https://github.com/QiuJueqin/fast-openISP): the NumPy implementation used here.
- H. S. Malvar, L. He, R. Cutler, *High-quality linear interpolation for demosaicing of Bayer-patterned color images*, ICASSP 2004.
- A. Buades, B. Coll, J.-M. Morel, *A non-local algorithm for image denoising*, CVPR 2005.
- C. Tomasi, R. Manduchi, *Bilateral filtering for gray and color images*, ICCV 1998.
- K. Zuiderveld, *Contrast Limited Adaptive Histogram Equalization*, Graphics Gems IV, 1994.
- ITU-R BT.601, *Studio encoding parameters of digital television*.
- D. Pascale, [*How to derive and use a Color Correction Matrix*](http://www.babelcolor.com/index_htm_files/AN-9a%20How%20to%20derive%20and%20use%20a%20Color%20Correction%20Matrix.pdf), BabelColor.

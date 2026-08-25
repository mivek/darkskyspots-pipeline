# Calibration Cross-Check - August 25, 2026

## Decision

`ALR_CALIB_C = 1/1125,44` remains unchanged.

The check does not provide grounds for recalibration: the spot ranking is
largely preserved, the residual is not a constant shift by pollution level,
and the altitude hypothesis does not explain the gap. A correction to the
propagation shape would be a separate study, with no independent field
measurement to settle it.

## Data and Provenance

This measurement is a snapshot of the layers available on August 25, 2026:

- pipeline input: VIIRS 2025 (`input/viirs_2025_raw.tif`) ;
- reference: lightpollutionmap's 2025 Sky Brightness layer, exported on
  August 25, 2026 ;
- Sky Brightness raster:
  `validation/sky_brightness/sb_2025_western_europe.tif` ;
- raster SHA-256:
  `6b6543bbb1b8f5135651c7ff2f25f8f9acbde2e13d5197a0e4d3a36ea7b5741a` ;
- actual returned extent: `[-4,8916667 ; 6,0000000]°E ×
  [42,50 ; 52,00]°N` ;
- Sky Brightness resolution: 30 arc-seconds, versus 15 arc-seconds for the
  pipeline raster ;
- full provenance: `validation/sky_brightness/manifest.json`.

The check reads the pixel containing each coordinate in each raster, without
interpolation. It covers 3 146 rows: 2 143 published French spots, 991 foreign
grid points, and 12 spot checks. The foreign points are in
`validation/crosscheck_samples.json`. The elevations used to test the
topographic hypothesis come from ASTER30m via OpenTopoData, so they are
modeled rather than measured, in `validation/france_spot_elevations.json` for
the French spots.

Sky Brightness is converted according to FAQ31:

```text
total_brightness = artificial_brightness + 0.171168465 mcd/m²
SQM = log10(total_brightness / 108000000) / −0.4
```

The natural reference point is therefore 22,00 mag/arcsec². The SQM from
lightpollutionmap remains a model, not a field measurement. Since both
pipelines share the same VIIRS, their agreement may be agreement on a false
hypothesis; this check measures a divergence between models, not physical
truth.

## SQM Residuals

Residual defined as `SQM_pipeline − SQM_lightpollutionmap`.

| Country | n | mean | median | standard deviation | Q1 | Q3 |
|---|---:|---:|---:|---:|---:|---:|
| France | 2 147 | −0.128 | −0.105 | 0.129 | −0.185 | −0.066 |
| Spain | 495 | −0.287 | −0.326 | 0.178 | −0.412 | −0.181 |
| United Kingdom | 504 | −0.153 | −0.200 | 0.252 | −0.296 | −0.094 |
| Overall | 3 146 | −0.157 | −0.128 | 0.172 | −0.255 | −0.073 |

The four known French spot checks are included in the France row; the manual
foreign points whose SQM was missing are evaluated by the Sky Brightness layer
and included in their respective countries.

By reference pollution level:

| Level | n | mean residual | median | standard deviation |
|---|---:|---:|---:|---:|
| Dark, SQM ≥ 21 | 2 700 | −0.161 | −0.124 | 0.121 |
| Rural, 20 ≤ SQM < 21 | 355 | −0.214 | −0.269 | 0.242 |
| Urban, SQM < 20 | 91 | +0.196 | +0.129 | 0.478 |

The sign change rules out a single multiplicative or additive calibration
correction. It also shows that the gap is present in the dark zone where the
spots are useful.

By latitude, the 1°-band averages are:

| Latitude | n | mean residual |
|---|---:|---:|
| 42–43° | 325 | −0.273 |
| 43–44° | 467 | −0.216 |
| 44–45° | 287 | −0.097 |
| 45–46° | 305 | −0.123 |
| 46–47° | 297 | −0.092 |
| 47–48° | 336 | −0.110 |
| 48–49° | 324 | −0.115 |
| 49–50° | 223 | −0.190 |
| 50–51° | 278 | −0.156 |
| 51–52° | 304 | −0.169 |

There is no monotonic latitudinal gradient. In any case, this extent does not
allow any conclusion about Scandinavia or high latitudes.

## Effect on Score and Ranking

Conditions: clear sky, new moon, full astronomical night; ideal score
`round(darkness × 10)`.

| Country | 0 point | 1 point | 2 points | 3+ points | mean Δ | median Δ |
|---|---:|---:|---:|---:|---:|---:|
| France | 393 | 1 305 | 449 | 0 | −0.98 | −1 |
| Spain | 50 | 303 | 142 | 0 | −1.16 | −1 |
| United Kingdom | 112 | 345 | 46 | 1 | −0.78 | −1 |
| Overall | 555 | 1 953 | 637 | 1 | −0.98 | −1 |

On an integer score, any non-zero difference is necessarily at least one
point. The distribution gives the magnitude: 17,6 % with no difference,
62,1 % at one point, 20,3 % at two points, and 0,03 % at three points or
more.

For the 2 143 French spots, Spearman is `0,9786`. There are 117 098 discordant
pairs out of 2 289 114 non-equal pairs, or 5,12 %; about 94,9 % of pairs keep
their order. The ranking useful to the application is therefore robust, even
if the absolute score is generally shifted by about one point.

## Altitude Hypothesis

The average altitude of the three samples is 682 m in Spain, 265 m for the
French spots, and 97 m in the United Kingdom. For the 2 143 French spots, the
residual and altitude have `Pearson = 0,038`, `Spearman = 0,100`, and
`R² = 0,0015`, with a slope of `+0,016 mag/km`. The effect is negligible and
does not match the hypothesis of a bias increasing with altitude.

The regressions by country give `R² = 0,0015` in Spain, `0,0015` in France,
and `0,0160` in the United Kingdom; the slopes even change sign. The multiple
regression confirms that, in the French sample, the standardized altitude beta
is `−0,007`, versus `+0,093` for darkness. Since darkness and Bortle are
linked outputs, these coefficients are descriptive associations rather than
causal effects.

The Spanish sample remains a Pyrenean band of less than 100 km,
`42,55–43,40°N`, with an average altitude of 682 m: its mean residual of
−0,287 should not be read as a national effect. The British sample covers only
southern England, `50,25–51,70°N`.

## Reproduce the Measurement

Per-point detail remains intentionally in `output/`, ignored by Git:
`output/crosscheck/report.json` and `output/crosscheck/altitude_residual.svg`.
The detailed report can be regenerated with the versioned artifacts:

```bash
python calibrate_exact.py \
  --points validation/calibration_points.json \
  --darkness output/crosscheck/debug_darkness_france_2025.tif \
  --bortle output/crosscheck/debug_bortle_france_2025.tif \
  --sky-brightness validation/sky_brightness/sb_2025_western_europe.tif \
  --spots-dir output/crosscheck/spots \
  --elevation-overrides validation/france_spot_elevations.json \
  --foreign-samples validation/crosscheck_samples.json \
  --scatter-out output/crosscheck/altitude_residual.svg \
  --json-out output/crosscheck/report.json
```

A future discrepancy reported by a user, or an extension toward high
latitudes, will trigger physical validation. Comparing against a model shared
with VIIRS is not enough to justify a new constant.

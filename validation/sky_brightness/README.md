# Versioned Sky Brightness

`sb_2025_western_europe.tif` is the polygon export of the 2025 Sky Brightness
layer from lightpollutionmap, retrieved on August 25, 2026. The provenance,
actual returned extent, and SHA-256 are in
[`manifest.json`](manifest.json).

The layer is at 30 arc-seconds (`EPSG:4326`). The cross-check reads the pixel
containing the coordinate in this layer and in the pipeline darkness raster,
without interpolation. The pipeline raster is finer (15 arc-seconds), but the
comparison therefore does not claim precision better than 30 arc-seconds.

The Sky Brightness value is converted according to FAQ31:

```text
SQM = log10((artificial_brightness + 0.171168465 mcd/m²) / 108000000) / −0.4
```

The export depends on an API used by the current interface, while the public
documentation is contradictory. It is versioned precisely so that future
checks do not depend on a live call.

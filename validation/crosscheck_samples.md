# Foreign Cross-Check Sample

`crosscheck_samples.json` contains the computable points outside France within
the versioned Sky Brightness extent. They were built on a regular 0.05°
grid, then filtered by the versioned Natural Earth borders and by the presence
of a valid pixel in both rasters. The file contains 491 Spanish points and
500 British points. Each point also carries an ASTER30m elevation from
OpenTopoData: this is a modeled elevation, not a field measurement.

The file ultimately covers only `42,55–43,40°N, −2,79–−0,29°E` in Spain and
`50,25–51,70°N, −4,64–1,36°E` in the United Kingdom: the pipeline darkness
raster does not provide valid pixels farther south or farther east in the
shared extent. The Sky Brightness export is wider; sampling those zones would
require recalculating ALR on an input covering those pixels.

These points are not field measurements and do not constitute a new
calibration. Their role is to provide reproducible spatial coverage in the
portion that is actually computable.

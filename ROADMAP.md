# Roadmap

## Validate calibration against physical SQM measurements

Status: planned.

The model cross-check against lightpollutionmap is structurally circular: both
chains use the same VIIRS input. The next independent validation is therefore
to use the site's physical SQM, SQM-L, SQM-LE and SQC layers, sourced from the
Unihedron database and user contributions.

Prerequisite: the measurements are downloadable as CSV, but do not carry a
normalized country field. Geocode and deduplicate the records first, then
count usable measurements in France, Spain and the United Kingdom. Those
counts are currently unknown and are the first fact to establish.

Trigger: a user-reported divergence, or an extension of the calibration audit
to high latitudes.

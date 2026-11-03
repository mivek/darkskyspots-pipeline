# Separate geographic distribution assessments

## Prepared geometries

OSMCoastline and Geofabrik geometries, including spatial selections and reviewed
water subsets, are OSM-derived databases. Distribute these under ODbL 1.0 with
"© OpenStreetMap contributors", source identification, the prepared derivative
database or applicable data offer, provenance and the preparation recipe. The
software licence does not replace the data licence. Adding geometries to a
public source repository is a distribution of those data.

References: [OSM licensing](https://www.openstreetmap.org/copyright),
[OSMCoastline licence](https://osmdata.openstreetmap.de/info/license.html),
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), and
[OSMF attribution guidance](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines).

Prepared packages remain under ignored `input/`; downloaded archives and
unassessed derived geometries are not added to the public code repository.
Geographic test fixtures are synthetic unless a separate data notice says
otherwise. Preserve source and member SHA-256, snapshot dates and attribution.

## Imagery-derived geometries

Permission to display imagery does not automatically permit tracing or
redistributing derived polygons. Record provider, acquisition metadata,
derivation permission and distribution terms before including any traced
geometry. The preparation tool does not trace imagery. Uncertain imagery must
not be used to invent islands, shorelines or water exclusions.

## Generated spot database

This is a separate publication assessment, still pending. Selection of
VIIRS-derived coordinates using an OSM-derived mask may have database-licensing
implications; independently computed coordinates do not establish exemption.
Assess the actual selection/combination process, resulting database licence,
attribution and required data offer. Preserve the licences for the other
pipeline inputs. Do not infer approval from the geometry assessment alone.

References: OSMF [collective-database guidance](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Collective_Database_Guideline_Guideline)
and [produced-work guidance](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Produced_Work_-_Guideline).

Prepared manifests default to `spot_distribution: pending`. Publication is
blocked until this distinct assessment is explicitly recorded. An assessed
synthetic fixture is not a licence approval for the real spot database.

Use `--licensing-assessment` when preparing a new revision to attach that
separate record. An `assessed` status requires its reference, reviewer and
resulting database licence; the code does not provide a legal sign-off.
Packaged WBM rasters retain their Copernicus terms and provenance separately
from the OSM-derived vector layers.

## WorldCover

ESA WorldCover 2021 v200 uses CC-BY-4.0. Retain its version, source DOI,
attribution/citation, licence and modification notices with regional rasters
and resulting products. See [ESA data access/licensing](https://esa-worldcover.org/en/data-access)
and the [2021 v200 dataset](https://doi.org/10.5281/zenodo.7254221).
This does not waive the OSM-derived database's ODbL obligations or resolve the
separate generated-spot assessment. E3 adds no imagery-derived geometry; real
package licensing remains pending.

# E3 2025 generated-database licensing assessment

Record: `darkspots-e3-2025-odbl-2026-11-04`. Date: **2026-11-04**.
Responsible maintainer and distribution decision owner: **[@mivek](https://github.com/mivek)**.
Authority: the maintainer decisions explicitly confirmed in the task instruction
and the follow-up requiring use of the GitHub handle only. No separate legal
reviewer is identified or implied.

## Actual sources and retained provenance

| Source actually used | Version / retained identity | Rights and use |
| --- | --- | --- |
| NASA Black Marble, redistributed by Jurij Stare/lightpollutionmap.info | 2025 VJ146A4, Collection 2, `AllAngle_Composite_Snow_Free`, according to distributor documentation | NASA LAADS unrestricted-use/non-copyright policy; distributor identifies NASA raw data as CC0 and requests both credits; radiance input |
| OpenStreetMap via OSMCoastline | Retained `land-polygons-split-4326.zip`, recorded retrieval and last modification 2026-10-31 | ODbL 1.0; land support |
| OpenStreetMap via Geofabrik | 40 retained extract records with dated `261030` URLs, member hashes and recorded retrieval metadata | ODbL 1.0; hydrographic/wetland evidence |
| ESA WorldCover | 10 m 2021 v200, DOI `10.5281/zenodo.7254221`; 24 retained tile files | CC BY 4.0; native-resolution land-cover qualification |
| Copernicus DEM and WBM | GLO-30 DGED release `2024_1`, retained regional manifests and paired raster checksums | Copernicus WorldDEM-30 general-public licence; elevation sampling and water qualification |
| GeoNames | Retained `cities500` archive/text and filtered FR/ES/GB/IE extracts | CC BY 4.0; coverage, nearest towns and names |
| Natural Earth | v5.1.1, 1:10m land and admin-0 countries | Public domain; land/country qualification |

The [metadata-only provenance inventory](e3-2025-provenance.json) records actual
local paths, available SHA-256 values, recorded source URLs/dates and versions.
The GeoNames naming manifest records FR/ES/GB acquisitions on 2026-08-25 and IE
on 2026-08-27. The actual `cities500.txt` matches the retained ZIP member.
Unknown acquisition dates are not inferred from filesystem timestamps.

The maintainer confirmed lightpollutionmap.info as the source of the retained
2025 GeoTIFFs. Its [FAQ](https://www.lightpollutionmap.info/help.html) identifies
the raw 2025 download as VJ146A4 and distinguishes it from RGB EPSG:3857 rendered
maps and modeled Sky Brightness. The local inputs are single-band Float32,
EPSG:4326, on the 15-arc-second grid. Each regional raster is an exact pixel
subset of the retained global TIFF. The global file's size and CRC32 match the
member currently offered through the official raw-download link.

These are concordant indicators supporting identification as raw VIIRS, **not
a cryptographic comparison against the remote TIFF and not proof of historical
acquisition**. Local SHA-256 values identify retained bytes; the original
download URLs/dates and NASA granule identifiers remain unknown. Product,
collection and band identification rely partly on provider documentation,
because embedded local tags do not identify them. No new original URL, product
granule ID or download date has been invented. No separately licensed rendered
or modeled lightpollutionmap.info layer is an identified generation input.

## Generation and the unresolved mandatory classification

Darkspots computes ALR, darkness and Bortle from raw VIIRS radiance. Coordinates
are the original VIIRS pixel centres on the frozen grid. Natural Earth provides
land/country attribution. E3 combines WBM, OSMCoastline, Geofabrik hydrography
and WorldCover to classify candidates A/B1/B2/C. This classification changes
which centre is selected in a mesh cell, its priority in same-Bortle redundancy
filtering and which candidates can supply locality coverage. OSM therefore
influences the membership and selection of the resulting database even though
coordinates are not copied from OSM nodes and no OSM geometry is exported in
the public spot JSON. Clusters summarize the resulting selected records.

The database is machine-readable; calling it a produced work or claiming that
independently calculated coordinates categorically exempt it from ODbL is not
justified. Conversely, the evidence does not conclusively establish the legal
thresholds for extraction/re-utilisation of a substantial part or whether this
particular qualification/combination creates a derivative database. The
[OSMF collective-database](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Collective_Database_Guideline_Guideline)
and [produced-work guidance](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Produced_Work_-_Guideline)
inform the assessment but do not decide this specific case.

## Maintainer decision and obligations

**@mivek has conservatively chosen ODbL 1.0 (`ODbL-1.0`) for the generated
Darkspots spot database and derived cluster databases.** This records the
maintainer's actual decision; it is not a finding that mandatory ODbL status
has been conclusively established or independently certified.

The source-rights assessment for this distribution is complete at the
documentary level: no identified upstream restriction prevents distributing
these derived databases with the prepared notices. Outstanding provenance
limitations are disclosed above and are not filled with assumptions about
historical downloads. The [notice review](e3-2025-notice-review.md) is separate
from the technical publication audit. Final maintainer review remains required.

- Preserve OSM contributor credit, ODbL notice and the required source notices
  with copies. Publicly used derivative databases must meet ODbL share-alike
  and section 4.6 access obligations. Produced works need attribution and any
  applicable underlying-database offer.
- Retain GeoNames and WorldCover CC BY 4.0 attribution, licence references and
  descriptions of the filtering, sampling and derived selection. The exact
  WorldCover provider attribution and full 2021 v200 citation appear in the
  data repository's `ATTRIBUTION.md`.
- Retain the exact Copernicus GLO-30 derived-product and article 6(c) liability
  notices, non-endorsement and applicable downstream obligations. Do not apply
  the separate restricted EEA-10 conditions to these GLO-30 inputs.
- Credit NASA's Black Marble nighttime lights product and Jurij Stare,
  www.lightpollutionmap.info. Do not imply that every distributor layer is CC0.
- Database ODbL licensing does **not** relicense individual upstream raster or
  vector datasets, waive their content rights or grant provider trademarks.

The machine-readable E3 release payload is prepared in
[mivek/darkskyspots-data](https://github.com/mivek/darkskyspots-data), in
`spots/` and `clusters/`, with the accompanying `README.md`, `LICENSE` and
`ATTRIBUTION.md`. This assessment identifies the intended public location; the
database is obtainable there when the reviewed release commit is authorized and
pushed. The ODbL database offer applies to the actual released files, not to
unpublished local source geometries.

Private prepared source geometries, rasters, spatial indexes and source
packages remain in ignored `input/`; none is being added to the public data
repository. Prepared OSM-derived geometry databases separately retain ODbL.
Keeping them private does not waive an offer if a future distribution or
public use of such a derivative triggers one: assess that event and supply
the derivative database or the applicable alterations/method offer. This
assessment makes no blanket claim that sharing spots settles every possible
future geometry-distribution obligation. No imagery tracing was introduced.

## References and remaining release conditions

Official terms consulted on 2026-11-04:
[OSM](https://www.openstreetmap.org/copyright),
[OSMCoastline](https://osmdata.openstreetmap.de/info/license.html),
[Geofabrik](https://www.geofabrik.de/data/download.html),
[NASA policy](https://ladsweb.modaps.eosdis.nasa.gov/learn/),
[NASA product](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VJ146A4),
[lightpollutionmap.info](https://www.lightpollutionmap.info/help.html),
[WorldCover](https://esa-worldcover.org/en/data-access),
[Copernicus product](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM),
[Copernicus licence compilation, GLO-30 pages 22–24](https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf),
[GeoNames](https://download.geonames.org/export/dump/readme.txt),
[Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/), and
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).

The OSMCoastline licence page was available through the official indexed
search result but its server was unreachable for a fresh HTML capture. Its
recorded licence is also corroborated by the retained source manifest and
OSM's own licence. Other consulted pages are retained locally with checksums.

Remaining concerns include incomplete historical VIIRS provenance, the
non-conclusive mandatory ODbL classification, individual upstream content and
trademark rights, and ensuring that downstream copies retain the notices.
The data are not assurances of land access, safety or dry ground.

An `assessed` package field records this completed source-rights assessment and
the maintainer's decision; it is not a legal certification. `approve_tree`
checks spots and binds the complete staged Git tree, but does not examine the
accuracy or completeness of licence documents. Final review of these documents
and release artifacts by **@mivek** and separate publication authorization
remain mandatory. The maintainer reports that the original package and delta
were transferred through Nextcloud and the original was independently
restored; the release audit records local integrity results but does not assert
an authenticated Nextcloud object identity. No publication is authorized by
this assessment or its technical audit.

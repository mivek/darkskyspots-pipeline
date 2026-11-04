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

The real E3 2025 database now has a separate
[source-rights assessment and maintainer decision](licensing/e3-2025-assessment.md),
[retained-source metadata/checksums](licensing/e3-2025-provenance.json), and
[explicit notice review](licensing/e3-2025-notice-review.md), dated 2026-11-04.
**@mivek** has conservatively chosen **ODbL 1.0** for the generated spot and
derived cluster databases. OSM-derived evidence influences qualification,
ranking and membership of the VIIRS-centred candidate database. Mandatory ODbL
classification is not conclusively established; independent coordinate
generation alone does not establish exemption. This decision does not relicense
upstream inputs or assert independently certified compliance.

References: OSMF [collective-database guidance](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Collective_Database_Guideline_Guideline)
and [produced-work guidance](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Produced_Work_-_Guideline).

Prepared manifests default to `spot_distribution: pending`. The original
`e3-p2-final` package remains unchanged and pending. A new immutable revision
may record the completed separate assessment; an assessed synthetic fixture
is not a licence approval for the real database.

Use `--licensing-assessment` when preparing a new revision to attach that
separate record. An `assessed` status requires its reference, reviewer and
resulting database licence; use `ODbL-1.0` for this decision and identify the
responsible maintainer as **@mivek**. The code does not provide a legal sign-off.
Packaged WBM rasters retain their Copernicus terms and provenance separately
from the OSM-derived vector layers.

## WorldCover

ESA WorldCover 2021 v200 uses CC-BY-4.0. Retain its version, source DOI,
attribution/citation, licence and modification notices with regional rasters
and resulting products. See [ESA data access/licensing](https://esa-worldcover.org/en/data-access)
and the [2021 v200 dataset](https://doi.org/10.5281/zenodo.7254221).
This does not waive the OSM-derived database's ODbL obligations. E3 adds no
imagery-derived geometry. The original package's pending flag is historical;
the separate real-data assessment above supports a licensing-only revision.

## Technical integrity is separate from notice review

`approve_tree` audits the spots represented by the Git tree and binds its
result to that tree's identity, including any staged notices. It does not check
whether `ATTRIBUTION.md` is accurate or every supplier obligation is satisfied.
Review `README.md`, `LICENSE` and `ATTRIBUTION.md` explicitly against official
instructions, particularly the exact Copernicus derived-product/liability
notices and WorldCover attribution, citation and modification statement.
Preserve both the separate documentary review and the technical report.

An `assessed` field is neither certification nor permission to publish. Final
maintainer review, durable artifact transfer and independently verified
restoration remain release requirements. Preserve the disclosed VIIRS
provenance limitations: matching local pixels and publisher size/CRC32 are
concordant evidence, not a cryptographic remote identity comparison or a
reconstructed historical download date.

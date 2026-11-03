# E3 geographic confidence and publication

The initial package covers France, UK/Ireland and Canaries. Rules are global:
no national runtime sources, coordinate exceptions, buffers or darkness penalties.
Confidence is evidence-relative; it guarantees neither dry ground nor access,
permission or safety. Darkspots does not model tides.

## Shared classification

Natural Earth land/country qualification, including the original border-pixel
area tie-break, runs first. E3 then evaluates verified WBM, OSMCoastline land,
prepared OSM hydrography and WorldCover **2021 v200** at native WBM support and
the original VIIRS centre.

| Tier | Evidence |
|---|---|
| C | WBM ocean/lake/river, valid WBM NoData, or active corroborated-water interior. NoData is inadmissible evidence, not observed water. |
| B2 | Incomplete coastline land support; mapped water; tidal influence; marsh/saltmarsh/tidalflat/swamp/reedbed/mangrove/untyped wetland; WorldCover water (80), herbaceous wetland (90) or mangrove (95); applicable explicit hold. |
| B1 | No B2 reason, but bog/wet meadow/fen uncertainty, incomplete hydrographic collection support, or legitimate unavailable/partial WorldCover evidence. |
| A | WBM land, complete supporting coastline land and hydrographic collection support, terrestrial WorldCover support/centre and no known disagreement. |

Keep every reason, including moderate reasons accompanying B2. Bog/fen/meadow
does not establish safety; water, tidal and aquatic raster evidence takes
precedence. Available tidal/intermittent/wetland/water attributes are retained,
never invented. Collection coverage does not establish OSM mapping completeness.
WorldCover alone never creates C. Vegetation cannot waive river/coast evidence.
Disputed islands, including Civrac, remain uncertainty rather than an invented
island exception or water exclusion. Preserve actual polygon holes.

Native raster reads use no overview or resampling. Bound calculations snap only
sub-1e-7-native-pixel numerical deviations at integer boundaries. Actual centre
coordinates are unchanged; consult an extra original 1×1 centre sample when it
falls adjacent to the positive-area WBM-support window. Exact 3° parallels use
the tile south of the line, following native row direction. Antimeridian supports
consult both sides. Outside WorldCover's -60° to 82.75° support is explicitly
unavailable. Valid product NoData may cause B1. Missing expected files, invalid
classes, checksum mismatches and technical failures abort, never degrade silently.

## Selection and bounded lookup

Within each original mesh cell select **A → B1 → B2**, then darkness descending
with stable row-major ties. Exclude C and examine another original VIIRS centre.
An A maximum is already optimal and retains exact coordinates/darkness/Bortle.
Rank a rejected/lower-tier cell once, retaining its best fallback while examining
alternatives. Redundancy uses the same priority and the unchanged same-Bortle
strictly-less-than-15-km rule; lower tiers cannot suppress retained higher tiers.
Coverage retains locality order, 100 km, target four and one-centre-per-cell stock,
adding A then B1 then B2, darkest within each tier. Uncertain coverage additions
receive no automatic publication acceptance. ALR/halo, grid, altitude and public
formats are unchanged; internal `_geographic_*` fields cannot enter public JSON.

SQLite/RTree supplies lookup candidates only. Exact predicates use authoritative
geometry: original WKB or certified unions of its source-vertex fragments, with
holes preserved. Component envelopes and outward-rounded
index bounds never become masks. Canonical ±180 representations do not create
shorelines; regional preparation retains whole intersecting features, including
features just outside a region that intersect native WBM support. Its explicit
preparation domain includes supplied native raster footprints; a lookup outside
the verified domain blocks rather than assuming land.

Index schema 2 (`PRAGMA user_version=2`) stores features above 8 MiB without
requiring a country-specific split product. Preserve the complete authoritative
WKB in 1-MiB checksummed-reconstruction chunks and retain the original feature
identity. Split original polygon components first; oversized components use
constrained Delaunay triangulation from source vertices, respecting holes.
Preparation requires the complete reconstructed component's normalized WKB to
equal the authoritative normalized WKB, including boundary coordinates and
otherwise redundant boundary vertices. Topological equality alone is insufficient.
Failed certification aborts preparation; no snapping, simplification or repair is
used to obtain a certificate.

Triangles are sorted deterministically through disk-backed SQLite temporary
storage and unioned within pages of at most 1 MiB. The final serialized page set,
not just the intermediate triangles, must pass the coordinate certificate.
Original components already below
8 MiB can be stored directly as pages. RTree primitive envelopes only select
pages; all relevant whole fragments of an original feature are unioned before
predicates. Internal seams never become independent shorelines or evidence IDs.
Tests compare source and indexed predicates on boundaries, seam vertices, holes,
narrow features and one-ULP neighbours. Review fingerprints use the authoritative
WKB intersection, never a tessellation-dependent digest.

The geometry cache remains 64 MiB; a query has a separate 64-MiB encoded-geometry
working budget (four-times-WKB accounting). Over-budget reconstruction or original
WKB reads fail explicitly. In particular, a review requiring an original feature
larger than that workspace can still be blocked: there is no automatic memory
increase or approximate fingerprint fallback. `GeographicIndex(query_bytes=...)`
allows an explicit technical budget; normal E3 uses the documented default.
These are accounting bounds, not a claim that whole-process RSS is 64 MiB.
Preparation still parses one source feature and holds its largest component's
GEOS triangulation; its peak memory is proportional to that input. Triangulation
can materially amplify storage. See the [final E3 validation report](../validation/e3_final.md) for measured costs.

Shapely >=2.1 and GEOS >=3.10 with constrained-triangulation capability are required
and checked before preparation creates output. Index metadata records exact
library versions. Existing unversioned indexes remain readable. An upgrade rebuilds
them into a new immutable package; it never rewrites the installed source package.
Batches: 4,096. One regional index: 64 MiB geometry cache, 32 MiB SQLite cache.
WorldCover: 64 MiB blocks, four raster handles. Existing WBM: 64 MiB values/mask
cache plus 32 MiB GDAL cache. RSS additionally includes Natural Earth, arrays,
Python objects and native libraries. Generation/publication remain offline.

## Final-only review and immutable enforcement

Audit the complete merged payload after redundancy, coverage, ownership merging
and global clusters. Intermediate losers need no acceptance. Confidence never
changes as a side effect of human decisions.

* A may pass when no other blocker exists.
* Genuine B1 needs reason-scoped `accept_uncertainty` with evidence/provenance.
* Solely legitimate complementary gaps may use an explicitly approved, versioned
  regional degraded-evidence policy covering full native support. Hydrographic
  gaps need an upstream inventory; upstream WorldCover NoData needs a documented
  availability record. Outside-product support is identified by the reader.
  Policies cannot waive contradictions, wetlands, holds or technical failures.
* B2 needs positive, evidence-bound `resolve_risk` for every significant reason:
  generally-above-water evidence, rather than acknowledging tides. Resolutions
  are audit-only and never alter ranking. Additional B1 reasons need acceptance.
* C cannot be waived.

Package schema 2 / `geographic-confidence-e3-v1` supports review schemas 3 and 4.
Schema 3 supports water exclusions, holds and B1 acceptances; it cannot contain
B2 resolutions, even if additional positive fields have been supplied.
`resolve_risk` requires explicit renewal under schema 4. Each reason-scoped
resolution contains this additional human-authored record:

```json
{
  "conclusion": {
    "outcome": "concern_resolved",
    "generally_above_water": true,
    "basis": "Explanation of how the evidence resolves this specific concern"
  },
  "reviewed_by": "Identifiable reviewer",
  "review_reference": "Identifiable review record",
  "evidence": [{
    "source": "Identifiable evidence provider",
    "reference": "Specific evidence item or retained document",
    "resolved_reason": "mapped_surface_water_disagreement",
    "finding": "Substantive finding relevant to that reason"
  }]
}
```

Existing reason, provenance and context fingerprints are also mandatory. A generic
acknowledgement, negative/unknown conclusion, non-boolean positive flag, missing
reviewer/reference, or evidence attached to another reason is rejected. Each
observed concern needs its own resolution; a B2 resolution cannot waive B1 reasons.
Structural validation checks an explicit human assertion and its references, not
the truth of the evidence. Substantive geographic review remains necessary;
acknowledging a tidal risk is not evidence that it was resolved. Preparation never
generates positive conclusions. An explicit schema-4 review can renew a schema-3
B2 package through evidence-only preparation; generation and publication reject
the obsolete package throughout that process. The source package stays unchanged.

B1 `acceptances` never become B2 `resolutions`. Contexts bind relevant local source
identities, attributes, original geometries, native WorldCover/WBM pixels and
availability records. Outside-extent changes preserve decisions; new reasons or
changed relevant evidence block. Generated review contexts never approve humans'
decisions. Applicable holds, including stale holds, block even without another
disagreement. Removal, replacement or extent/region change requires a new reviewed
revision with `reviewed_by`, `rationale` and `supersedes` matching the old review
SHA-256. Stale water exclusions are blocking input errors.

The shared gate binds an actual `git write-tree` object and geographic, decision
and implementation revisions, verifies the index before commit and the commit's
tree before push. Cluster-only publication and supported migrations use the same
gate. Altitude/country migrations stage temporary copies; failed validation leaves
the destination unchanged. Legacy packages are comparison inputs, never production
approval. No live acceptance, risk resolution or degraded policy is issued here.

## Coordinated initial migration

```bash
darkspots/bin/python tools/migrate_e3.py \
  --source-data-repo /path/to/published-data --reference <source-commit> \
  --geographic-manifest input/geography/revisions/<sha256>/manifest.json \
  --year 2025 --generated 2026-11-02T00:00:00Z \
  --output /tmp/e3-complete-snapshot
```

Generate every configured region, merge into one complete temporary snapshot,
regenerate global clusters once and audit one staged tree. An unresolved retained
region blocks the complete snapshot. There is no partial-registry, apply, commit
or push mode; the snapshot remote is removed and the source remains untouched.
Normal migration uses local VIIRS/ALR. For regression only, add
`--frozen-simulation /path/to/retained-arrays`; checksums/grid/CRS are verified.

See [final E3 validation](../validation/e3_final.md), [artifact retention](geographic-artifacts.md)
and [licensing](geographic-licensing.md). Code readiness is separate from data
publication approval, licensing assessment and durable-release restore proof.

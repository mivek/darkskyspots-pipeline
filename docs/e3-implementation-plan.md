# E3 implementation and acceptance

Implement the approved corrected E3 hierarchy and strict A → B1 → B2 → darkness priority. Keep source-derived confidence separate from final publication decisions. B2 resolutions are audit-only, reason-specific and evidence-bound; legacy acceptances never become resolutions. No live degraded-evidence authorization or new human acceptance is created by implementation.

1. Prepare immutable regional SQLite/RTree evidence and verified WorldCover 2021 v200 assets. The RTree only selects authoritative geometries; index boxes/fragments never determine a geographic predicate. Preserve holes, original attributes, source provenance and exact native raster sampling, including the centre/support numerical correction. Bound caches and batch sizes; generation and publication stay offline.
2. Share classification between candidate qualification and final auditing. Preserve country attribution, ALR/halo, VIIRS grid, stable ties, same-Bortle <15 km redundancy, and 100-km conditional coverage. Keep internal tiers out of public JSON.
3. Enforce final-only B1 acceptance, separately approved versioned degraded-evidence policies for solely documented complementary coverage gaps, and positive B2 risk resolutions. Missing expected assets and technical failures are never degraded operation. Explicit holds, including stale holds, block until a new reviewed revision removes them. Bind attempts to immutable staged Git trees and all relevant code/data/decision revisions.
4. Provide an offline coordinated initial migration: generate all three regions, merge country ownership blocks into one temporary complete snapshot, regenerate clusters once, and audit one immutable tree. Never publish regions separately to evade preserved-region disagreements. A remaining unresolved region must block the complete snapshot. The workflow has no push/apply-to-published-repository operation.
5. Test antimeridian regions/geometries, high-latitude WorldCover unavailability, cross-region features, index/fragment/raster boundaries, islands/holes, wetland subtypes, scoped decision migration, stale holds and genuine mixed-tier coverage. Compare every frozen E3 coordinate/stage and historical control, not only totals. Expected final counts: 2207/1217/66, total 3490, 25 coordinate substitutions relative to E2, no worsened coverage and no final B/C.
6. Run focused and complete tests; benchmark preparation, lookups, selection, redundancy, coverage and final audit separately with RSS. Explain every deviation. Retain private reproducible assets and separate licensing/retention prerequisites from code readiness. Update Draft PR #13 in logical Conventional Commits. Do not merge or publish data; the published repository remains unchanged.

## Approved P2 corrections

Replace the complete-WKB 8-MiB rejection with versioned, bounded storage and
source-vertex constrained triangulation. Preserve original WKB/identity, certify
normalized boundary-coordinate equality, union queried fragments before exact
predicates, and retain original review-context fingerprints. Check Shapely >=2.1 /
GEOS >=3.10 before preparation. Document storage amplification, proportional GEOS
preparation memory and explicit working-budget failures rather than approximating.

Require review schema 4 for B2 risk resolutions, including structured positive
conclusion, identifiable reviewer/review reference and reason-specific evidence.
Schema 3 remains valid for its water/hold/B1 decision types; never auto-renew B2.
Rebuild all regional indexes, verify frozen D/E2/E3 and historical controls, and
rerun focused/full tests and publication safeguards. Keep the PR Draft and data
repository untouched. Results: [final E3 validation](../validation/e3_final.md) and [structured results](../validation/e3_final.json).

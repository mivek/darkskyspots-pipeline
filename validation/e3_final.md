# E3 final implementation and validation

**Code review status:** the E3 implementation is ready for review. PR #13 remains Draft.
**Data publication status:** not authorized. Generated-spot licensing and durable artifact release/restore remain prerequisites.

## Policy and safeguards

E3 classifies candidate evidence as A (preferred terrestrial), B1 (moderate uncertainty), B2 (significant water or inundation risk), or C (identified water or inadmissible WBM). Ranking is strict A → B1 → B2 → darkness. Confidence describes the available evidence; A does not guarantee dry ground, access or safety. WorldCover supplements WBM, OSMCoastline and prepared OSM hydrography rather than acting as a water veto.

The implementation preserves Natural Earth country attribution, frozen VIIRS centres and mesh anchoring, ALR and its halo, deterministic ties, the same-Bortle strictly-under-15-km redundancy rule, conditional 100-km locality coverage, altitude sampling and public spot formats. Lower confidence cannot suppress an already retained higher-confidence spot of the same Bortle.

Final geographic review runs on the complete merged payload after redundancy, coverage, ownership merging and cluster regeneration. WBM is mandatory. B1 uncertainty needs a scoped reviewed decision, except for explicitly authorized degraded-evidence policies tied to documented coverage limits. B2 requires a positive, reason-specific schema-4 resolution with identifiable evidence, reviewer and review reference. C, unresolved B2, missing/corrupt evidence, new disagreements, and active or stale holds block approval. Decisions do not promote confidence.

Publication binds the final geographic report to the immutable staged Git tree and geographic revisions. Coordinated migration stages all three regions, clusters and audit together before approval. Validation failures leave the destination unchanged.

## Frozen three-region comparison

The complete frozen D/E2/E3 replay reproduces every selected, deduplicated and covered sequence exactly, including ordering, coordinates, darkness, Bortle, country and E3 tier. E3's final output is A-only.

| Region | D | E2 | E3 final |
|---|---:|---:|---:|
| France | 2,210 | 2,207 | 2,207 |
| UK/Ireland | 1,218 | 1,217 | 1,217 |
| Canary Islands | 66 | 66 | 66 |
| **Total** | **3,494** | **3,490** | **3,490** |

E3 changes 25 final coordinates relative to E2. No new or worsened locality coverage deficit was observed. Darkness/Bortle trade-offs are those recorded in the frozen study and are not altered by this cleanup.

All 3,349 historical WBM-land controls retain their frozen confidence and evidence: A=3,265, B1=18, B2=65, C=1. The full 4,181-coordinate regression comparison remains exact. The eight probable-water controls, Civrac-sur-Dordogne, sixteen uncertain cases, three harmful replacement chains, Galway and the two WorldCover conflict controls retain their reviewed outcomes. Losing selection priority is not reported as confirmed water.

The original published 3,422-spot bootstrap remains blocked (74 C rejections and 83 unresolved holds). The temporary complete 3,490-spot payload passes the geographic audit, while the shared publication gate remains blocked by the pending generated-data licensing assessment.

## Geometry and B2 review corrections

Oversized authoritative geometries retain original WKB, source identity and review-context fingerprints. Constrained triangulation is used only for bounded index storage. The final serialized pages must reconstruct the normalized authoritative WKB byte-for-byte, including boundary coordinate representation; indexed predicates union relevant fragments and evaluate the original geometry semantics. Failure to certify or fit the explicit query budget blocks rather than approximates. Preparation checks Shapely >=2.1 and GEOS >=3.10 before creating output; validated runtime was Shapely 2.2.0 / GEOS 3.14.1.

B2 resolutions require schema 4, a positive concern_resolved conclusion, generally_above_water=true, substantive basis, reviewer, review reference and evidence explicitly tied to the resolved reason. Schema-3 B2 resolutions are rejected pending explicit renewal. Schema-3 water, hold and B1 decisions remain supported. No decision is auto-created or promoted.

## Tests and measured costs

- Complete suite: **539 passed**, 792 Affine pending-deprecation warnings, 141.51 s on the cleaned branch. The focused P2 suite was recorded at 75ea305: 144 passed in 96.01 s; its tests are included in the full rerun.
- Focused geographic, subdivision, review, selection, migration and publication suite: **144 passed**, 766 warnings, 96.01 s.
- Regional index rebuild: 477.26 s / 236.52 MiB peak; indexes total 950,300,672 bytes. Existing 1,079,640 features all use the unfragmented path.
- Synthetic 8,480,113-byte polygon: subdivision/index preparation 67.61 s / 498.48 MiB peak; 39 pages; final index 65,908,736 bytes. Twenty boundary/seam/hole/narrow-feature predicate comparisons matched the authoritative geometry. The complete D/E2/E3 replay also had exact stage sequences and passed final audit; its process peak RSS was 947.50 MiB, reported separately from the standalone audit.
- Fresh oversized-index reader: five probes in 0.57 s / 247.79 MiB peak; authoritative context comparison 0.12 s.
- Standalone 3,490-payload audit: 24.63 s / 330.34 MiB peak, including integrity verification and geographic evaluation. One contemporaneous comparison with the prior index reader was 24.31 s vs 24.68 s; this is one pair, not a statistical benchmark.

RSS was sampled every 10 ms and can miss shorter peaks. Preparation memory remains proportional to the largest source feature/GEOS triangulation; index storage can amplify source size. The 64-MiB cache and query accounting are not a whole-process memory cap. Over-budget exact queries fail closed. Current source geometries did not exercise subdivision; oversized behavior is covered by large synthetic geometries.

## Reproducibility record

Approved source tree before this documentation/history cleanup: 75ea305bbf454ee85f42346a1b21e4aa07593306. The grouped implementation commit is recorded as 862bcdb744306b85c4fe4cb9df2e3282f1b42db6; the rewritten PR head is recorded in the PR description. The pre-cleanup full source remains available as local backup ref backup/pr13-before-cleanup-75ea305.

The exact private prepared geographic package used for final acceptance is input/geography/e3-p2-final/manifest.json, SHA-256 6ac72c9735f6f9d2d93c6e47e1a1a60ee011820f628f977225cfc52684fc39ff. Review-document SHA-256 is 7583f4c6a9ab74172c427d0207c40260042f8725d9411a5dce1aa8ddb2f19e88; local reviewed-context fingerprint is 640f693bc700bf71246cf566021f7232ee5acd3542194fffe0ca677f18a8620c. The source validation manifest fingerprint is b291678148093f3125a8bc1631e854d5bae9cd47543408e03cd749d5b771323b; the WorldCover 2021 v200 tile-manifest fingerprint is 117d72ce8adcbf7949764f8b7f63f781dcb32b547371fc35aef97ddc8b83f36a. The frozen regression inventory SHA-256 is fe8cc14f1c6f341d28f2afc0d0fcf61112d50d14705d7c267338a17cc2ff6035; the frozen-study inventory is 2b2bc682fbe76756b79ec4629e65f51e7dbe54087bed4091039abb70ece155a1; the full regression-output digest is b45188673278df5e2b3b5c15c1eb35567c7ad9b1def6f83e267a0c640f38210f. The study provenance records frozen-generation source commit 3e68f332627fb4ed0912c12f29a3988ddd31ad76.

Frozen ALR arrays use EPSG:4326, a 1/240-degree grid and identical parameters in each region: alpha base 2.3, alpha exponent 0.28, calibration C 0.0008885413704862098, 38 rings, 300 km maximum, noise floor 0.5, 450 m work resolution, dark 0.1, bright 10.0 and epsilon 0.001. Per-region input, darkness-array and Bortle-array SHA-256 values are in the JSON record. The full private inputs and reports remain in ignored input/geography/e3-regression-reference/ and input/geography/e3-p2-final-acceptance/.

Rebuild the regional geographic package from the retained source manifests:

    darkspots/bin/python tools/prepare_e3.py \
      --base-manifest input/geography/e3-validation/manifest.json \
      --worldcover-manifest input/geography/e3-validation/worldcover/manifest.json \
      --output /tmp/e3-final-package

Then reproduce the frozen selection comparison with the retained package and arrays:

    darkspots/bin/python tools/validate_e3.py \
      --manifest input/geography/e3-p2-final/manifest.json \
      --frozen input/geography/e3-regression-reference/frozen \
      --e2 input/geography/e3-regression-reference/e2 \
      --e3 input/geography/e3-regression-reference/study/results \
      --output /tmp/e3-final-replay

Run the complete suite with darkspots/bin/pytest. The original historical reports and their SHA-256/Git-blob inventory are retained locally at /tmp/darkskyspots-pr13-history/75ea305/. This is a local temporary archive, not a durable backup or verified restore.

## Readiness

**Code:** ready for review; the current Actions result is shown on the rewritten PR head.
**Publication:** not ready or authorized. The separate generated-spot licensing assessment and authenticated durable artifact retention/restore process remain outstanding. The published data repository remains unchanged at 1502e353ef7f88aa63a95deb5678e7af33b9b028.

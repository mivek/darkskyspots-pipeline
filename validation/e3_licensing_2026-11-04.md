# E3 licensing revision and final offline publication audit

Date: 2026-11-04. Responsible maintainer: **@mivek**.
**Local candidate only; nothing was published, committed, merged or deployed.**

## Separate documentary and technical conclusions

The [source-rights assessment](../docs/licensing/e3-2025-assessment.md),
[actual provenance inventory](../docs/licensing/e3-2025-provenance.json) and
[notice review](../docs/licensing/e3-2025-notice-review.md) document the
maintainer's conservative **ODbL 1.0** decision for the spot and derived cluster
databases. Source contents retain their own rights. The data repository now
has local `README.md`, canonical `LICENSE` and `ATTRIBUTION.md` files.

The notice review separately checked the exact Copernicus GLO-30 derived-product
and article 6(c) liability notices, full WorldCover 2021 v200 citation/provider
notice and Darkspots modification statement, and the other upstream credits.
It records **no independent legal certification** and **no final maintainer
approval**. `approve_tree` does not verify legal notices or supplier compliance.

VIIRS origin is supported by the maintainer's confirmation, provider documentation,
exact regional/global pixel correspondence, and matching publisher member size/
CRC32. This is not cryptographic remote identity proof. Historical download
URLs/dates and original NASA granule IDs remain unknown. OSMCoastline's live
licence page could not be captured; its official indexed page and recorded ODbL
source licence were corroborated against OSM's official terms.

## Immutable geographic revision

- Original manifest SHA-256: `6ac72c9735f6f9d2d93c6e47e1a1a60ee011820f628f977225cfc52684fc39ff`.
- New manifest: `input/geography/e3-p2-odbl-2026-11-04/manifest.json`.
- New SHA-256: **`34cc9365f131d3c6a0abaf2d7c83e3bc74d38630cd6b9787ed278893e25caaa6`**.
- Only the manifest's `licensing` section changed. All 192 other package files
  remain byte-identical; geographic decisions and review contexts are unchanged.
- The [schema-compatible JSON](../docs/licensing/e3-2025-assessment.json)
  preserves existing metadata, records `ODbL-1.0`, binds the real assessment,
  review and notice hashes, and sets `spot_distribution` to `assessed`.

Prepared with `tools/prepare_e3.py --licensing-assessment`, then verified by the
production `GeographicDataset(..., require_e3=True)` integrity checks. The
original package was rehashed after the audit and remains unchanged.

## Complete frozen migration

Source data commit: `1502e353ef7f88aa63a95deb5678e7af33b9b028`.
Year: `2025`; fixed generation timestamp: `2026-11-03T00:00:00Z`.
Frozen arrays: `input/geography/e3-regression-reference/frozen/`.
Fresh output: `input/geography/e3-odbl-2026-11-04-migration/`.

| Region | Spots | Confidence |
| --- | ---: | --- |
| France | 2,207 | A |
| United Kingdom and Ireland | 1,217 | A |
| Canary Islands | 66 | A |
| **Total** | **3,490** | **All A** |

The full final technical gate **passed**: `no_additional_objection=3490`, no
licensing-pending blocker. Every selection/envelope file and geographic finding
matches the prior preview exactly. All spot and cluster bytes, identifiers,
coordinates, generation metadata, naming and altitudes are unchanged.

All 3,490 identifiers are unique and match the coordinate-ID contract. Independent
native DEM/WBM reads confirm all 3,490 integer altitudes and WBM land samples.
Each cluster index SHA-256 matches its file; each level accounts for 3,490 spots
and its representatives match the spot records. The detailed JSON contains all
cluster hashes and the identifier/altitude digests.

- Data-only Git tree, exactly reproduced: `d9513a965b887bc7183f9d0045041a3ea65f171d`.
- Final tree including notices: **`511315b53262f329c8daa503f3f4273ce7cf79e3`**.
- The tree difference consists solely of added `README.md`, `LICENSE` and
  `ATTRIBUTION.md`. Reports additionally change status, package revision, tree
  identity and attempt ID; geography, decisions and implementation are unchanged.
- Implementation revision: `dfd866513f00aa3079b5e21e6ce435f86c4d90c5575fe38b97b891a4496ae7c1`.
- Decisions revision: `7583f4c6a9ab74172c427d0207c40260042f8725d9411a5dce1aa8ddb2f19e88`.
- Existing focused tests: **129 passed**, 648 Affine pending-deprecation warnings.
  Production algorithms, frozen arrays and the published data payload were
  verified unchanged. The isolated snapshot has no Git remote.

## Preserved evidence and remaining prerequisites

Full report: `input/geography/e3-odbl-2026-11-04-migration/final-report.json`.
Report SHA-256: `224f57b176d7804428ec15bed4b2173fc5aaba8a7010c978c0f37ca1594dbe90`.

Local artifacts are retained under
`input/geography/e3-odbl-2026-11-04-records/`: complete final/prior reports,
package/snapshot inventories, commands, software versions, source checksums,
official-term captures, documentary review and verification scripts/logs.

| Private artifact | SHA-256 |
| --- | --- |
| `geographic-package.tar` | `fbbd59a4946d368208863c387839ea4b4430a45c1eb5c40237fabc70de12154d` |
| `staged-snapshot.tar` | `fa3bd196feee9cf107ab831a845934c8141afc240b1383852a106cfb51ac27bb` |

`artifact-inventory.json` inventories the retained audit collection;
`audit-records.tar.gz` bundles audit evidence, and `handoff-record.json` records
the local archive/inventory hashes. They are private local artifacts, not proof
of an authenticated external release or independently tested restoration.
See the retained `REVIEW_REQUIRED.md` for the reproduction-input and transfer
requirements. No raw geographic source data or unpublished spot coordinates
were added to the public pipeline documents.

**Publication remains stopped.** @mivek must review the exact licensing and
release artifacts. The separately handled Nextcloud transfer, authenticated
retention/version record and independent restoration remain mandatory, followed
by separate publication authorization. A technical `passed` does not satisfy
these prerequisites.

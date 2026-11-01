# WBM land-selection implementation validation

No data was published. A/B/C/D ran on identical full-precision arrays for each region. The source data repository stayed at `1502e353ef7f88aa63a95deb5678e7af33b9b028`, with unchanged tracked files and unchanged pre-existing untracked IDE files. All global spot/cluster snapshots are under `/tmp/darksky-wbm-simulation/snapshots/`.

## Historical scenario A

France and UK/Ireland reproduce exactly. 48 Canaries darkness values differ by at most 2.7977620220553945e-14; positions, Bortle, countries and naming match. Differences exist in A without WBM. A 350-km ALR kernel was tested and does not reproduce the published values; directory names do not establish kernel parameters. Floating-point/FFT build differences are plausible, but the original build environment was not archived; exact last-bit cause remains unresolved. Absolute comparison tolerance is 1e-12, explicitly reported, not rounded away.

The full reference field differences are retained in each regional `historical-A.json`. Naming, nearest locality and GeoNames provenance were recomputed: no naming differences in any region. Altitude enrichment is intentionally common to all temporary snapshots and is evaluated separately from selection; it is not evidence of a WBM selection change.

## Final spot counts

| Country | A: historical | B: discard water | C: geographic rescan | D: geographic + WBM |
|---|---:|---:|---:|---:|
| FR | 2189 | 2179 | 2210 | 2210 |
| GB | 821 | 814 | 849 | 842 |
| IE | 358 | 355 | 378 | 376 |
| ES | 54 | 53 | 67 | 66 |
| **Total** | **3422** | **3401** | **3504** | **3494** |

A to D final-cell transitions: **3,000 retained, 32 moved, 390 removed, 462 newly represented cells**. These are cell-based transitions; a changed coordinate generates a different public ID. C to D isolates the WBM effect from recovery after geographic rejection. C still produces 181 WBM-water final points; D produces none.

| Region | Stage | Retained | Moved | Removed | New |
|---|---|---:|---:|---:|---:|
| france | selected | 25226 | 224 | 2 | 542 |
| france | deduplicated | 2037 | 13 | 112 | 132 |
| france | covered | 2063 | 14 | 112 | 133 |
| uk_ireland | selected | 13982 | 264 | 15 | 1180 |
| uk_ireland | deduplicated | 896 | 17 | 246 | 282 |
| uk_ireland | covered | 913 | 17 | 249 | 288 |
| canaries | selected | 249 | 6 | 0 | 195 |
| canaries | deduplicated | 24 | 1 | 29 | 41 |
| canaries | covered | 24 | 1 | 29 | 41 |

## Positive controls and coverage

Independent scalar raster reads checked every one of the **41,868 D selected candidates**: all WBM land. All **39,457 historical WBM-land mesh maxima** preserve row/column, coordinates, darkness, Bortle and country exactly at selection. This includes all **3,349 published WBM-land controls**. The original 73 WBM-water coordinates never appear in final D.

The final greedy redundancy result changes: **349 previously published WBM-land points disappear after redundancy and are not restored by coverage**. Each remains in the D selection pool. Its actual retained same-Bortle suppressor within 15 km, with equal or higher darkness, is listed in the JSON report. 11 removals involve an OSM-suspect suppressor and deserve particular review.

No WBM NoData candidates were encountered in these regional runs. Future nonzero NoData counters are reported for review rather than treated as terrestrial pixels or silently merged with water.

**Coverage loss:** La Roquette-sur-Var changes from four to three spots within 100 km. The old Esparron lake point is at 99.695 km. Its terrestrial replacement in the same cell is at 100.027943 km. The eligible pool contains only three centres inside the radius, and all three are selected. No radius expansion or fabricated spot was introduced. No new four-to-fewer-than-four loss was found in UK/Ireland or the Canaries.

## OSM disagreements requiring review

The existing Geofabrik water surfaces and global OSMCoastline land polygons were checked against the entire D pool, not only the original anomalies. There are **24 suspect final positions, including 17 newly selected final positions**. WBM land is therefore insufficient to establish that every result is physically dry. Tidal flats, shoreline differences, source age and map errors remain possible explanations; no automatic correction was made.

| Region | D final | Suspect final | New suspect final | Outside inland-extract coverage |
|---|---:|---:|---:|---:|
| france | 2210 | 11 | 6 | 678 |
| uk_ireland | 1218 | 13 | 11 | 33 |
| canaries | 66 | 0 | 0 | 0 |

The missing inland-extract coverage concerns 711 final points. Global OSM land-side containment was checked everywhere, but does not exclude lakes or rivers. Absence from selected OSM water polygons is never treated as proof of dry land. The 24 cases are listed below and in the JSON with source feature IDs.

| ID | Country | Name / near | New vs A | OSM observation |
|---|---|---|---|---|
| `50.1979_1.5062` | FR | Brighton / Cayeux-sur-Mer | True | sea side; tidal flat |
| `49.6937_-1.2646` | FR | Gatteville-le-Phare / Gatteville-le-Phare | True | sea side |
| `48.8687_-3.2271` | FR | Porz Bugalé / Plougrescant | True | sea side |
| `48.8396_-3.5146` | FR | Île Dhu / Trégastel | True | sea side |
| `48.7146_-3.8313` | FR | Trégastel / Plougasnou | True | sea side |
| `47.4896_-2.4563` | FR | Tréhiguier / Pénestin | False | sea side; tidal flat |
| `47.3562_0.5229` | FR | Berthenay / Berthenay | False | unnamed (water) |
| `46.2062_-1.3563` | FR | Saint-Martin-de-Ré / Saint-Martin-de-Ré | False | sea side; tidal flat |
| `44.9687_-0.2146` | FR | Les Eymerits / Savignac-de-l'Isle | False | unnamed (water) |
| `44.8354_-0.0771` | FR | Civrac-sur-Dordogne / Saint-Pey-de-Castets | False | unnamed (riverbank) |
| `43.3729_4.5687` | FR | Pointe du Sablon / Saintes-Maries-de-la-Mer | True | unnamed (water) |
| `54.8854_-3.5854` | GB | Southerness / Silloth | True | sea side |
| `54.5896_-0.9854` | GB | Saltburn-by-the-Sea / Saltburn-by-the-Sea | True | sea side |
| `54.5438_-5.4813` | GB | Ballywalter / Ballywalter | True | sea side |
| `54.4979_-0.6563` | GB | Raithwaite / Whitby | True | sea side |
| `54.0063_-6.1104` | IE | Ballagan / Carlingford | True | sea side |
| `53.8646_-6.2438` | IE | Roadstown / Clogherhead | True | sea side |
| `52.8021_1.5854` | GB | Eccles on Sea / Stalham | True | sea side |
| `52.6604_1.6021` | GB | Billockby / Filby | False | unnamed (water) |
| `52.2062_-6.6396` | IE | Cull Island / Taghmon | True | sea side; tidal flat |
| `51.6979_-2.9063` | GB | Llanbadoc / Llanbadoc | False | unnamed (riverbank) |
| `51.5604_-2.9979` | GB | Duffryn / Newport | True | unnamed (water) |
| `51.1896_-4.2229` | GB | Mortehoe / Woolacombe | True | sea side |
| `50.9021_0.7104` | GB | Winchelsea Beach / Winchelsea Beach | True | sea side |

## Performance and resource bounds

France, three warm repetitions after an empty application-cache run: A selection median **7.113 s**, D selector **5.551 s**. Including the geographic/WBM guards used by the normal orchestrator, D is **7.028 s** versus A **7.113 s** (ratio **0.988**). All measured outputs match the saved simulations exactly.

WBM LRU peak: **40.77 MiB**, below its 64 MiB bound including validity masks. GDAL has a separate 32 MiB bound. Benchmark process peak RSS: 526.2 MiB; the full ALR/comparison process peaked at 1372.4 MiB. These process totals include arrays, geometry and comparison records and are not cache sizes.

The initial naive retry was too slow on geographically empty cells. Profiling then identified repeated scalar `rasterio.xy` envelope calls. Conservative cell-envelope rejection, prepared geometry indexes and batched affine envelope calculations removed that overhead. Pixel ranking occurs once per rejected cell; WBM reads are grouped by native block. Cold means application cache, not a flushed OS filesystem cache. Initial full-run timings are retained as observations, not substituted for the isolated benchmark.

## Verification and review boundary

- `darkspots/bin/pytest -q`: **403 passed**, 12 affine deprecation warnings. Tests cover real frozen cells, exact maximum preservation, country-area attribution, NoData retries, unexpected classes, missing/corrupt/unreadable rasters, grouped projected-WBM transforms, bounded-cache eviction, all four scenarios, mandatory generation qualification and OSM island holes.
- Thirteen real-cell fixtures cover both Lough Neagh points, French coasts and lake banks, Clogher/Ryhope disagreements, a river bank and terrestrial island controls. Lough Neagh cells have no admissible centre and remain empty.
- Global A/B/C/D temporary snapshots passed country/ownership audits, ID uniqueness and spot-field checks. Six cluster levels and their indexes were regenerated for each. Manifest schema and altitude sampling behavior remain unchanged.
- A real normal-path France run (`run.py --year 2025 --region france --input-dir input --output-dir /tmp/darksky-wbm-normal-france --no-push`) completed successfully: 2,210 spots, all spot fields exactly equal to the D snapshot, all elevations sampled on WBM land, and local clusters generated. This also exercised the mandatory qualifier, pre-redundancy guards and shared verified reader outside the simulation harness.
- Source rasters, Natural Earth files, region ownership rules and the published data repository were not modified. OSM is a validation-only input.
- Review the 24 OSM disagreements, their effect on redundancy, and the one coverage deficit before considering publication. No recommendation to publish is implied.

## Artifacts and reproduction

Compact evidence, provenance hashes, metrics and unresolved cases: [`wbm_selection_2026-11-01.json`](wbm_selection_2026-11-01.json). Large arrays, complete cell transitions, source-point suppressor details, independent checks and all snapshots are under `/tmp/darksky-wbm-simulation/`. Input/array hashes and ALR parameters are in each regional `arrays/metadata.json`. Original OSM sources remain under `/tmp/darksky-water-diagnostic/`.

```bash
darkspots/bin/python tools/compare_wbm.py --data-repo /home/jk/dev_workspace/darkskyspots-data --reference 1502e35 --output-dir /tmp/darksky-wbm-reproduction --snapshots
darkspots/bin/python tools/compare_wbm.py --data-repo /home/jk/dev_workspace/darkskyspots-data --output-dir /tmp/darksky-wbm-reproduction --benchmark-region france
darkspots/bin/python tools/audit_wbm_osm.py --simulation /tmp/darksky-wbm-reproduction --diagnostic /tmp/darksky-water-diagnostic --output /tmp/darksky-wbm-reproduction/osm-audit.json
darkspots/bin/python tools/check_wbm_results.py --simulation /tmp/darksky-wbm-reproduction --data-repo /home/jk/dev_workspace/darkskyspots-data
```

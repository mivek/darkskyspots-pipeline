# E3 2025 licence-notice review

Date: **2026-11-04**. Distribution decision owner: **@mivek**.
This is a documented source-rights and editorial review prepared with Codex
assistance, separate from `approve_tree`. It is not a legal opinion or an
independent certification. Final maintainer review has **not** been recorded.

## Documents examined

The local `mivek/darkskyspots-data` `README.md`, `LICENSE` and `ATTRIBUTION.md`
were read against the retained official terms and actual pipeline usage.
Their exact SHA-256 values are bound into `e3-2025-assessment.json` and the
private release audit. Changes to them require a new review of the changes
and a new tree-bound technical audit before release.

| Document / obligation | Documentary finding |
| --- | --- |
| README: scope and public availability | Describes formats, 2025 radiance year, actual regional bounding boxes, model limitations and upstream rights. It identifies the E3 release payload and geographic revision, reports the audited regional counts, and names the machine-readable distribution location without claiming that an unauthorised push has occurred. |
| LICENSE: canonical ODbL | Copied byte-for-byte from Open Data Commons' official `odbl-10.txt`; the source copy is retained. README and attribution identify @mivek's database decision and scope. |
| NASA and lightpollutionmap.info | NASA policy permits subsequent use/redistribution and requests credit. Distributor FAQ 14 supplies the two credits; FAQ 18/29/30 distinguishes raw Float32 radiance from rendered RGB maps. Collection/band identification is qualified by its evidential basis. Neither all site layers nor historical acquisition are asserted to be proven. |
| OSM / OSMCoastline / Geofabrik | Contributor credit, ODbL reference, actual retained sources and separate geometry obligations are stated. A machine-readable database offer, share-alike and relevant produced-work/access obligations are described. No categorical independent-coordinate exemption is claimed. |
| ESA WorldCover | The 2021 provider attribution is reproduced with year substitution as instructed. The full official 2021 v200 author citation and DOI match the supplier page. Darkspots' native-class sampling and derived qualification/selection are described separately from the provider's Sentinel modification notice. CC BY 4.0 and non-endorsement are retained. |
| Copernicus GLO-30 | The exact derived-product notice matches the provider page and GLO-30 article 6(b). The additional required liability text in article 6(c) is reproduced. Articles 4–9 in the GLO-30 section of the official compilation were examined: distribution/adaptation rights, notices, downstream obligations, non-endorsement and retained supplier rights are reflected. No EEA-10 restrictions or CC0 claim are substituted. |
| GeoNames | CC BY 4.0, provider link, naming/coverage sources and filtering/modification description are stated. Country-extract acquisition dates come from the local manifest; the unknown `cities500` acquisition date remains unknown. |
| Natural Earth | Public-domain status, v5.1.1, actual 1:10m layers and use are stated. |
| Licence boundaries and identity | Database and upstream content rights are distinguished. Only @mivek identifies the responsible maintainer. No raw source data or private package is added to the data repository. |

The exact-text comparisons and canonical-licence hash comparison support this
review; they do not establish that every legal obligation has been satisfied.
The substantive documentary conclusion is that no identified source restriction
prevents the described derived-database distribution with these notices.

## Evidence and limitations

The [assessment](e3-2025-assessment.md) and
[metadata/checksum inventory](e3-2025-provenance.json) identify the sources and
remaining provenance concerns. Official pages/PDFs and their hashes are retained
in ignored `input/geography/e3-odbl-2026-11-04-records/official-terms/` with an
inventory beside that directory. OSMCoastline's live server was unreachable;
its official indexed licence page, retained source record and OSM's official
licence provide the documented corroboration. This capture limitation is
preserved, not treated as a fresh live-page retrieval.

VIIRS size/CRC32 concordance is not cryptographic remote identity proof. Original
download dates/URLs and NASA granule identifiers remain missing. The mandatory
ODbL classification remains legally inconclusive; @mivek's conservative
decision and disclosure do not replace an independent legal determination.

## Release review still required

`approve_tree` audits final spots and binds its result to the entire staged
Git tree. It does **not** inspect supplier terms, verify notice accuracy or
certify compliance. Its `passed` status must be reported separately from this
documentary review and from final maintainer approval.

Before publication, **@mivek must review the three exact notice files and final
release artifacts**, including the VIIRS limitations, ODbL offer and Copernicus/
WorldCover wording. The maintainer reports Nextcloud transfer and independent
restoration; local verification records content hashes but cannot identify the
remote object. This review records neither final maintainer approval nor
permission to publish.

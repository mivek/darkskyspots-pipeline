# Optional accessibility evidence

Accessibility enrichment records nearby mapped roads, tracks, pedestrian paths
and parking as straight-line evidence for existing spots. It does not create
spots, calculate routes, infer connectivity or difficulty, establish legal
permission, recommend stopping places, or determine whether parking is available
at night. It makes no runtime network requests.

## Preparation and supported profiles

Install the preparation-only Pyosmium dependency in addition to the normal
requirements:

```bash
darkspots/bin/python -m pip install -r requirements-accessibility.txt
```

The versioned scope registry is `config/accessibility.yaml`. It currently
supports these profiles:

- `canaries`: Canary Islands G1, assigned from configured Spain ownership,
  prepared with a 6 km buffer in EPSG:32628.
- `great_britain`: GB-only G2, prepared in EPSG:32630.
- `ireland_northern_ireland`: Ireland and Northern Ireland G2, prepared in
  EPSG:32629.

All profiles use a 1 km public search radius and support preparation/query
radii of 500, 1,000, 3,000 and 5,000 m. Profiles bind their source extracts,
coverage polygons, regional DEM manifests, CRS, scope and policy. Use the
versioned configuration and the preparation tools rather than editing generated
manifests by hand. Inputs are local and explicitly fetched; preparation writes
a new immutable dataset revision and does not overwrite an existing one. The
`requirements-accessibility.txt` file contains preparation-only dependencies.

## Public data contract

The public `accessibility` field is optional; existing spots without it remain
valid. Schema 1 contains a content-derived source ID, one shared positive
radius, and queries for `road`, `track`, `path` and `parking`. A query is
`found`, `not_found_within_radius`, or `unknown`. A found result carries the OSM
type and ID, integer straight-line distance, nullable elevation delta, and
`foot`/`motorcar` evidence. Raw tags, conditional expressions, geometry,
nearest-point coordinates, extra candidates and nearby restrictions are not
part of the public payload. Payloads are limited to 4 KiB.

Access precedence is `foot → access` for walking and
`motorcar → motor_vehicle → vehicle → access` for passenger cars. Recognized
values remain literal mapped evidence; unsupported values remain unknown and
do not fall through to broader affirmative tags. A compact `conditional` state
may appear in public evidence to show that a mapped conditional rule exists but
was not evaluated. The conditional expression itself remains excluded. A
generic affirmative `access` tag on a pedestrian path does not imply
motor-vehicle permission. Nearby restrictions remain audit-only and never
become spot warnings.

An empty result is returned only when the complete search disk is covered by
both the prepared domain and provider extract extent and the relevant feature
class has no unresolved geometry errors. Otherwise the result is `unknown`.
Completeness covers supported features in the pinned OSM extract, not all
real-world features. Restriction-relation audit completeness is reported
separately. Elevation delta is the preserved spot altitude minus the unrounded
DEM value at the nearest point on the selected feature; uncertain samples
produce a null delta with a reason.

## Accepted Tenerife regression exception

The accepted 2026-11-08 comparison between legacy Tenerife source
`tfe-f021a-2726a3c584faee70` and Canary Islands G1 source
`can-g1-a7ffe1cfa7d88565` has 72 query differences, all in
`nearbyRestrictionsComplete`: Tenerife reports `false`, while G1 reports
`true`. G1 resolves 33 restriction relations unresolved by the legacy
preparation; their geometries lie outside its prepared domain. The nearest is
relation `10364067` at 4,967.213 m. Candidate selection, distances, access,
elevation, coverage and query statuses are otherwise identical. The difference
was independently reviewed and accepted because the broader G1 domain makes
the restriction audit complete. This exception does not establish legal access
or guarantee complete mapping of real-world restrictions.

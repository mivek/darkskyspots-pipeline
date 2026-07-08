# CHANGELOG

<!-- version list -->

## v1.2.1 (2026-07-08)

### Bug Fixes

- **enrich**: Remove Overpass API calls from step 5, drop name field
  ([`ac7e67e`](https://github.com/mivek/darkskyspots-pipeline/commit/ac7e67ef39fe089d6811908ae82f82deca6f54a3))


## v1.2.0 (2026-07-06)

### Features

- **enrich**: Add coordinate-based spot IDs, strip row/col from output
  ([`3685145`](https://github.com/mivek/darkskyspots-pipeline/commit/368514509b0e513a90d677e4def82d854462cbf2))


## v1.1.1 (2026-07-06)

### Bug Fixes

- **coverage**: Replace Overpass load_communes with GeoNames load_places
  ([`e142d55`](https://github.com/mivek/darkskyspots-pipeline/commit/e142d556620eee197e63608fb8182f8bf086ed82))


## v1.1.0 (2026-07-06)

### Features

- **enrich**: Replace per-spot Overpass queries with single batched fetch
  ([`e75930a`](https://github.com/mivek/darkskyspots-pipeline/commit/e75930ab6e277fcc2797b13f39122ed6cc042652))


## v1.0.1 (2026-07-03)

### Bug Fixes

- **cli, coverage**: Make --data-repo-url optional with --no-push; add Overpass retry, bbox filter,
  and User-Agent header
  ([`38d46e7`](https://github.com/mivek/darkskyspots-pipeline/commit/38d46e7c4982229576dd9f34e658db061f2ba390))


## v1.0.0 (2026-07-03)

- Initial Release

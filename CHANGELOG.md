# CHANGELOG

<!-- version list -->

## v1.4.0 (2026-08-06)

### Bug Fixes

- **pipeline**: Filter sea spots with empty GeoNames near before tile export
  ([`b7073b4`](https://github.com/mivek/darkskyspots-pipeline/commit/b7073b42145c04e44c4aa86c9ad63d0e597cc252))

### Chores

- Ignore local worktrees
  ([`c9ef9c8`](https://github.com/mivek/darkskyspots-pipeline/commit/c9ef9c8ad8dc8235547c940fd5f3ddac5a8a6a9e))

### Continuous Integration

- Run tests on pull requests and main
  ([`ea0f53f`](https://github.com/mivek/darkskyspots-pipeline/commit/ea0f53fec5f3a9e211fb23ee5aa9ff42b20864f6))

### Documentation

- Define cluster pipeline and migration contracts
  ([`48b87a5`](https://github.com/mivek/darkskyspots-pipeline/commit/48b87a56d277ec3f1d3d07d4d162da620b9c6ed3))

### Features

- Generate and publish deterministic spot clusters
  ([`581ed75`](https://github.com/mivek/darkskyspots-pipeline/commit/581ed756ebba817cc3e98bbf8267c34a471bc582))

- Validate region ownership and migration modes
  ([`61843c4`](https://github.com/mivek/darkskyspots-pipeline/commit/61843c4a5eb5236f21ee36525d799fea40e07c3a))


## v1.3.0 (2026-07-09)

### Features

- Update calibration for Europe
  ([`160a468`](https://github.com/mivek/darkskyspots-pipeline/commit/160a468cd150f5e5bf547e4e484cbbf79797f6c2))

- **cli**: Add --debug-raster option to save intermediate darkness/bortle GeoTIFFs
  ([`34bd416`](https://github.com/mivek/darkskyspots-pipeline/commit/34bd4161a6e89298acb15aa0a1b5cecf96c48891))


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

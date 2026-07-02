# Dark Sky Spots Pipeline — Agent Guide

## Entrypoint
```bash
# One-shot batch pipeline (not a server, not long-running)
python run.py --year 2025 --region france --data-repo-url git@github.com:user/data-repo.git
```
Required: GeoTIFF at `--input-dir/<region>/<year>.tif` (default: `./input/france/2025.tif`). No auto-download.

## Tests
```bash
pytest                                          # all 139 tests
pytest tests/unit/                               # unit tests only
pytest tests/integration/test_smoke.py           # full pipeline with synthetic data
pytest tests/unit/test_tile_id.py -xvs           # single module (tile naming)
```
No `--cov`, no `mypy`, no `ruff`, no CI config — plain pytest.

## Architecture
- **7 steps** in `run.py:44` — ALR `→` convert `→` mesh minima `→` redundancy filter `→` OSM coverage `→` enrich `→` tile export + publish
- **Modules** in `src/` mirror pipeline steps: `alr.py`, `convert.py`, `extract.py` (mesh+filter), `coverage.py`, `enrich.py`, `tile_export.py`, `publish.py`
- **Config** in `src/config.py`: ALR tuning, Bortle thresholds, mesh/filter/tile constants
- **Only region:** `france` in `regions.yaml` (bbox `[-5, 41, 10, 51]`, EPSG:3035)
- **Tile IDs:** 3-digit zero-padded — `tile_id(42.7283, 1.6492)` → `"N042E001"`. This is the app contract. Do not change.

## Quirks
- **nightskyquality fork** installed via Git tag (`git+https://github.com/mivek/nightskyquality.git@v1.0.0` in `requirements.txt`). Has a 666-pixel NaN halo — input must be >666px per side.
- **OSM Overpass API** live calls in `coverage.py` and `enrich.py` — tests mock via `@patch("src.coverage.requests.get")`.
- **Publish step** (step 7) does `git clone --depth 1 && git add/commit/push` via `subprocess`. Requires `SSH_AUTH_SOCK`. Use `--no-push` to skip.
- **No type-checking or linting configured** — `pyproject.toml` is minimal (name+version only).
- **Validation** (§6): manually record Bortle at `validation/checkpoints.json` control points after each run. Tune `ALR_CALIB_C` in `src/config.py` if mismatch >±1 class.
- **Large inputs** are sliced automatically if they exceed `--budget-mb` (default 500 MB). `TMPDIR` controls temp file location.

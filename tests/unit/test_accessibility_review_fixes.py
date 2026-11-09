"""Regression coverage for the independently reproduced G2.1a review findings."""

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import rasterio
import yaml
from affine import Affine
from rasterio.transform import from_origin

from src.accessibility_baseline import (
    APPROVED_G2_INVENTORY_SHA256,
    APPROVED_G2_SPOT_COUNT,
    validate_approved_g2_baseline,
)
from src.accessibility_dem import validate_accessibility_dem_manifest


ROOT = Path(__file__).resolve().parents[2]
G1_MANIFEST = ROOT / "input/accessibility/datasets/canaries-261107-g1-v1/manifest.json"
TENERIFE_MANIFEST = ROOT / "input/accessibility/datasets/tenerife-f02-1a-review-fixes/manifest.json"
G2_BASELINE = ROOT / "input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19-g2-full"
G2_INVENTORY = ROOT / "input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19-g2-full-inventory.json"


def test_real_tenerife_and_g1_profiles_retain_dem_requirements_and_identities():
    from src.accessibility_index import AccessibilityIndex
    from src.elevation import ElevationSampler

    if not TENERIFE_MANIFEST.is_file() or not G1_MANIFEST.is_file():
        pytest.skip("Retained Tenerife and Canary G1 datasets are not present")
    with ElevationSampler(ROOT / "data/elevation/manifests/canaries.json", ROOT / "input/elevation") as sampler:
        assert validate_accessibility_dem_manifest(
            ROOT / "data/elevation/manifests/canaries.json", ROOT / "input/elevation",
            "tenerife", "tenerife-legacy-v1",
        )["region"] == "canaries"
        assert validate_accessibility_dem_manifest(
            ROOT / "data/elevation/manifests/canaries.json", ROOT / "input/elevation",
            "canaries", "canary-islands-g1-v1",
        )["region"] == "canaries"
        for path, source_id in (
            (TENERIFE_MANIFEST, "tfe-f021a-2726a3c584faee70"),
            (G1_MANIFEST, "can-g1-a7ffe1cfa7d88565"),
        ):
            index = AccessibilityIndex(path)
            try:
                index.validate_dem_manifest(sampler.manifest_path)
                assert index.source_id == source_id
            finally:
                index.close()


def test_real_g1_manifest_loads_through_both_canary_interfaces_and_keeps_core_bytes(monkeypatch):
    from shapely import to_wkb

    from src.accessibility_index import AccessibilityIndex
    from src.accessibility_prepare import parse_geofabrik_poly
    from src.accessibility_resolver import AccessibilityResolver
    from src.accessibility_scope import GeographicSpecification
    from src.cli import parse_args
    from src.elevation import ElevationSampler

    if not G1_MANIFEST.is_file():
        pytest.skip("Retained immutable Canary G1 index is not present")
    manifest = json.loads(G1_MANIFEST.read_text(encoding="utf-8"))
    expected_source = "can-g1-a7ffe1cfa7d88565"
    assert manifest["dataset_id"] == expected_source
    spec = GeographicSpecification.load(ROOT / "config/accessibility.yaml", "canaries")
    provider = parse_geofabrik_poly(G1_MANIFEST.parent / "coverage.poly")
    core, *_ = spec.projected_geometries(provider)
    retained_wkb = bytes.fromhex(manifest["assignment_core_wkb_hex"])
    assert to_wkb(core) == retained_wkb
    assert hashlib.sha256(retained_wkb).hexdigest() == manifest["identity"]["assignment_core_sha256"]

    legacy_args = parse_args([
        "--year", "2025", "--region", "canaries", "--no-push",
        "--accessibility-manifest", str(G1_MANIFEST),
    ])
    multi_args = parse_args([
        "--year", "2025", "--region", "canaries", "--no-push",
        "--accessibility-dataset", f"canaries={G1_MANIFEST}",
    ])
    assert legacy_args.accessibility_manifest == str(G1_MANIFEST)
    assert multi_args.accessibility_datasets == {"canaries": str(G1_MANIFEST)}

    with ElevationSampler(ROOT / "data/elevation/manifests/canaries.json", ROOT / "input/elevation") as sampler:
        # Legacy single-index loading, including the retained DEM identity.
        single = AccessibilityIndex(G1_MANIFEST)
        single.validate_dem_manifest(sampler.manifest_path)
        single.close()

        # The new resolver accepts the exact same immutable G1 index.
        resolver = AccessibilityResolver(
            {"canaries": G1_MANIFEST}, sampler,
            geographic_config=ROOT / "config/accessibility.yaml",
        )
        try:
            assert resolver.indices["canaries"].source_id == expected_source
            assert resolver.indices["canaries"].manifest["assignment_core_wkb_hex"] == retained_wkb.hex()

            original_projected = GeographicSpecification.projected_geometries

            def modified(current, coverage):
                result = original_projected(current, coverage)
                return (result[0].buffer(0.25), *result[1:])

            monkeypatch.setattr(GeographicSpecification, "projected_geometries", modified)
            with pytest.raises(ValueError, match="assignment_core_sha256"):
                resolver._validate_index("canaries", resolver.indices["canaries"], spec)
        finally:
            resolver.close()


def _tiny_rasters(cache: Path) -> tuple[dict, dict]:
    cache.mkdir(parents=True, exist_ok=True)
    dem = cache / "fixture-dem.tif"
    wbm = cache / "fixture-wbm.tif"
    for path, dtype, value, nodata in ((dem, "int16", 100, -9999), (wbm, "uint8", 0, 255)):
        with rasterio.open(
            path, "w", driver="GTiff", width=2, height=2, count=1, dtype=dtype,
            crs="EPSG:4326", transform=from_origin(-10, 57, 1, 1), nodata=nodata,
        ) as ds:
            import numpy as np
            ds.write(np.full((2, 2), value, dtype=dtype), 1)
    from src.elevation import sha256

    return (
        {"path": dem.name, "size": dem.stat().st_size, "sha256": sha256(dem), "source": "fixture:dem"},
        {"path": wbm.name, "size": wbm.stat().st_size, "sha256": sha256(wbm), "source": "fixture:wbm"},
    )


def _dged_cell_rasters(cache: Path, lat: int, lon: int) -> tuple[dict, dict]:
    """Create sparse native-layout DGED fixture rasters without bulk pixel data."""
    cache.mkdir(parents=True, exist_ok=True)
    center_latitude = abs(lat + 0.5)
    factor = 1 if center_latitude < 50 else 1.5
    dx, dy = factor / 3600, 1 / 3600
    width, height = round(3600 / factor) + 1, 3601
    transform = Affine(dx, 0, lon - dx / 2, 0, -dy, lat + 1 + dy / 2)
    lat_name = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
    lon_name = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
    cell_id = f"{lat_name}_00_{lon_name}_00"
    records = []
    for layer, dtype, value, directory in (
        ("DEM", "float32", 100, "DEM"), ("WBM", "uint8", 0, "AUXFILES"),
    ):
        name = f"Copernicus_DSM_10_{cell_id}_{layer}.tif"
        path = cache / name
        with rasterio.open(
            path, "w", driver="GTiff", width=width, height=height, count=1,
            dtype=dtype, crs="EPSG:4326", transform=transform, tiled=True,
            blockxsize=256, blockysize=256, compress="DEFLATE", SPARSE_OK="TRUE",
        ) as ds:
            ds.update_tags(AREA_OR_POINT="Point")
            ds.write(__import__("numpy").array([[value]], dtype=dtype), 1, window=((0, 1), (0, 1)))
        from src.elevation import sha256

        records.append({
            "path": path.name, "size": path.stat().st_size, "sha256": sha256(path),
            "source": (
                "s3://eodata/CCM/COP-DEM_GLO-30-DGED/SAR_DGE_30_A4AD/2011/01/01/fixture/"
                f"Copernicus_DSM_10_{cell_id}/{directory}/{name}"
            ),
        })
    return records[0], records[1]


@pytest.fixture
def g2_dem_fixture(tmp_path):
    config = yaml.safe_load((ROOT / "config/accessibility.yaml").read_text(encoding="utf-8"))
    policy = config["dem_manifest_policies"]["uk_ireland_accessibility_g2"]
    cache = tmp_path / "cache"
    dem_info, wbm_info = _tiny_rasters(cache)
    source_base = json.loads((ROOT / policy["base_manifest"]).read_text(encoding="utf-8"))
    cells = [(tile["lat"], tile["lon"]) for tile in source_base["tiles"]]
    assert len(cells) == policy["expected_base_pairs"] == 56

    additional = [tuple(cell) for cell in policy["required_additional_cells"]]
    additional_rasters = {cell: _dged_cell_rasters(cache, *cell) for cell in additional}

    def tile(lat, lon):
        if (lat, lon) in additional_rasters:
            dem_item, wbm_item = additional_rasters[(lat, lon)]
        else:
            dem_item, wbm_item = dem_info, wbm_info
        return {
            "lat": lat, "lon": lon, "product": policy["product"], "version": policy["version"],
            "dem": dict(dem_item), "wbm": dict(wbm_item),
        }

    base = {
        "schema": 1, "region": policy["base_region"], "bbox": policy["base_bbox"],
        "tiles": [tile(*cell) for cell in cells],
    }
    base_path = tmp_path / "uk_ireland.json"
    base_bytes = (json.dumps(base, sort_keys=True) + "\n").encode()
    base_path.write_bytes(base_bytes)
    policy["base_manifest"] = str(base_path)
    policy["base_manifest_sha256"] = hashlib.sha256(base_bytes).hexdigest()
    config_path = tmp_path / "accessibility.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    manifest = {
        "schema": 1, "region": policy["region"], "bbox": policy["bbox"],
        "base_manifest": base_path.name, "base_manifest_sha256": policy["base_manifest_sha256"],
        "tiles": [tile(*cell) for cell in cells + additional],
    }
    manifest_path = tmp_path / "uk_ireland-accessibility-g2.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {"config": config_path, "cache": cache, "manifest": manifest_path,
            "base": base, "policy": policy, "tile": tile, "additional": additional}


def _upgrade_fixture_to_64_geographic_dged_cells(fixture):
    """Give the controlled success fixture native DGED grids in all 64 cells."""
    base = fixture["base"]
    base_cells = {(tile["lat"], tile["lon"]): tile for tile in base["tiles"]}
    for cell, tile in base_cells.items():
        tile["dem"], tile["wbm"] = _dged_cell_rasters(fixture["cache"], *cell)

    base_path = Path(fixture["policy"]["base_manifest"])
    base_bytes = (json.dumps(base, sort_keys=True) + "\n").encode()
    base_path.write_bytes(base_bytes)
    base_hash = hashlib.sha256(base_bytes).hexdigest()
    fixture["policy"]["base_manifest_sha256"] = base_hash
    config = yaml.safe_load(fixture["config"].read_text(encoding="utf-8"))
    config["dem_manifest_policies"]["uk_ireland_accessibility_g2"]["base_manifest_sha256"] = base_hash
    fixture["config"].write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    manifest = json.loads(fixture["manifest"].read_text())
    manifest["base_manifest_sha256"] = base_hash
    for tile in manifest["tiles"]:
        cell = tile["lat"], tile["lon"]
        if cell in base_cells:
            tile["dem"] = dict(base_cells[cell]["dem"])
            tile["wbm"] = dict(base_cells[cell]["wbm"])
    _write_manifest(fixture["manifest"], manifest)


def test_g2_full_configured_dem_inventory_passes_with_64_geographic_dged_pairs(g2_dem_fixture):
    _upgrade_fixture_to_64_geographic_dged_cells(g2_dem_fixture)
    result = validate_accessibility_dem_manifest(
        g2_dem_fixture["manifest"], g2_dem_fixture["cache"],
        "great_britain", "great-britain-g2-v1", geographic_config=g2_dem_fixture["config"],
    )
    assert len(result["tiles"]) == 64
    assert len({(tile["lat"], tile["lon"]) for tile in result["tiles"]}) == 64
    for tile in result["tiles"]:
        lat, lon = tile["lat"], tile["lon"]
        factor = 1 if abs(lat + 0.5) < 50 else 1.5
        dx, dy = factor / 3600, 1 / 3600
        expected = Affine(dx, 0, lon - dx / 2, 0, -dy, lat + 1 + dy / 2)
        for layer, dtype in (("dem", "float32"), ("wbm", "uint8")):
            with rasterio.open(g2_dem_fixture["cache"] / tile[layer]["path"]) as ds:
                assert ds.crs == rasterio.crs.CRS.from_epsg(4326)
                assert ds.tags()["AREA_OR_POINT"] == "Point"
                assert (ds.width, ds.height) == (round(3600 / factor) + 1, 3601)
                assert ds.dtypes == (dtype,)
                assert ds.transform == expected


def test_g2_fixture_uses_original_pixel_is_point_55_degree_dged_contract(g2_dem_fixture):
    tile = next(tile for tile in json.loads(g2_dem_fixture["manifest"].read_text())["tiles"]
                if (tile["lat"], tile["lon"]) == (55, -9))
    with rasterio.open(g2_dem_fixture["cache"] / tile["dem"]["path"]) as dem:
        assert (dem.width, dem.height) == (2401, 3601)
        assert dem.crs == rasterio.crs.CRS.from_epsg(4326)
        assert dem.tags()["AREA_OR_POINT"] == "Point"
        assert dem.res == pytest.approx((1.5 / 3600, 1 / 3600))
        assert tuple(dem.transform)[:6] == pytest.approx(
            (1.5 / 3600, 0, -9 - 0.75 / 3600, 0, -1 / 3600, 56 + 0.5 / 3600),
            abs=1e-12,
        )
        assert tuple(dem.bounds) == pytest.approx(
            (-9.000208333333333, 54.99986111111111,
             -7.999791666666667, 56.00013888888889),
            abs=1e-12,
        )


def test_g2_grid_validation_closes_opened_rasters_on_success_and_second_open_failure(
    g2_dem_fixture, monkeypatch,
):
    import src.accessibility_dem as accessibility_dem
    from src.accessibility_scope import GeographicSpecification

    fixture = g2_dem_fixture
    manifest = json.loads(fixture["manifest"].read_text())
    policy = GeographicSpecification.load(fixture["config"], "great_britain").dem_manifest_policy
    real_open = rasterio.open
    opened = []

    class TrackedRaster:
        def __init__(self, dataset):
            self.dataset = dataset
            self.closed = False

        def __getattr__(self, name):
            return getattr(self.dataset, name)

        def __enter__(self):
            self.dataset.__enter__()
            return self

        def __exit__(self, *args):
            result = self.dataset.__exit__(*args)
            self.closed = self.dataset.closed
            return result

    def tracking_open(path, *args, **kwargs):
        dataset = real_open(path, *args, **kwargs)
        tracked = TrackedRaster(dataset)
        opened.append(tracked)
        return tracked

    monkeypatch.setattr(accessibility_dem.rasterio, "open", tracking_open)
    accessibility_dem._validate_g2_added_dged_grids(manifest, policy, fixture["cache"])
    assert len(opened) == 16
    assert all(dataset.closed and dataset.dataset.closed for dataset in opened)

    opened.clear()
    n55_w009 = next(tile for tile in manifest["tiles"] if (tile["lat"], tile["lon"]) == (55, -9))
    wbm_path = fixture["cache"] / n55_w009["wbm"]["path"]

    def fail_on_wbm(path, *args, **kwargs):
        if Path(path) == wbm_path:
            raise rasterio.errors.RasterioIOError("injected WBM open failure")
        dataset = real_open(path, *args, **kwargs)
        tracked = TrackedRaster(dataset)
        opened.append(tracked)
        return tracked

    monkeypatch.setattr(accessibility_dem.rasterio, "open", fail_on_wbm)
    with pytest.raises(ValueError, match="Cannot open G2 WBM raster.*injected WBM open failure") as caught:
        accessibility_dem._validate_g2_added_dged_grids(manifest, policy, fixture["cache"])
    assert len(opened) == 1
    assert opened[0].closed and opened[0].dataset.closed
    assert isinstance(caught.value.__cause__, rasterio.errors.RasterioIOError)


def test_g2_rejects_historical_56_pairs_and_missing_halo_or_wbm(g2_dem_fixture, monkeypatch):
    import src.accessibility_dem as accessibility_dem

    fixture = g2_dem_fixture
    sampler_calls = []
    monkeypatch.setattr(accessibility_dem, "ElevationSampler",
                        lambda *args, **kwargs: sampler_calls.append((args, kwargs)))
    data = json.loads(fixture["manifest"].read_text())
    with pytest.raises(ValueError, match="cell inventory is incomplete"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], {**data, "tiles": data["tiles"][:56]}),
            fixture["cache"], "great_britain", "great-britain-g2-v1",
            geographic_config=fixture["config"],
        )
    assert sampler_calls == []

    data = json.loads(fixture["manifest"].read_text())
    omitted_cell = fixture["additional"][0]
    data["tiles"] = [tile for tile in data["tiles"] if (tile["lat"], tile["lon"]) != omitted_cell]
    with pytest.raises(ValueError, match="cell inventory is incomplete"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )

    data = json.loads(fixture["manifest"].read_text())
    data["tiles"][-1].pop("wbm")
    with pytest.raises(ValueError, match="invalid WBM metadata"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )


def _write_manifest(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _rewrite_dged_raster(fixture, cell, layer, *, width=None, height=None, crs=None,
                         transform=None, dtype=None, point_tag="Point"):
    data = json.loads(fixture["manifest"].read_text())
    tile = next(item for item in data["tiles"] if (item["lat"], item["lon"]) == cell)
    info = tile[layer]
    path = fixture["cache"] / info["path"]
    with rasterio.open(path) as source:
        profile = source.profile.copy()
        old_transform = source.transform
    profile.update(
        width=width or profile["width"], height=height or profile["height"],
        crs=crs or profile["crs"], transform=transform or old_transform,
        dtype=dtype or profile["dtype"],
    )
    temporary = path.with_name(path.stem + ".rewritten.tif")
    with rasterio.open(temporary, "w", **profile) as output:
        output.update_tags(AREA_OR_POINT=point_tag)
        output.write(
            __import__("numpy").array([[0]], dtype=profile["dtype"]), 1,
            window=((0, 1), (0, 1)),
        )
    temporary.replace(path)
    from src.elevation import sha256

    info["size"] = path.stat().st_size
    info["sha256"] = sha256(path)
    _write_manifest(fixture["manifest"], data)
    return data


def _real_n49_w007_pair_relabelled_as_n55_w009(fixture):
    original = json.loads((ROOT / "data/elevation/manifests/uk_ireland.json").read_text())
    n49 = next(tile for tile in original["tiles"] if (tile["lat"], tile["lon"]) == (49, -7))
    data = json.loads(fixture["manifest"].read_text())
    n55 = next(tile for tile in data["tiles"] if (tile["lat"], tile["lon"]) == (55, -9))
    from src.elevation import sha256

    for layer in ("dem", "wbm"):
        source = ROOT / "input/elevation" / n49[layer]["path"]
        target = fixture["cache"] / n55[layer]["path"]
        shutil.copyfile(source, target)
        n55[layer]["size"] = target.stat().st_size
        n55[layer]["sha256"] = sha256(target)
    _write_manifest(fixture["manifest"], data)
    return data


@pytest.mark.parametrize("mutation", [
    "one_pixel_shift", "incorrect_origin", "longitude_resolution",
    "latitude_resolution", "orientation", "dimensions", "oversized", "derived_cog", "crs",
])
def test_g2_rejects_geographically_incorrect_dged_grids_with_updated_hashes(
    g2_dem_fixture, mutation,
):
    fixture = g2_dem_fixture
    cell = (55, -9)
    data = json.loads(fixture["manifest"].read_text())
    tile = next(item for item in data["tiles"] if (item["lat"], item["lon"]) == cell)
    with rasterio.open(fixture["cache"] / tile["dem"]["path"]) as ds:
        transform = ds.transform
        width, height, crs = ds.width, ds.height, ds.crs
    width_override = height_override = crs_override = transform_override = None
    point_tag = "Point"
    if mutation == "one_pixel_shift":
        transform_override = Affine(
            transform.a, transform.b, transform.c + transform.a,
            transform.d, transform.e, transform.f,
        )
    elif mutation == "incorrect_origin":
        transform_override = Affine(
            transform.a, transform.b, transform.c + transform.a / 10,
            transform.d, transform.e, transform.f,
        )
    elif mutation == "longitude_resolution":
        transform_override = Affine(
            transform.a * 1.01, transform.b, transform.c,
            transform.d, transform.e, transform.f,
        )
    elif mutation == "latitude_resolution":
        transform_override = Affine(
            transform.a, transform.b, transform.c,
            transform.d, transform.e * 1.01, transform.f,
        )
    elif mutation == "orientation":
        transform_override = Affine(
            -transform.a, transform.b, transform.c,
            transform.d, -transform.e, transform.f,
        )
    elif mutation == "dimensions":
        width_override = width - 1
    elif mutation == "oversized":
        width_override, height_override = width + 120, height + 120
    elif mutation == "derived_cog":
        width_override, height_override = 2400, 3600
        transform_override = Affine(transform.a, 0, -9, 0, transform.e, 56)
        point_tag = "Area"
    elif mutation == "crs":
        crs_override = "EPSG:3857"

    for layer in ("dem", "wbm"):
        _rewrite_dged_raster(
            fixture, cell, layer, width=width_override, height=height_override,
            crs=crs_override, transform=transform_override, point_tag=point_tag,
        )
    with pytest.raises(ValueError, match="G2|Corrupt elevation|Mismatched"):
        validate_accessibility_dem_manifest(
            fixture["manifest"], fixture["cache"], "great_britain", "great-britain-g2-v1",
            geographic_config=fixture["config"],
        )


def test_g2_rejects_mixed_cell_dem_wbm_and_contradictory_source_metadata(g2_dem_fixture):
    fixture = g2_dem_fixture
    original = json.loads(fixture["manifest"].read_text())
    data = json.loads(json.dumps(original))
    tile_55_w009 = next(tile for tile in data["tiles"] if (tile["lat"], tile["lon"]) == (55, -9))
    tile_55_w008 = next(tile for tile in data["tiles"] if (tile["lat"], tile["lon"]) == (55, -8))
    tile_55_w009["wbm"] = dict(tile_55_w008["wbm"])
    with pytest.raises(ValueError, match="Mismatched DEM and WBM grids"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )

    data = json.loads(json.dumps(original))
    tile_55_w009 = next(tile for tile in data["tiles"] if (tile["lat"], tile["lon"]) == (55, -9))
    tile_55_w009["dem"]["source"] = tile_55_w009["dem"]["source"].replace(
        "COP-DEM_GLO-30-DGED/SAR_DGE_30_A4AD", "COP-DEM_GLO-90-DGED/SAR_DGE_90_A407",
    )
    with pytest.raises(ValueError, match="source metadata contradicts"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )


def test_retained_n49_pair_relabelled_as_n55_is_rejected_in_preparation_and_qualification(
    g2_dem_fixture, tmp_path, monkeypatch,
):
    fixture = g2_dem_fixture
    _real_n49_w007_pair_relabelled_as_n55_w009(fixture)

    # Exercise the preparation entry point up to its first DEM validation. Its
    # output directory and every geographic preparation artifact must remain absent.
    from src import accessibility_prepare
    from src.accessibility_scope import GeographicSpecification
    from tools import prepare_elevation

    original_spec = GeographicSpecification.load(fixture["config"], "great_britain")
    pbf = tmp_path / "controlled-source.pbf"
    polygon = tmp_path / "controlled-source.poly"
    pbf.write_bytes(b"controlled test input")
    polygon.write_text("controlled test polygon", encoding="utf-8")
    source = dict(original_spec.source)
    source.update({
        "pbf_path": str(pbf), "source_url": "https://example.invalid/controlled.pbf",
        "coverage_polygon_path": str(polygon),
    })
    spec = replace(original_spec, dem_manifest=str(fixture["manifest"]), source=source)
    monkeypatch.setattr(GeographicSpecification, "load", lambda *_args, **_kwargs: spec)
    output_dir = tmp_path / "must-not-be-created"
    with pytest.raises(ValueError, match="dimensions|grid"):
        accessibility_prepare.prepare_accessibility(
            pbf, source["source_url"], "great_britain", fixture["manifest"], output_dir,
            coverage_polygon=polygon, dem_cache_dir=fixture["cache"],
            geographic_config=fixture["config"],
        )
    assert not output_dir.exists()

    # Qualification uses the same shared validator and cannot emit a result or
    # reach baseline sampling for this otherwise hash-consistent attack.
    monkeypatch.setattr(prepare_elevation, "validate_approved_g2_baseline", lambda *_args, **_kwargs: {
        "g2_spots": [], "inventory_sha256": APPROVED_G2_INVENTORY_SHA256,
    })
    sample_calls = []
    monkeypatch.setattr(prepare_elevation, "_sample_accessibility_halo",
                        lambda *_args, **_kwargs: sample_calls.append(True))
    with pytest.raises(ValueError, match="dimensions|grid"):
        prepare_elevation.validate_accessibility_halo_samples(
            fixture["manifest"], fixture["cache"], "unused-baseline",
            geographic_config=fixture["config"],
        )
    assert sample_calls == []


def test_runtime_g2_sampler_initialization_uses_the_same_geographic_validator(
    g2_dem_fixture, tmp_path, monkeypatch, caplog,
):
    from run import run
    import run as pipeline
    import src.accessibility_dem as accessibility_dem
    import src.accessibility_index as accessibility_index
    import src.accessibility_resolver as accessibility_resolver
    from src.accessibility_scope import GeographicSpecification

    fixture = g2_dem_fixture
    data = json.loads(fixture["manifest"].read_text())
    tile = next(item for item in data["tiles"] if (item["lat"], item["lon"]) == (55, -9))
    with rasterio.open(fixture["cache"] / tile["dem"]["path"]) as ds:
        shifted = Affine(ds.transform.a, ds.transform.b, ds.transform.c + ds.transform.a,
                         ds.transform.d, ds.transform.e, ds.transform.f)
    for layer in ("dem", "wbm"):
        _rewrite_dged_raster(fixture, (55, -9), layer, transform=shifted)

    original_spec = GeographicSpecification.load(fixture["config"], "great_britain")
    spec = replace(original_spec, dem_manifest=str(fixture["manifest"]))
    monkeypatch.setattr(GeographicSpecification, "load", lambda *_args, **_kwargs: spec)
    monkeypatch.setattr(pipeline, "_validate_geonames_inputs", lambda *_args, **_kwargs: None)
    runtime_samplers = []

    class RuntimeSampler:
        def __init__(self, manifest_path):
            self.manifest_path = Path(manifest_path)
            self.closed = False
            runtime_samplers.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        pipeline, "ElevationSampler",
        lambda manifest_path, *_args, **_kwargs: RuntimeSampler(manifest_path),
    )
    geographic_datasets = []

    def geographic_factory(*_args, **_kwargs):
        dataset = MagicMock()
        dataset.is_e3 = True
        geographic_datasets.append(dataset)
        return dataset

    monkeypatch.setattr(pipeline, "GeographicDataset", geographic_factory)

    real_validator = accessibility_dem.validate_accessibility_dem_manifest
    validator_calls = []

    def validator_spy(*args, **kwargs):
        validator_calls.append((args, kwargs))
        return real_validator(*args, **kwargs)

    monkeypatch.setattr(accessibility_dem, "validate_accessibility_dem_manifest", validator_spy)
    real_validator_sampler = accessibility_dem.ElevationSampler
    validator_samplers = []

    class TrackingElevationSampler(real_validator_sampler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed_after_validation = False
            validator_samplers.append(self)

        def close(self):
            try:
                super().close()
            finally:
                self.closed_after_validation = True

    monkeypatch.setattr(accessibility_dem, "ElevationSampler", TrackingElevationSampler)
    resolver_constructions = []
    index_constructions = []
    monkeypatch.setattr(accessibility_resolver, "AccessibilityResolver", lambda *args, **kwargs: (
        resolver_constructions.append((args, kwargs))
    ))
    monkeypatch.setattr(accessibility_resolver, "AccessibilityIndex", lambda *args, **kwargs: (
        index_constructions.append((args, kwargs))
    ))
    enrichment_calls = []
    monkeypatch.setattr(pipeline, "enrich_all", lambda *args, **kwargs: enrichment_calls.append(True))
    from src.cli import parse_args
    args = parse_args([
        "--year", "2025", "--region", "uk_ireland", "--no-push",
        "--data-repo-url", "git@example.invalid:darkskyspots/data.git",
        "--input-dir", str(tmp_path / "input"), "--output-dir", str(tmp_path / "output"),
    ])
    args.accessibility_datasets = {
        "great_britain": "unused-gb-index.json",
        "ireland_northern_ireland": "unused-ie-index.json",
    }
    args.dem_cache_dir = str(fixture["cache"])

    assert run(args) == 1
    assert len(geographic_datasets) == 1
    geographic_datasets[0].require_region.assert_called_once()
    geographic_datasets[0].close.assert_called_once()
    assert len(validator_calls) == 1
    assert validator_calls[0][0][2:4] == ("great_britain", "great-britain-g2-v1")
    failures = [record for record in caplog.records if record.exc_info]
    assert len(failures) == 1
    assert failures[0].exc_info[0] is ValueError
    assert "G2 DEM geographic-grid mismatch" in str(failures[0].exc_info[1])
    assert "NameError" not in caplog.text
    assert len(runtime_samplers) == 1
    assert runtime_samplers[0].manifest_path != fixture["manifest"]
    assert runtime_samplers[0].closed
    assert len(validator_samplers) == 1
    assert validator_samplers[0].closed_after_validation
    assert resolver_constructions == []
    assert index_constructions == []
    assert enrichment_calls == []
    assert not (tmp_path / "output").exists()


def test_multi_scope_audit_validates_each_g2_profile_before_resolver_and_output(
    g2_dem_fixture, tmp_path, monkeypatch,
):
    from tools import audit_accessibility
    from src.accessibility_scope import GeographicSpecification
    import src.accessibility_resolver as accessibility_resolver

    fixture = g2_dem_fixture
    baseline_root, audit_spots, external_inventory = _baseline_copy(tmp_path)
    audit_inventory = baseline_root / "source-inventory.json"
    shutil.copyfile(external_inventory, audit_inventory)
    configured_specs = {
        scope: replace(
            GeographicSpecification.load(fixture["config"], scope),
            dem_manifest=str(fixture["manifest"]),
        )
        for scope in ("great_britain", "ireland_northern_ireland")
    }
    monkeypatch.setattr(
        GeographicSpecification, "load", lambda _path, scope: configured_specs[scope],
    )
    real_validator = audit_accessibility.validate_accessibility_dem_manifest
    validator_calls = []

    def validator_spy(*args, **kwargs):
        validator_calls.append((args, kwargs))
        return real_validator(*args, **kwargs)

    monkeypatch.setattr(audit_accessibility, "validate_accessibility_dem_manifest", validator_spy)
    resolver_calls = []

    class ResolverReached(RuntimeError):
        pass

    def resolver_factory(*args, **kwargs):
        resolver_calls.append((args, kwargs))
        raise ResolverReached("valid controlled DEM reached resolver construction")

    monkeypatch.setattr(audit_accessibility, "AccessibilityResolver", resolver_factory)
    index_calls = []
    monkeypatch.setattr(accessibility_resolver, "AccessibilityIndex", lambda *args, **kwargs: (
        index_calls.append((args, kwargs))
    ))

    def run_controlled_audit(output_dir):
        return audit_accessibility.run_audit(
            spots_dir=audit_spots,
            clusters_dir=baseline_root / "clusters",
            accessibility_datasets={
                "great_britain": Path("unused-gb-index.json"),
                "ireland_northern_ireland": Path("unused-ie-index.json"),
            },
            baseline_inventory=audit_inventory,
            baseline_inventory_sha256=APPROVED_G2_INVENTORY_SHA256,
            dem_manifest=fixture["manifest"], dem_cache_dir=fixture["cache"],
            radius_m=1000, output_dir=output_dir, geographic_config=fixture["config"],
        )

    accepted_output = tmp_path / "accepted-audit"
    with pytest.raises(ResolverReached, match="valid controlled DEM"):
        run_controlled_audit(accepted_output)
    assert [call[0][2] for call in validator_calls] == [
        "great_britain", "ireland_northern_ireland",
    ]
    assert len(resolver_calls) == 1
    assert index_calls == []
    assert not accepted_output.exists()
    assert not (accepted_output / "audit.json").exists()

    validator_calls.clear()
    _real_n49_w007_pair_relabelled_as_n55_w009(fixture)
    rejected_parent = tmp_path / "must-not-be-created"
    rejected_output = rejected_parent / "audit"
    with pytest.raises(ValueError, match="geographic-grid mismatch"):
        run_controlled_audit(rejected_output)
    assert [call[0][2] for call in validator_calls] == ["great_britain"]
    assert len(resolver_calls) == 1
    assert index_calls == []
    assert not rejected_parent.exists()
    assert not rejected_output.exists()


def test_legacy_g1_audit_dispatch_does_not_enter_g2_dem_validation(monkeypatch, tmp_path):
    from tools import audit_accessibility

    class LegacyValidationReached(RuntimeError):
        pass

    legacy_calls = []
    g2_calls = []

    def stop_at_legacy_validator(path):
        legacy_calls.append(Path(path))
        raise LegacyValidationReached("legacy Canary audit validation reached")

    monkeypatch.setattr(audit_accessibility, "validate_accessibility_manifest", stop_at_legacy_validator)
    monkeypatch.setattr(
        audit_accessibility, "validate_accessibility_dem_manifest",
        lambda *args, **kwargs: g2_calls.append((args, kwargs)),
    )
    with pytest.raises(LegacyValidationReached, match="legacy Canary audit"):
        audit_accessibility.run_audit(
            spots_dir=tmp_path / "spots", clusters_dir=tmp_path / "clusters",
            accessibility_manifest=G1_MANIFEST,
            dem_manifest=ROOT / "data/elevation/manifests/canaries.json",
            dem_cache_dir=ROOT / "input/elevation", radius_m=1000,
            output_dir=tmp_path / "legacy-audit",
        )
    assert legacy_calls == [G1_MANIFEST]
    assert g2_calls == []


def test_g2_rejects_bad_hash_wrong_profile_and_replaced_historical_pair(g2_dem_fixture):
    fixture = g2_dem_fixture
    original = json.loads(fixture["manifest"].read_text())
    data = json.loads(json.dumps(original))
    data["tiles"][-1]["dem"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Corrupt elevation dem"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )

    data = json.loads(json.dumps(original))
    data["tiles"][-1]["wbm"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Corrupt elevation wbm"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )

    data = json.loads(json.dumps(original))
    data["region"] = "canaries"
    with pytest.raises(ValueError, match="requires DEM region"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )

    data = json.loads(json.dumps(original))
    data["tiles"][0]["dem"]["source"] = "fixture:replacement"
    with pytest.raises(ValueError, match="altered historical source pair"):
        validate_accessibility_dem_manifest(
            _write_manifest(fixture["manifest"], data), fixture["cache"],
            "great_britain", "great-britain-g2-v1", geographic_config=fixture["config"],
        )


def test_existing_uk_ireland_astronomical_manifest_and_g2_source_hash_are_retained():
    from src.elevation import ElevationSampler

    manifest = ROOT / "data/elevation/manifests/uk_ireland.json"
    if not manifest.is_file() or not (ROOT / "input/elevation").is_dir():
        pytest.skip("Retained UK/Ireland astronomical DEM cache is not present")
    expected = "2462f8b18874115b69f531088b016d504698b6628619b52ffd022198c32759fd"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == expected
    with ElevationSampler(manifest, ROOT / "input/elevation") as sampler:
        assert len(sampler.tiles) == 56


def _baseline_copy(tmp_path):
    if not G2_BASELINE.is_dir() or not G2_INVENTORY.is_file():
        pytest.skip("Retained approved G2 baseline package and trusted inventory are not present")
    root = tmp_path / "baseline"
    shutil.copytree(G2_BASELINE, root)
    for directory in [root, *(path for path in root.rglob("*") if path.is_dir())]:
        directory.chmod(0o755)
    inventory = tmp_path / "approved-inventory.json"
    shutil.copyfile(G2_INVENTORY, inventory)
    return root, root / "spots", inventory


def _update_local_manifest(root, relative, payload):
    path = root / relative
    path.chmod(0o644)
    path.write_bytes(payload)
    manifest_path = root / "baseline.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][relative]["size"] = len(payload)
    manifest["files"][relative]["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(manifest))


def test_approved_baseline_authenticates_full_1217_spots_tiles_and_clusters():
    if not G2_BASELINE.is_dir() or not G2_INVENTORY.is_file():
        pytest.skip("Retained approved G2 baseline package and trusted inventory are not present")
    baseline = validate_approved_g2_baseline(G2_BASELINE / "spots", G2_INVENTORY)
    assert baseline["commit"] == "312bc366ccef674a232bde2f0a3a42067d083d19"
    assert baseline["inventory_sha256"] == APPROVED_G2_INVENTORY_SHA256
    assert len(baseline["g2_spots"]) == APPROVED_G2_SPOT_COUNT == 1217
    assert len(baseline["spot_tiles"]) == 136
    assert len(baseline["cluster_files"]) == 7
    assert len({spot["id"] for spot in baseline["g2_spots"]}) == 1217


def test_truncated_spot_tile_attack_fails_before_dem_sampler(tmp_path, monkeypatch):
    from tools import prepare_elevation

    root, spots_dir, inventory = _baseline_copy(tmp_path)
    relative = next(path for path in sorted(root.glob("spots/*.json"))
                    if any(spot.get("country") in {"GB", "IE"}
                           for spot in json.loads(path.read_text())["spots"]))
    relative_name = relative.relative_to(root).as_posix()
    envelope = json.loads(relative.read_text())
    envelope["spots"] = [spot for spot in envelope["spots"] if spot.get("country") in {"GB", "IE"}][:1]
    _update_local_manifest(root, relative_name, json.dumps(envelope).encode())
    sampler_calls = []
    monkeypatch.setattr(prepare_elevation, "ElevationSampler",
                        lambda *args, **kwargs: sampler_calls.append((args, kwargs)))
    with pytest.raises(ValueError, match="trusted source inventory"):
        prepare_elevation.validate_accessibility_halo_samples(
            "unused-dem.json", tmp_path, spots_dir, inventory,
        )
    cache_dir = tmp_path / "staged-cache"
    manifest_dir = tmp_path / "staged-manifests"
    with pytest.raises(ValueError, match="trusted source inventory"):
        prepare_elevation.prepare_accessibility_halo(
            None, None, cache_dir, manifest_dir, "2024_1",
            baseline_spots_dir=spots_dir, baseline_inventory_path=inventory,
        )
    assert sampler_calls == []
    assert not cache_dir.exists()
    assert not manifest_dir.exists()


@pytest.mark.parametrize("attack", ["missing_tile", "modified_property", "modified_hash", "missing_spot"])
def test_approved_baseline_rejects_incomplete_or_altered_tiles(tmp_path, attack):
    root, spots_dir, inventory = _baseline_copy(tmp_path)
    tile_path = next(path for path in sorted(root.glob("spots/*.json"))
                     if any(spot.get("country") in {"GB", "IE"}
                            for spot in json.loads(path.read_text())["spots"]))
    relative = tile_path.relative_to(root).as_posix()
    if attack == "missing_tile":
        tile_path.chmod(0o644)
        tile_path.unlink()
        with pytest.raises(ValueError, match="file set differs"):
            validate_approved_g2_baseline(spots_dir, inventory)
    elif attack == "modified_hash":
        baseline_path = root / "baseline.json"
        baseline_path.chmod(0o644)
        data = json.loads(baseline_path.read_text())
        data["files"][relative]["sha256"] = "0" * 64
        baseline_path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="contradicts trusted source inventory"):
            validate_approved_g2_baseline(spots_dir, inventory)
    else:
        envelope = json.loads(tile_path.read_text())
        if attack == "modified_property":
            spot = next(spot for spot in envelope["spots"] if spot.get("country") in {"GB", "IE"})
            spot["name"] = str(spot.get("name", "")) + " altered"
        else:
            envelope["spots"] = [spot for spot in envelope["spots"]
                                 if spot.get("country") not in {"GB", "IE"}]
        _update_local_manifest(root, relative, json.dumps(envelope).encode())
        with pytest.raises(ValueError, match="trusted source inventory"):
            validate_approved_g2_baseline(spots_dir, inventory)


def test_approved_baseline_rejects_tampered_inventory_even_when_locally_consistent(tmp_path):
    root, spots_dir, inventory = _baseline_copy(tmp_path)
    tile = next(path for path in sorted(root.glob("spots/*.json")))
    envelope = json.loads(tile.read_text())
    envelope["marker"] = "locally self-authorized"
    relative = tile.relative_to(root).as_posix()
    _update_local_manifest(root, relative, json.dumps(envelope).encode())
    trusted = json.loads(inventory.read_text())
    trusted["files"][relative]["size"] = (root / relative).stat().st_size
    trusted["files"][relative]["sha256"] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    inventory.write_text(json.dumps(trusted))
    with pytest.raises(ValueError, match="inventory SHA-256 mismatch"):
        validate_approved_g2_baseline(spots_dir, inventory)


def test_duplicate_published_ids_are_rejected_by_baseline_parser():
    from src.accessibility_baseline import _parse_published_spots

    payload = json.dumps({"spots": [
        {"id": "repeat", "country": "GB", "lat": 51, "lon": -1},
        {"id": "repeat", "country": "IE", "lat": 52, "lon": -7},
    ]}).encode()
    with pytest.raises(ValueError, match="Duplicate published spot ID"):
        _parse_published_spots({"spots/test.json": payload}, 2)


def test_schema_and_manifest_policy_mismatch_is_explicit(tmp_path):
    from src.accessibility_scope import GeographicSpecification

    canary = GeographicSpecification.load(ROOT / "config/accessibility.yaml", "canaries")
    assert canary.dem_manifest_policy is None
    g2 = GeographicSpecification.load(ROOT / "config/accessibility.yaml", "great_britain")
    assert set(map(tuple, g2.dem_manifest_policy["required_additional_cells"])) == {
        (55, longitude) for longitude in range(-9, -1)
    }
    data = yaml.safe_load((ROOT / "config/accessibility.yaml").read_text())
    data["scopes"]["canaries"]["dem_manifest_policy"] = "uk_ireland_accessibility_g2"
    bad = tmp_path / "accessibility.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown or contradictory DEM manifest policy"):
        GeographicSpecification.load(bad, "canaries")

    wrong_dem = tmp_path / "uk-only.json"
    wrong_dem.write_text(json.dumps({
        "schema": 1, "region": "uk_ireland-accessibility-g2", "tiles": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="requires DEM region canaries"):
        validate_accessibility_dem_manifest(
            wrong_dem, tmp_path, "canaries", "canary-islands-g1-v1",
        )
    with pytest.raises(ValueError, match="scope/profile"):
        validate_accessibility_dem_manifest(
            wrong_dem, tmp_path, "great_britain", "canary-islands-g1-v1",
        )

"""Controlled pipeline integration for two-scope accessibility enrichment."""

from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import numpy as np

from src.cli import parse_args
from src.tile_export import write_tile_file as real_write_tile_file
from src.accessibility import PUBLIC_CLASSES


def test_uk_ireland_pipeline_routes_accessibility_and_closes_both_samplers(tmp_path, monkeypatch):
    import run as pipeline

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_path = input_dir / "uk_ireland" / "2025.tif"
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"controlled raster fixture")
    args = parse_args([
        "--year", "2025", "--region", "uk_ireland",
        "--data-repo-url", "git@example:user/data.git", "--no-push", "--no-clusters",
        "--input-dir", str(input_dir), "--output-dir", str(output_dir),
        "--accessibility-dataset", "great_britain=gb-manifest.json",
        "--accessibility-dataset", "ireland_northern_ireland=ie-manifest.json",
    ])

    region = {
        "name": "UK and Ireland", "bbox": [-11, 49, 2, 55],
        "equal_area_epsg": 3035, "osm_country_code": ["GB", "IE"],
    }
    monkeypatch.setattr(pipeline, "get_region", lambda _name: region)
    monkeypatch.setattr(pipeline, "load_regions", lambda: {"uk_ireland": region})
    monkeypatch.setattr(pipeline, "build_region_ownerships", lambda _regions: {"uk_ireland": object()})
    monkeypatch.setattr(pipeline, "_validate_geonames_inputs", lambda _codes: None)
    monkeypatch.setattr(pipeline, "enumerate_tiles_in_bbox", lambda *_args: [])
    monkeypatch.setattr(pipeline, "slice_and_compute", lambda *_args, **_kwargs: SimpleNamespace(
        data=np.ones((2, 2)), transform=object(), crs="EPSG:3035",
    ))
    monkeypatch.setattr(pipeline, "alr_to_darkness", lambda _data: np.ones((2, 2)))
    monkeypatch.setattr(pipeline, "alr_to_bortle", lambda _data: np.full((2, 2), 3))
    monkeypatch.setattr(pipeline, "CandidateQualifier", lambda *_args, **_kwargs: SimpleNamespace(counts=Counter()))
    candidates = [
        {"lat": 54.5, "lon": -6.0, "country": "GB", "row": 0, "col": 0},
        {"lat": 52.0, "lon": -2.0, "country": "IE", "row": 1, "col": 1},
    ]
    monkeypatch.setattr(pipeline, "mesh_darkest", lambda *_args, **_kwargs: [dict(item) for item in candidates])
    monkeypatch.setattr(pipeline, "classify_candidates", lambda items, *_args, **_kwargs: (items, {}))
    monkeypatch.setattr(pipeline, "redundancy_filter", lambda items, *_args, **_kwargs: items)
    monkeypatch.setattr(pipeline, "load_places", lambda _region: [])
    monkeypatch.setattr(pipeline, "ensure_coverage", lambda items, *_args, **_kwargs: items)
    monkeypatch.setattr(pipeline, "attach_near_town", lambda items, _places: items)

    class Sampler:
        def __init__(self, manifest_path, _cache_dir):
            self.manifest_path = Path(manifest_path)
            self.counts = {}
            self.wbm_io = {}
            self.closed = False
            samplers.append(self)

        def sample(self, _lat, _lon):
            return 100

        def wbm_classes(self, lats, _lons):
            return np.zeros(len(lats), dtype=np.int16)

        def close(self):
            self.closed = True

    samplers = []
    monkeypatch.setattr(pipeline, "ElevationSampler", Sampler)

    class GeographicDataset:
        is_e3 = True

        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def require_region(self, *_args):
            return None

        def close(self):
            self.closed = True

    geographic_datasets = []

    def geographic_factory(*args, **kwargs):
        dataset = GeographicDataset(*args, **kwargs)
        geographic_datasets.append(dataset)
        return dataset

    monkeypatch.setattr(pipeline, "GeographicDataset", geographic_factory)

    class NamingIndex:
        def enrich_spots(self, spots):
            return [dict(spot, name="Fixture", nameDistanceKm=1.0,
                         nameFeatureCode="PPL", nameFeatureClass="P", nameGeoNameId=1)
                    for spot in spots]

    monkeypatch.setattr(pipeline, "_load_geonames_index", lambda *_args: NamingIndex())

    class Resolver:
        def __init__(self, datasets, elevation_sampler, **_kwargs):
            self.datasets = datasets
            self.elevation_sampler = elevation_sampler
            self.closed = False
            self.audit_calls = []
            self.indices = {
                scope: SimpleNamespace(manifest={"dataset_id": source_id})
                for scope, source_id in (("great_britain", "gb-source"),
                                         ("ireland_northern_ireland", "ie-source"))
            }

        def audit_assignments(self, spots, *, enriched=False):
            self.audit_calls.append((len(spots), enriched))
            for spot in spots:
                if enriched:
                    expected = "ie-source" if float(spot["lon"]) < -5.5 else "gb-source"
                    assert spot["accessibility"]["source"] == expected
            return {"assigned": len(spots), "enriched": len(spots) if enriched else 0,
                    "excluded": 0, "missing": 0,
                    "by_scope": {scope: {"assigned": 1, "enriched": 1 if enriched else 0}
                                 for scope in self.indices}}

        def enrich_spot(self, spot):
            source = "ie-source" if float(spot["lon"]) < -5.5 else "gb-source"
            evidence = {"schema": 1, "source": source, "radiusM": 1000}
            evidence.update({name: {"status": "not_found_within_radius"}
                             for name in PUBLIC_CLASSES})
            return dict(spot, accessibility=evidence)

        def close(self):
            self.closed = True

    resolvers = []

    def resolver_factory(*args, **kwargs):
        resolver = Resolver(*args, **kwargs)
        resolvers.append(resolver)
        return resolver

    monkeypatch.setattr("src.accessibility_resolver.AccessibilityResolver", resolver_factory)
    monkeypatch.setattr(
        "src.accessibility_scope.GeographicSpecification.load",
        lambda _path, _scope: SimpleNamespace(
            dem_manifest="data/elevation/manifests/uk_ireland-accessibility-g2.json",
        ),
    )
    dem_validation = MagicMock(return_value={"region": "uk_ireland-accessibility-g2"})
    monkeypatch.setattr(
        "src.accessibility_dem.validate_accessibility_dem_manifest", dem_validation,
    )
    source_records = []
    def write_source_record(directory, manifest):
        directory = Path(directory)
        source_id = manifest["dataset_id"]
        source = directory / "accessibility" / "sources" / f"{source_id}.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({"schema": 1, "source_id": source_id}))
        source_records.append((directory, source_id))
        return source
    monkeypatch.setattr(
        "src.accessibility_prepare.write_accessibility_source_record",
        write_source_record,
    )
    monkeypatch.setattr(pipeline, "classify_spots_into_tiles", lambda spots, _size: {"N054W006": spots})
    monkeypatch.setattr(pipeline, "compute_new_version", lambda *_args: ("2025.1", True))
    monkeypatch.setattr(pipeline, "merge_tile_envelopes", lambda _old, new, _ownership: new)
    monkeypatch.setattr(pipeline, "write_tile_file", real_write_tile_file)

    assert pipeline.run(args) == 0
    dem_validation.assert_called_once_with(
        "data/elevation/manifests/uk_ireland-accessibility-g2.json", args.dem_cache_dir,
        "great_britain", "great-britain-g2-v1",
        geographic_config="config/accessibility.yaml",
    )

    assert [sampler.manifest_path.as_posix() for sampler in samplers] == [
        (Path(args.dem_manifest_dir) / "uk_ireland.json").as_posix(),
        "data/elevation/manifests/uk_ireland-accessibility-g2.json",
    ]
    assert all(sampler.closed for sampler in samplers)
    assert geographic_datasets[0].closed
    assert resolvers[0].datasets == args.accessibility_datasets
    assert resolvers[0].elevation_sampler is samplers[1]
    assert resolvers[0].audit_calls == [(2, False), (2, True)]
    assert {source for _directory, source in source_records} == {"gb-source", "ie-source"}
    assert all("attempts" in directory.parts and "package" in directory.parts
               for directory, _source in source_records)

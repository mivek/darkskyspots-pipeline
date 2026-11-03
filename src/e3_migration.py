"""Coordinated offline E3 generation and one complete immutable snapshot.

No push, commit or apply-to-source operation exists in this module.
"""

import json
from pathlib import Path
import subprocess

import numpy as np
import rasterio

from .geographic_data import GeographicDataset
from .geographic_publication import (
    approve_tree,
    verify_approval,
    GeographicApprovalError,
    git,
)
from .geographic_confidence import priority
from .selection import CandidateQualifier
from .extract import mesh_darkest, redundancy_filter
from .coverage import load_places, ensure_coverage, attach_near_town
from .elevation import ElevationSampler, sha256
from .geonames import GeoNamesIndex, NAMING_FEATURE_CODES
from .enrich import enrich_all
from .tile_export import classify_spots_into_tiles, write_tile_file
from .regions import load_regions, build_region_ownerships
from .publish import merge_publication_envelopes, compute_new_version
from .clusters import write_cluster_files


def frozen_region(
    name,
    region,
    arrays,
    dataset,
    dem_manifest,
    dem_cache,
    *,
    year,
    generated,
    trace=None,
):
    """Use original frozen ALR-derived arrays with the production operators."""
    arrays = Path(arrays)
    meta = json.loads((arrays / "metadata.json").read_text())
    for key in ["darkness", "bortle"]:
        if sha256(arrays / f"{key}.npy") != meta[key + "_sha256"]:
            raise ValueError("Corrupt frozen simulation array")
    if meta["crs"] != "EPSG:4326":
        raise ValueError("Frozen mesh requires WGS84 VIIRS")
    darkness = np.load(arrays / "darkness.npy", mmap_mode="r")
    bortle = np.load(arrays / "bortle.npy", mmap_mode="r")
    transform = rasterio.Affine(*meta["transform"])
    with ElevationSampler(dem_manifest, dem_cache) as sampler:
        qualifier = CandidateQualifier(
            region, transform, meta["crs"], sampler, reviewed_water=dataset
        )
        selected = mesh_darkest(
            darkness, transform, qualify=qualifier, priority_key=priority
        )
        for p in selected:
            p["bortle"] = int(bortle[p["row"], p["col"]])
        dedup = redundancy_filter(selected, priority_key=priority)
        places = load_places(region)
        covered = ensure_coverage(dedup, selected, places, priority_key=priority)
        if trace is not None:
            trace.update(
                selected=[dict(p) for p in selected],
                deduplicated=[dict(p) for p in dedup],
                covered=[dict(p) for p in covered],
            )
        attach_near_town(covered, places)
        naming = GeoNamesIndex.from_filtered_extracts(
            data_dir="data",
            countries=region["osm_country_code"],
            bbox=region["bbox"],
            feature_codes=NAMING_FEATURE_CODES,
        )
        spots = enrich_all(naming.enrich_spots(covered), elevation_sampler=sampler)
    return {
        tile: dict(
            tile=tile,
            spots=points,
            source=f"VIIRS/{year}/{name}",
            version=f"{year}.0",
            generated=generated,
        )
        for tile, points in classify_spots_into_tiles(spots).items()
    }


def assemble_snapshot(
    source,
    reference,
    regional,
    output,
    dataset,
    *,
    year,
    generated,
    regions=None,
    report_dir=None,
):
    """Merge all ownership blocks, clusters, then audit ONE final Git tree."""
    dataset.require_e3()
    regions = regions or load_regions()
    if set(regional) != set(regions) or set(regions) != set(dataset.regions):
        raise ValueError("Coordinated E3 migration requires every configured region")
    source = Path(source).resolve()
    output = Path(output).resolve()
    if (
        output.is_relative_to(source)
        or source.is_relative_to(output)
        or output.exists()
    ):
        raise ValueError(
            "Snapshot must use a new directory separate from the source repository"
        )
    before = git(source, "status", "--porcelain")
    head = git(source, "rev-parse", "HEAD")
    commit = git(source, "rev-parse", reference + "^{commit}").decode().strip()
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(source), str(output)],
        check=True,
    )
    git(output, "checkout", "--quiet", commit)
    # Prevent any accidental network publication from this migration copy.
    git(output, "remote", "remove", "origin")
    original = {
        p.stem: json.loads(p.read_text()) for p in (output / "spots").glob("*.json")
    }
    merged = original
    ownerships = build_region_ownerships(regions)
    for name in sorted(regions):
        merged = merge_publication_envelopes(merged, regional[name], ownerships[name])
    version, _ = compute_new_version(original, merged, year)
    for path in (output / "spots").glob("*.json"):
        if path.stem not in merged:
            path.unlink()
    for tile, env in merged.items():
        write_tile_file(
            tile,
            env["spots"],
            output,
            version,
            env.get("source", f"VIIRS/{year}"),
            generated,
        )
    write_cluster_files(
        output / "spots", output / "clusters", data_year=year, generated=generated
    )
    git(output, "add", "spots", "clusters")
    try:
        report = approve_tree(output, dataset=dataset, report_dir=report_dir)
        verify_approval(output, report, dataset)
    finally:
        if (
            git(source, "status", "--porcelain") != before
            or git(source, "rev-parse", "HEAD") != head
        ):
            raise ValueError("Source repository changed during coordinated migration")
    return report

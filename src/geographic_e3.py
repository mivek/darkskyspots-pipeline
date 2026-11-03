"""Shared indexed E3 evidence and final-only, reason-scoped adjudication."""

from collections import Counter
import hashlib
import json
import math
import time
import weakref

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window
from shapely.geometry import Point, box, mapping
from shapely.ops import unary_union

from .geographic_confidence import confidence
from .geographic_geometry import canonical, longitude, contains
from .geographic_index import GeographicIndex
from .geographic_data import (
    digest,
    geometry_digest,
    valid_geometry,
    is_fingerprint,
    E3_POLICY,
)
from .worldcover import WorldCover
from .elevation import ElevationSampler

B1_REASONS = frozenset(
    {
        "peatland_meadow_or_fen_uncertainty",
        "WorldCover_unavailable_or_partial",
        "incomplete_hydrographic_collection",
    }
)
B2_REASONS = frozenset(
    {
        "coastal_support_disagreement",
        "mapped_surface_water_disagreement",
        "higher_risk_or_untyped_wetland",
        "explicit_tidal_influence",
        "WorldCover_aquatic_or_inundation_disagreement",
    }
)
GAP_REASONS = B1_REASONS - {"peatland_meadow_or_fen_uncertainty"}
BATCH_SIZE = 4096


class E3Evidence:
    def __init__(self, dataset):
        self.dataset = dataset
        key = dataset.manifest.get("worldcover")
        if not key or key not in dataset.entries:
            raise ValueError("Missing verified WorldCover reference")
        self.worldcover = WorldCover(dataset.entries[key].parent)
        self.index = None
        self.region = None
        self.metrics = Counter()
        self.samplers = weakref.WeakKeyDictionary()

    def for_region(self, name):
        if self.region != name:
            if self.index:
                self.index.close()
            entry = self.dataset.regions[name].get("index")
            if entry not in self.dataset.entries:
                raise ValueError(f"Missing E3 index: {name}")
            self.index = GeographicIndex(self.dataset.entries[entry])
            self.region = name
        return self.index

    def close(self):
        if self.index:
            self.index.close()
            self.index = None
        self.worldcover.close()

    def classify(self, candidates, region, sampler):
        if self.samplers.get(sampler) != region:
            key = self.dataset.manifest.get("terrain", {}).get(region)
            if key not in self.dataset.entries:
                raise ValueError(f"Missing verified WBM terrain reference: {region}")
            terrain = json.loads(self.dataset.entries[key].read_text())
            if terrain.get("region") != region:
                raise ValueError(f"WBM terrain region mismatch: {region}")
            expected = frozenset(
                (
                    int(t["lat"]),
                    int(t["lon"]),
                    t["product"],
                    t["wbm"]["size"],
                    t["wbm"]["sha256"],
                )
                for t in terrain["tiles"]
            )
            if sampler.wbm_sources != expected:
                raise ValueError(
                    "WBM generation inputs differ from the approved geographic package"
                )
            self.samplers[sampler] = region
        result = []
        index = self.for_region(region)
        for offset in range(0, len(candidates), BATCH_SIZE):
            start = time.perf_counter()
            batch = candidates[offset : offset + BATCH_SIZE]
            lat = [p["lat"] for p in batch]
            lon = [longitude(p["lon"]) for p in batch]
            values = sampler.wbm_classes(lat, lon)
            if (
                values is None
                or len(values) != len(batch)
                or any(v not in {-1, 0, 1, 2, 3} for v in values)
            ):
                raise ValueError("Invalid WBM audit samples")
            bounds = sampler.wbm_supports(lat, lon)
            supports = [canonical(box(*b)) for b in bounds]
            entry = self.dataset.regions[region]
            prepared = (
                canonical(valid_geometry(entry["evidence_domain"], polygon=True))
                if "evidence_domain" in entry
                else entry["domain"]
            )
            if any(not prepared.covers(g) for g in supports):
                raise ValueError(
                    "Native WBM support leaves verified evidence domain; prepare complete boundary evidence"
                )
            self.metrics["wbm_seconds"] += time.perf_counter() - start
            start = time.perf_counter()
            evidence = index.observe(batch, supports)
            self.metrics["vector_seconds"] += time.perf_counter() - start
            start = time.perf_counter()
            wc = self.worldcover.sample(
                [
                    dict(point=dict(p, lon=x), wbm_support=b)
                    for p, x, b in zip(batch, lon, bounds)
                ]
            )
            self.metrics["WorldCover_seconds"] += time.perf_counter() - start
            start = time.perf_counter()
            water = self.dataset.water_matches(batch)
            for i, (p, ev, value, b, w) in enumerate(
                zip(batch, evidence, values, bounds, wc)
            ):
                point = Point(longitude(p["lon"]), p["lat"])
                applicable = [
                    d
                    for d in self.dataset.decisions
                    if contains(d["extent_geometry"], point)
                ]
                reasons = []
                if any(d["conclusion"] == "hold" for d in applicable):
                    reasons.append("explicit_review_hold")
                if value != 0:
                    reasons.append("WBM_NoData" if value == -1 else "WBM_water")
                if i in water:
                    reasons.append("reviewed_high_confidence_water")
                ev.update(
                    wbm_class=int(value),
                    wbm_support=list(b),
                    reviewed_water=water.get(i, []),
                    reasons=reasons,
                )
                c = confidence(ev, w)
                result.append(
                    dict(
                        spot=dict(p),
                        region=region,
                        confidence=c["level"],
                        reasons=c["reasons"],
                        base_evidence=ev,
                        worldcover=w,
                    )
                )
            self.metrics["classification_seconds"] += time.perf_counter() - start
            self.metrics["points"] += len(batch)
        return result


def local_context(dataset, extent, region, *, water_exclusion=False):
    """Fingerprint local authoritative evidence, not unrelated archive bytes.

    Native WC pixels touching the extent are evidence within that extent.
    Pixels wholly outside its shape (including holes) are not fingerprinted.
    """
    if dataset._e3_engine is None:
        dataset._e3_engine = E3Evidence(dataset)
    engine = dataset._e3_engine
    index = engine.for_region(region)
    parts = []
    for identifier in sorted({identifier for _, identifier in index.query([extent])}):
        clipped, prop = index.clipped(identifier,extent)
        if clipped.is_empty:
            continue
        identity = {
            k: prop[k]
            for k in [
                "kind",
                "class",
                "source",
                "id",
                "tidal",
                "intermittent",
                "wetland",
                "water",
            ]
            if k in prop
        }
        parts.append((identity, geometry_digest(clipped)))
    if water_exclusion:
        # Preserve existing reviewed exclusions on unchanged source geometry.
        # New complementary evidence cannot turn a water exclusion into a waiver.
        return digest(
            sorted(set((p.get("kind"), p.get("class", ""), g) for p, g in parts))
        )
    pixels = []
    wc = engine.worldcover
    for name, info in sorted(wc.manifest["tiles"].items()):
        ds = wc.ds(name)
        for piece in extent.geoms if extent.geom_type == "MultiPolygon" else [extent]:
            if not box(*ds.bounds).intersects(piece):
                continue
            crop = piece.intersection(box(*ds.bounds))
            if crop.is_empty:
                continue
            w, s, e, n = crop.bounds
            inv = ~ds.transform
            c0, r0 = inv * (w, n)
            c1, r1 = inv * (e, s)
            c0 = max(0, math.floor(c0) - 1)
            r0 = max(0, math.floor(r0) - 1)
            c1 = min(ds.width, math.ceil(c1) + 1)
            r1 = min(ds.height, math.ceil(r1) + 1)
            for r in range(r0, r1, 256):
                for c in range(c0, c1, 256):
                    window = Window(c, r, min(256, c1 - c), min(256, r1 - r))
                    mask = geometry_mask(
                        [mapping(crop)],
                        out_shape=(int(window.height), int(window.width)),
                        transform=ds.window_transform(window),
                        all_touched=True,
                        invert=True,
                    )
                    if not np.any(mask):
                        continue
                    a = ds.read(1, window=window)
                    # Missing or novel categories are never reviewed by an
                    # unrelated acceptance; pixel values and mask both bind.
                    payload = np.where(mask, a, 255).astype("uint8").tobytes()
                    pixels.append(
                        (
                            name,
                            list(ds.transform)[:6],
                            c,
                            r,
                            int(window.width),
                            int(window.height),
                            hashlib.sha256(payload).hexdigest(),
                        )
                    )
    terrain = dataset.manifest.get("terrain", {}).get(region)
    if terrain not in dataset.entries:
        raise ValueError("Missing WBM review context")
    terrain_document = json.loads(dataset.entries[terrain].read_text())
    wbm = []
    seen = set()
    for tile in terrain_document["tiles"]:
        # Review only relevant source cells. WBM files can overlap at their
        # native edge; keep their original grids and local pixel contents.
        cell = box(tile["lon"], tile["lat"], tile["lon"] + 1, tile["lat"] + 1)
        if not cell.intersects(extent):
            continue
        path = (dataset.path.parent / tile["wbm"]["path"]).resolve()
        if path in seen:
            continue
        seen.add(path)
        with rasterio.open(path) as ds:
            for piece in (
                extent.geoms if extent.geom_type == "MultiPolygon" else [extent]
            ):
                crop = piece.intersection(box(*ds.bounds))
                if crop.is_empty:
                    continue
                w, s, e, n = crop.bounds
                inv = ~ds.transform
                c0, r0 = inv * (w, n)
                c1, r1 = inv * (e, s)
                c0 = max(0, math.floor(c0) - 1)
                r0 = max(0, math.floor(r0) - 1)
                c1 = min(ds.width, math.ceil(c1) + 1)
                r1 = min(ds.height, math.ceil(r1) + 1)
                for r in range(r0, r1, 256):
                    for c in range(c0, c1, 256):
                        window = Window(c, r, min(256, c1 - c), min(256, r1 - r))
                        mask = geometry_mask(
                            [mapping(crop)],
                            out_shape=(int(window.height), int(window.width)),
                            transform=ds.window_transform(window),
                            all_touched=True,
                            invert=True,
                        )
                        if not np.any(mask):
                            continue
                        a = ds.read(1, window=window, masked=True)
                        payload = (
                            np.where(mask, a.filled(255), 255).astype("uint8").tobytes()
                        )
                        wbm.append(
                            (
                                tile["product"],
                                str(ds.crs),
                                list(ds.transform)[:6],
                                c,
                                r,
                                hashlib.sha256(payload).hexdigest(),
                            )
                        )
    gaps = []
    for gap in dataset.manifest.get("availability", []):
        g = canonical(valid_geometry(gap["extent"], polygon=True))
        if g.intersects(extent):
            gaps.append(dict(gap, extent=mapping(g.intersection(extent))))
    return digest(
        dict(
            extent=geometry_digest(extent),
            features=sorted(parts, key=lambda p: json.dumps(p, sort_keys=True)),
            WorldCover=pixels,
            coverage=wc.support,
            WBM=sorted(wbm),
            availability=gaps,
        )
    )


def reviewed_contexts(dataset, reviews):
    result = []
    for d in reviews["decisions"]:
        extent = canonical(valid_geometry(d["extent"], polygon=True))
        if d["region"] not in dataset.regions or not dataset.regions[d["region"]][
            "domain"
        ].covers(extent):
            raise ValueError("E3 review outside regional domain")
        ctx = local_context(
            dataset,
            extent,
            d["region"],
            water_exclusion=d["conclusion"] == "corroborated_water",
        )
        if d["conclusion"] == "corroborated_water":
            index = dataset._e3_engine.for_region(d["region"])
            land = []
            water = []
            for identifier in {identifier for _, identifier in index.query([extent])}:
                g, prop = index.clipped(identifier,extent)
                if prop["kind"] == "marine_land":
                    land.append(g)
                elif prop["kind"] == "water":
                    water.append(g)
            supported = extent.difference(unary_union(land)).union(unary_union(water))
            if not supported.covers(extent):
                raise ValueError("Exclusion extends outside source water geometry")
        entries = d.get("acceptances", d.get("resolutions", []))
        result.append(
            dict(
                id=d["id"],
                context_sha256=ctx,
                extent_sha256=geometry_digest(extent),
                acceptance_contexts={
                    a["reason"]: digest(dict(reason=a["reason"], context=ctx))
                    for a in entries
                },
            )
        )
    return result


def validate_degraded_document(document, regions):
    if document.get("schema") != 1 or document.get("policy") != E3_POLICY:
        raise ValueError("Invalid degraded-evidence policy schema")
    seen = set()
    for p in document.get("policies", []):
        if not p.get("id") or p["id"] in seen:
            raise ValueError("Duplicate/invalid degraded policy identifier")
        seen.add(p["id"])
        if not all(
            p.get(k)
            for k in [
                "version",
                "approved_by",
                "approval_reference",
                "documented_limitation",
                "provenance",
            ]
        ):
            raise ValueError(
                "Degraded-evidence policy requires explicit reviewed authorization"
            )
        if (
            p.get("region") not in regions
            or not set(p.get("reasons", []))
            or not set(p["reasons"]) <= GAP_REASONS
        ):
            raise ValueError("Invalid degraded policy reasons or region")
        extent = canonical(valid_geometry(p["extent"], polygon=True))
        if not regions[p["region"]]["domain"].covers(extent):
            raise ValueError("Degraded policy outside region")
        if not is_fingerprint(p.get("context_sha256")):
            raise ValueError("Degraded policy requires reviewed context fingerprint")


def audit(dataset, spots):
    findings = []
    owners = {name: [] for name in dataset.regions}
    for p in spots:
        point = Point(longitude(float(p["lon"])), float(p["lat"]))
        names = [
            name
            for name, r in dataset.regions.items()
            if p.get("country") in r["countries"] and r["domain"].covers(point)
        ]
        if len(names) != 1:
            raise ValueError(
                "Publication spot has no unique configured geographic domain"
            )
        owners[names[0]].append(p)
    for name, points in owners.items():
        if not points:
            continue
        terrain = dataset.manifest.get("terrain", {}).get(name)
        if terrain not in dataset.entries:
            raise ValueError(f"Missing verified WBM terrain reference: {name}")
        path = dataset.entries[terrain]
        if json.loads(path.read_text()).get("region") != name:
            raise ValueError(f"WBM terrain region mismatch: {name}")
        with ElevationSampler.water_masks(path, dataset.path.parent) as sampler:
            findings.extend(dataset.classify(points, name, sampler))
    degraded_key = dataset.manifest.get("degraded_policies")
    if degraded_key not in dataset.entries:
        raise ValueError("Missing explicit degraded-evidence policy inventory")
    doc = json.loads(dataset.entries[degraded_key].read_text())
    validate_degraded_document(doc, dataset.regions)
    policies = doc.get("policies", [])
    counts = Counter()
    for f in findings:
        p = f["spot"]
        point = Point(longitude(p["lon"]), p["lat"])
        support = canonical(box(*f["base_evidence"]["wbm_support"]))
        applicable = [
            d for d in dataset.decisions if contains(d["extent_geometry"], point)
        ]
        held = any(d["conclusion"] == "hold" for d in applicable)
        stale = [d["id"] for d in applicable if not d["active"]]
        accepted = set()
        resolved = set()
        used = []
        for d in applicable:
            if not d["active"] or not d["extent_geometry"].covers(support):
                continue
            field = (
                "resolutions" if d["conclusion"] == "resolve_risk" else "acceptances"
            )
            if d["conclusion"] not in {"resolve_risk", "accept_uncertainty"}:
                continue
            for a in d.get(field, []):
                if field=='resolutions':
                    from .geographic_data import validate_positive_resolution
                    if dataset.review_document.get('schema')!=4:
                        raise ValueError('B2 resolutions require explicit renewal under review schema 4')
                    validate_positive_resolution(a)
                expected = (
                    dataset.manifest.get("acceptance_contexts", {})
                    .get(d["id"], {})
                    .get(a["reason"])
                )
                if a["context_sha256"] != expected:
                    continue
                (resolved if field == "resolutions" else accepted).add(a["reason"])
                used.append(d["id"])
        reasons = set(f["reasons"])
        satisfied = accepted | resolved
        # Regional policies can only cover the ENTIRE B1 reason set when it
        # consists solely of documented legitimate complementary gaps.
        if f["confidence"] == "B1" and reasons and reasons <= GAP_REASONS:
            legitimate = True
            if "WorldCover_unavailable_or_partial" in reasons:
                legitimate = (
                    f["worldcover"]["unavailable_reason"] == "outside_product_support"
                )
                if not legitimate:
                    legitimate = any(
                        g.get("source") == "WorldCover"
                        and g.get("reason") == "valid_upstream_NoData"
                        and g.get("evidence")
                        and canonical(valid_geometry(g["extent"], polygon=True)).covers(
                            support
                        )
                        for g in dataset.manifest.get("availability", [])
                    )
            if "incomplete_hydrographic_collection" in reasons:
                gaps = dataset.manifest.get("availability", [])
                legitimate = legitimate and any(
                    g.get("source") == "hydrography"
                    and g.get("reason") == "upstream_coverage_gap"
                    and g.get("evidence")
                    and canonical(valid_geometry(g["extent"], polygon=True)).covers(
                        support
                    )
                    for g in gaps
                )
            for policy in policies:
                extent = canonical(valid_geometry(policy["extent"], polygon=True))
                if (
                    legitimate
                    and policy["region"] == f["region"]
                    and extent.covers(support)
                    and contains(extent, point)
                    and reasons <= set(policy["reasons"])
                ):
                    expected = dataset.manifest.get("degraded_contexts", {}).get(
                        policy["id"]
                    )
                    if policy["context_sha256"] == expected:
                        satisfied |= reasons
                        used.append(policy["id"])
        status = "no_additional_objection"
        if f["confidence"] == "C":
            status = "reject_water"
        elif held or stale or reasons - satisfied:
            status = "hold"
        elif f["confidence"] == "B1":
            status = "accepted_uncertainty"
        elif f["confidence"] == "B2":
            status = "resolved_risk"
        f.update(
            status=status,
            accepted_reasons=sorted(reasons & satisfied),
            unreviewed_reasons=sorted(reasons - satisfied),
            decisions=sorted(set(used)),
            stale_decisions=stale,
        )
        counts[status] += 1
    return dict(
        status="blocked" if counts["hold"] or counts["reject_water"] else "passed",
        counts=dict(counts),
        final_spot_count=len(spots),
        findings=findings,
        geographic_revision=dataset.revision,
        decisions_revision=dataset.decisions_revision,
        policy=E3_POLICY,
        limitations="Confidence is evidence-relative; no dry-ground, accessibility or safety guarantee.",
    )

"""Reproducible offline E3 package preparation from frozen source assets."""

import json
from pathlib import Path
import shutil
import tempfile

from shapely.geometry import shape
from .geographic_data import (
    GeographicDataset,
    GeographicEvidence,
    E3_POLICY,
    records,
    file_digest,
    validate_review_document,
)
from .geographic_index import (
    build_index,
    index_version,
    INDEX_VERSION,
    require_subdivision_runtime,
)
from .geographic_prepare import write_json
from .geographic_e3 import reviewed_contexts, local_context, validate_degraded_document
from .geographic_geometry import canonical


def prepare_e3(
    base,
    worldcover,
    output,
    *,
    reviews=None,
    degraded=None,
    availability=None,
    regions=None,
    licensing=None,
):
    """Upgrade assets, never approvals; older acceptance documents are rejected."""
    require_subdivision_runtime()
    # Evidence-only loading permits explicit renewal of obsolete schema-3 B2
    # decisions. Such decisions are never accepted by generation/publication.
    source = GeographicEvidence(base, regions=regions)
    source.is_e3 = source.manifest.get("policy") == E3_POLICY
    source.review_document = json.loads(source.entries["reviews"].read_text())
    source.decisions_revision = file_digest(source.entries["reviews"])
    if reviews is None:
        if not source.is_e3 and any(
            d["conclusion"] == "accept_uncertainty"
            for d in source.review_document["decisions"]
        ):
            raise ValueError(
                "Legacy uncertainty acceptances require explicit E3 re-review; never convert to B2 resolutions"
            )
        reviews = (
            dict(source.review_document)
            if source.is_e3
            else dict(
                schema=3,
                policy=E3_POLICY,
                decisions=source.review_document["decisions"],
            )
        )
    validate_review_document(reviews)
    if reviews.get("schema") not in {3, 4} or reviews.get("policy") != E3_POLICY:
        raise ValueError(
            "E3 requires explicit schema-3/4 reviews; legacy acceptances are not resolutions"
        )
    from .geographic_data import geometry_digest, valid_geometry

    oldholds = {
        d["id"]: dict(
            d, extent_geometry=canonical(valid_geometry(d["extent"], polygon=True))
        )
        for d in source.review_document["decisions"]
        if d["conclusion"] == "hold"
    }
    newholds = {d["id"]: d for d in reviews["decisions"] if d["conclusion"] == "hold"}
    withdrawn = any(
        identifier not in newholds
        or old["region"] != newholds[identifier]["region"]
        or geometry_digest(old["extent_geometry"])
        != geometry_digest(
            canonical(valid_geometry(newholds[identifier]["extent"], polygon=True))
        )
        for identifier, old in oldholds.items()
    )
    if withdrawn and not (
        reviews.get("reviewed_by")
        and reviews.get("rationale")
        and reviews.get("supersedes") == source.decisions_revision
    ):
        raise ValueError(
            "Explicit hold withdrawal requires a new reviewed decision revision"
        )
    output = Path(output)
    if output.exists():
        raise ValueError("Use a new immutable E3 package directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".e3-") as temporary:
        stage = Path(temporary) / "package"
        stage.mkdir()
        manifest = json.loads(source.path.read_text())
        if licensing is not None:
            from .geographic_data import validate_licensing

            validate_licensing(licensing)
            manifest["licensing"] = licensing
        for key, entry in manifest["files"].items():
            path = stage / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source.entries[key], path)
            if (
                path.stat().st_size != entry["size"]
                or file_digest(path) != entry["sha256"]
            ):
                raise ValueError("Geographic source changed during preparation")

        def asset(key, path):
            manifest["files"][key] = dict(
                path=str(path.relative_to(stage)),
                size=path.stat().st_size,
                sha256=file_digest(path),
            )

        manifest.update(
            schema=2,
            policy=E3_POLICY,
            availability=(
                availability
                if availability is not None
                else manifest.get("availability", [])
            ),
        )
        for name, region in manifest["regions"].items():
            if (
                not source.is_e3
                or index_version(source.entries[region["index"]]) != INDEX_VERSION
            ):
                path = stage / f"{name}.sqlite"
                if path.exists():
                    path.unlink()
                print("Indexing authoritative evidence:", name, flush=True)
                build_index(path, records(source.entries[region["evidence"]]))
                key = "index:" + name
                asset(key, path)
                region["index"] = key
        wcpath = Path(worldcover)
        wc = json.loads(wcpath.read_text())
        if wc.get("product") != "ESA WorldCover 2021 v200":
            raise ValueError("Wrong WorldCover product")
        wcdir = stage / "worldcover"
        wcdir.mkdir(exist_ok=True)
        for name, entry in wc["tiles"].items():
            src = (wcpath.parent / entry["path"]).resolve()
            if (
                not src.is_relative_to(wcpath.parent.resolve())
                or not src.is_file()
                or src.stat().st_size != entry["size"]
                or file_digest(src) != entry["sha256"]
            ):
                raise ValueError("Corrupt WorldCover preparation input")
            target = wcdir / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if src != target.resolve():
                shutil.copyfile(src, target)
            asset("worldcover:" + name, target)
        shutil.copyfile(wcpath, wcdir / "manifest.json")
        asset("worldcover:manifest", wcdir / "manifest.json")
        manifest["worldcover"] = "worldcover:manifest"
        write_json(stage / "reviews.json", reviews)
        asset("reviews", stage / "reviews.json")
        if degraded is None and source.is_e3:
            degraded = json.loads(
                source.entries[source.manifest["degraded_policies"]].read_text()
            )
        degraded = (
            degraded
            if degraded is not None
            else dict(schema=1, policy=E3_POLICY, policies=[])
        )
        write_json(stage / "degraded-policies.json", degraded)
        asset("degraded:policies", stage / "degraded-policies.json")
        manifest["degraded_policies"] = "degraded:policies"
        manifest["contexts"] = {}
        manifest["acceptance_contexts"] = {}
        # The context-generation object has no publication authority. In
        # particular, a not-yet-bound water decision must not be auto-approved.
        write_json(stage / "manifest.json", manifest)
        draft = GeographicEvidence(stage / "manifest.json", regions=regions)
        draft._e3_engine = None
        contexts = reviewed_contexts(draft, reviews)
        manifest["contexts"] = {d["id"]: d["context_sha256"] for d in contexts}
        manifest["acceptance_contexts"] = {
            d["id"]: d["acceptance_contexts"] for d in contexts
        }
        validate_degraded_document(degraded, draft.regions)
        manifest["degraded_contexts"] = {
            p["id"]: local_context(draft, canonical(shape(p["extent"])), p["region"])
            for p in degraded["policies"]
        }
        if draft._e3_engine:
            draft._e3_engine.close()
        write_json(stage / "review-contexts.json", contexts)
        write_json(stage / "manifest.json", manifest)
        # No generated context is copied into a human decision above. The
        # constructor rejects stale exclusions and malformed authorizations.
        verified = GeographicDataset(
            stage / "manifest.json", regions=regions, require_e3=True
        )
        verified.close()
        stage.rename(output)
    return manifest, contexts

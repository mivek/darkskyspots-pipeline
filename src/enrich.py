"""Step 5: Spot ID generation and final spot-field normalization.

Naming is performed by :mod:`src.geonames` before this module runs.  This
stage deliberately treats ``name`` and ``nameDistanceKm`` as data fields and
passes them through unchanged; presentation (including the ``\u00b7 38 km``
format) belongs to the app.
"""


def enrich_spot(spot: dict) -> dict:
    """Enrich a single spot without discarding the naming contract.

    ``near`` may be empty for isolated, but valid, land spots.  ``name`` and
    ``nameDistanceKm`` are expected to have been attached by the GeoNames
    cascade; retaining the fields here makes the hand-off to tile export
    explicit and prevents accidental loss during enrichment.
    """
    enriched = dict(spot)
    enriched["id"] = spot_id(enriched["lat"], enriched["lon"])
    enriched["near"] = spot.get("near")
    enriched["altitude"] = None
    enriched.pop("row", None)
    enriched.pop("col", None)
    return enriched


def enrich_all(spots: list[dict], naming_index=None) -> list[dict]:
    """Enrich a batch, optionally attaching GeoNames names first.

    The optional index keeps this helper convenient for callers that already
    attached names while allowing the pipeline to make the naming boundary
    explicit in one place.  ``GeoNamesIndex.enrich_spots`` returns copies, so
    neither path mutates the caller's list.
    """
    named = naming_index.enrich_spots(spots) if naming_index is not None else spots
    return [enrich_spot(s) for s in named]


def spot_id(lat: float, lon: float) -> str:
    """
    Generate a unique, deterministic spot ID from coordinates.

    Format: "lat_lon" with 4 decimal places.
    Normalizes -0.0 to 0.0 via adding 0.0 to avoid "-0.0000".

    Idempotent: same (lat, lon) -> same output.
    """
    return f"{lat + 0.0:.4f}_{lon + 0.0:.4f}"

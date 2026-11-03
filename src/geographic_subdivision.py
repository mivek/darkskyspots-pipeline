"""Lossless spatial subdivision using source vertices, never box clipping."""

import re

import shapely

PAGE_BYTES = 1024 * 1024


def require_subdivision_runtime():
    version = tuple(int(n) for n in re.findall(r"\d+", shapely.__version__)[:2])
    if (
        version < (2, 1)
        or shapely.geos_version < (3, 10, 0)
        or not callable(getattr(shapely, "constrained_delaunay_triangles", None))
    ):
        raise ValueError(
            "Error: geographic preparation requires Shapely >=2.1 and GEOS >=3.10 with constrained triangulation"
        )


def normalized_wkb(geometry):
    return shapely.to_wkb(shapely.normalize(geometry), byte_order=1)


def component_pages(component, db, max_bytes):
    """Yield bounded pages and original primitive envelopes, deterministically.

    Only oversized components are triangulated. The complete source component
    remains the preparation oracle, including every original boundary vertex.
    SQLite spills sorting to disk; GEOS preparation needs one component's
    triangulation in memory. No constant-RAM triangulation claim is made.
    """
    raw = shapely.to_wkb(component, byte_order=1)
    if len(raw) <= max_bytes:
        yield raw, [component.bounds]
        return
    del raw
    triangles = shapely.constrained_delaunay_triangles(component)
    if triangles.is_empty:
        raise ValueError("Error: constrained subdivision produced no geometry")
    db.execute(
        """CREATE TEMP TABLE IF NOT EXISTS triangle_sort
                  (w REAL,s REAL,e REAL,n REAL,geometry BLOB)"""
    )
    db.execute("DELETE FROM triangle_sort")
    db.execute(
        "CREATE TEMP TABLE IF NOT EXISTS page_certificate(sequence INTEGER PRIMARY KEY,geometry BLOB)"
    )
    db.execute("DELETE FROM page_certificate")
    for triangle in triangles.geoms:
        blob = normalized_wkb(triangle)
        db.execute(
            "INSERT INTO triangle_sort VALUES(?,?,?,?,?)", (*triangle.bounds, blob)
        )
    del triangles
    limit = min(PAGE_BYTES, max_bytes)
    entries, envelopes, size = [], [], 9
    sequence = 0

    def packed_page(entries):
        nonlocal sequence
        # Cancel internal triangle edges once during preparation. This is an
        # exact storage union, not clipping or a new geographic exclusion.
        geometry = shapely.coverage_union_all(entries)
        blob = normalized_wkb(geometry)
        if not geometry.is_valid or geometry.is_empty or len(blob) > limit:
            raise ValueError(
                "Error: subdivision page exceeds storage budget or is invalid"
            )
        db.execute("INSERT INTO page_certificate VALUES(?,?)", (sequence, blob))
        sequence += 1
        return blob

    for w, s, e, n, blob in db.execute(
        "SELECT * FROM triangle_sort ORDER BY w,s,e,n,geometry"
    ):
        if size + len(blob) > limit and entries:
            yield packed_page(entries), envelopes
            entries, envelopes, size = [], [], 9
        if len(blob) + 9 > limit:
            raise ValueError("Error: subdivision primitive exceeds storage budget")
        entries.append(shapely.from_wkb(blob))
        envelopes.append((w, s, e, n))
        size += len(blob)
    if entries:
        yield packed_page(entries), envelopes
    # Certify the final serialized pages, including every source boundary
    # coordinate. The caller must exhaust this iterator before marking the
    # index complete. Any failure rolls back and removes its staging index.
    pages = [
        shapely.from_wkb(row[0])
        for row in db.execute("SELECT geometry FROM page_certificate ORDER BY sequence")
    ]
    reconstructed = shapely.coverage_union_all(pages)
    if not reconstructed.is_valid or normalized_wkb(reconstructed) != normalized_wkb(
        component
    ):
        raise ValueError(
            "Error: subdivision cannot preserve authoritative boundary coordinates exactly"
        )
    db.execute("DELETE FROM triangle_sort")
    db.execute("DELETE FROM page_certificate")

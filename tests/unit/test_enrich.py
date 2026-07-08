"""Tests for src/enrich.py (spot ID generation, near passthrough, altitude stub)."""


# --- enrich_spot tests ---


def test_enrich_spot_adds_id_and_near():
    """Places with a nearby match sets name."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "near": "Paris"}
    out = enrich_spot(spot)
    assert "name" not in out
    assert "id" in out
    assert out["id"] == "48.5000_2.0000"
    assert out["near"] == "Paris"
    assert "row" not in out
    assert "col" not in out


def test_enrich_spot_without_name_field():
    """Spot without name field: no name in output, id present, altitude=None."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "row": 10, "col": 20}
    out = enrich_spot(spot)
    assert "name" not in out
    assert out["altitude"] is None
    assert "id" in out
    assert out["id"] == "48.5000_2.0000"
    assert "row" not in out
    assert "col" not in out


def test_enrich_spot_altitude_null():
    """Altitude field is present but None (MVP stub), id present, row/col stripped."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "row": 10, "col": 20}
    out = enrich_spot(spot)
    assert out["altitude"] is None
    assert "name" not in out
    assert "id" in out
    assert out["id"] == "48.5000_2.0000"
    assert "row" not in out
    assert "col" not in out


def test_enrich_spot_strips_row_col():
    """row and col are stripped from the output dict."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "row": 10, "col": 20}
    out = enrich_spot(spot)
    assert "row" not in out
    assert "col" not in out
    assert "id" in out


# --- enrich_all tests ---


def test_enrich_all_batch():
    """3 spots, verify all are processed."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.5, "lon": 4.0, "darkness": 0.7},
        {"lat": 43.0, "lon": 1.0, "darkness": 0.5},
    ]
    out = enrich_all(spots)
    assert len(out) == 3
    for s in out:
        assert "id" in s
        assert "name" not in s
        assert "altitude" in s
        assert s["altitude"] is None


def test_enrich_all_preserves_near():
    """Spots with near values preserve them."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "near": "Paris"},
    ]
    out = enrich_all(spots)
    assert out[0]["near"] == "Paris"


def test_enrich_all_altitude_none():
    """All output spots have altitude=None."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.5, "lon": 4.0, "darkness": 0.7},
    ]
    out = enrich_all(spots)
    for s in out:
        assert s["altitude"] is None


# --- spec-technique.md ---


def test_spec_technique_id_format():
    """spec-technique.md uses coordinate-based ID format '42.7283_1.6492'."""
    with open("spec-technique.md") as f:
        content = f.read()
    assert '"42.7283_1.6492"' in content


# --- spot_id tests ---


def test_spot_id_format():
    """spot_id returns lat_lon with 4 decimal places."""
    from src.enrich import spot_id
    assert spot_id(42.7283, 1.6492) == "42.7283_1.6492"


def test_spot_id_negative_lon():
    """Negative longitude produces negative in output."""
    from src.enrich import spot_id
    assert spot_id(48.8566, -2.3522) == "48.8566_-2.3522"


def test_spot_id_deterministic():
    """Same inputs twice -> same result."""
    from src.enrich import spot_id
    a = spot_id(42.7283, 1.6492)
    b = spot_id(42.7283, 1.6492)
    assert a == b


def test_spot_id_uniqueness():
    """Two nearby but distinct coords produce different IDs."""
    from src.enrich import spot_id
    a = spot_id(42.7283, 1.6492)
    b = spot_id(42.7284, 1.6493)
    assert a != b


def test_spot_id_negative_zero_normalized():
    """-0.0 produces '0.0000' not '-0.0000'."""
    from src.enrich import spot_id
    assert spot_id(-0.0, 0.0) == "0.0000_0.0000"
    assert spot_id(0.0, -0.0) == "0.0000_0.0000"
    assert spot_id(-0.0, -0.0) == "0.0000_0.0000"

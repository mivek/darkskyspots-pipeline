"""Tests for src/enrich.py (OSM place lookup + spot ID generation)."""
from unittest.mock import MagicMock, patch


def test_enrich_spot_adds_name(mock_region):
    """Mock Overpass to return a known place, verify name is set."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9}
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "tags": {"name": "Pic de Beille"},
            }
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        out = enrich_spot(spot, mock_region)
    assert out["name"] == "Pic de Beille"


def test_enrich_spot_no_name(mock_region):
    """Mock returns empty elements, name is None."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9}
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        out = enrich_spot(spot, mock_region)
    assert out["name"] is None


def test_enrich_spot_request_failure(mock_region):
    """Mock raises RequestException, name is None gracefully."""
    from src.enrich import enrich_spot
    import requests
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9}
    with patch("src.enrich.requests.get",
               side_effect=requests.RequestException("overpass down")):
        out = enrich_spot(spot, mock_region)
    assert out["name"] is None


def test_enrich_all_batch(mock_region):
    """3 spots, verify all are processed."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.5, "lon": 4.0, "darkness": 0.7},
        {"lat": 43.0, "lon": 1.0, "darkness": 0.5},
    ]
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        out = enrich_all(spots, mock_region)
    assert len(out) == 3
    for s in out:
        assert "name" in s
        assert "altitude" in s
        assert s["altitude"] is None  # MVP stub


def test_enrich_spot_altitude_null(mock_region):
    """Altitude field is present but None (MVP stub)."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9}
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        out = enrich_spot(spot, mock_region)
    assert out["altitude"] is None


# --- spot_id tests ---

def test_spot_id_with_name():
    """spot_id with name uses slug."""
    from src.enrich import spot_id
    assert spot_id(42.7, 1.6, "fr", name="Beille") == "fr-00-beille"


def test_spot_id_fallback():
    """No name -> uses lat/lon encoding."""
    from src.enrich import spot_id
    assert spot_id(42.7, 1.6, "fr") == "fr-00-42_001"


def test_spot_id_deterministic():
    """Same inputs twice -> same result."""
    from src.enrich import spot_id
    a = spot_id(42.7, 1.6, "fr", name="Beille")
    b = spot_id(42.7, 1.6, "fr", name="Beille")
    assert a == b


def test_spot_id_with_dept():
    """Department is part of the ID."""
    from src.enrich import spot_id
    assert spot_id(42.7, 1.6, "fr", name="Beille", dept=9) == "fr-09-beille"


def test_spot_id_slugify_special_chars():
    """Accents and punctuation get stripped from slug."""
    from src.enrich import spot_id
    assert spot_id(43.3, -1.9, "fr", name="Saint-Jean-de-Luz") == "fr-00-saint-jean-de-luz"


def test_spot_id_empty_name_fallback():
    """Empty name triggers the lat/lon fallback."""
    from src.enrich import spot_id
    assert spot_id(42.7, 1.6, "fr", name="") == "fr-00-42_001"

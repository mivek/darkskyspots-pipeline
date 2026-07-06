"""Tests for src/enrich.py (OSM place lookup + spot ID generation)."""
from unittest.mock import MagicMock, call, patch

from requests.exceptions import RequestException


# --- _fetch_places tests ---


def test_fetch_places_one_request(mock_region):
    """Single request returns one place."""
    from src.enrich import _fetch_places

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {"type": "node", "id": 1, "lat": 48.8566, "lon": 2.3522, "tags": {"name": "Paris", "place": "city"}}
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response) as mock_get:
        places = _fetch_places(mock_region)
    assert len(places) == 1
    assert places[0]["name"] == "Paris"
    mock_get.assert_called_once()


def test_fetch_places_retry_then_succeed(mock_region):
    """Two failures then success on third try."""
    from src.enrich import _fetch_places
    import time

    success = MagicMock()
    success.json.return_value = {
        "elements": [{"type": "node", "id": 1, "lat": 48.8566, "lon": 2.3522, "tags": {"name": "Paris", "place": "city"}}]
    }
    success.raise_for_status = MagicMock()

    mock_get = MagicMock(side_effect=[RequestException("fail1"), RequestException("fail2"), success])
    with patch("src.enrich.requests.get", mock_get), patch("src.enrich.time.sleep") as mock_sleep:
        places = _fetch_places(mock_region)
    assert len(places) == 1
    assert places[0]["name"] == "Paris"
    assert mock_get.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_has_calls([call(2), call(4)])


def test_fetch_places_raises_after_exhaustion(mock_region):
    """Three failures raises RuntimeError."""
    from src.enrich import _fetch_places

    mock_get = MagicMock(side_effect=RequestException("fail"))
    with patch("src.enrich.requests.get", mock_get), patch("src.enrich.time.sleep"):
        import pytest
        with pytest.raises(RuntimeError, match="Overpass query failed after"):
            _fetch_places(mock_region)


def test_fetch_places_user_agent(mock_region):
    """Request includes expected User-Agent header."""
    from src.enrich import _fetch_places

    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response) as mock_get:
        _fetch_places(mock_region)
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["User-Agent"] == "darkskyspots-pipeline/1.0"


def test_fetch_places_bbox_query(mock_region):
    """Query string contains lat_min,lon_min,lat_max,lon_max order for Overpass."""
    from src.enrich import _fetch_places

    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response) as mock_get:
        _fetch_places(mock_region)
    _, kwargs = mock_get.call_args
    query = kwargs["params"]["data"]
    # bbox in Overpass is lat_min,lon_min,lat_max,lon_max
    # mock_region has bbox: [-5, 41, 10, 51] → lon_min=-5, lat_min=41, lon_max=10, lat_max=51
    # So the query should contain (41,-5,51,10)
    assert "41,-5,51,10" in query


def test_fetch_places_skips_elements_without_name(mock_region):
    """Elements without name tag or with empty name are skipped."""
    from src.enrich import _fetch_places

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {"type": "node", "id": 1, "lat": 48.8566, "lon": 2.3522, "tags": {"name": "Paris", "place": "city"}},
            {"type": "node", "id": 2, "lat": 45.7640, "lon": 4.8357, "tags": {"place": "city"}},  # no name tag
            {"type": "node", "id": 3, "lat": 43.2965, "lon": 5.3698, "tags": {"name": "", "place": "city"}},  # empty name
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        places = _fetch_places(mock_region)
    assert len(places) == 1
    assert places[0]["name"] == "Paris"


def test_fetch_places_handles_ways_with_center(mock_region):
    """Way elements use center dict for coordinates."""
    from src.enrich import _fetch_places

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {"type": "way", "id": 1, "center": {"lat": 48.8566, "lon": 2.3522}, "tags": {"name": "Paris", "place": "city"}}
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        places = _fetch_places(mock_region)
    assert len(places) == 1
    assert places[0]["name"] == "Paris"
    assert places[0]["lat"] == 48.8566
    assert places[0]["lon"] == 2.3522


def test_fetch_places_handles_nodes_with_lat_lon(mock_region):
    """Node elements use direct lat/lon for coordinates."""
    from src.enrich import _fetch_places

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {"type": "node", "id": 1, "lat": 48.8566, "lon": 2.3522, "tags": {"name": "Paris", "place": "city"}}
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        places = _fetch_places(mock_region)
    assert len(places) == 1
    assert places[0]["lat"] == 48.8566
    assert places[0]["lon"] == 2.3522


# --- _nearest_place tests ---


def test_nearest_place_found():
    """Place within radius returns its name."""
    from src.enrich import _nearest_place

    spot = {"lat": 48.0, "lon": 2.0}
    places = [{"name": "Paris", "lat": 48.8566, "lon": 2.3522}]
    assert _nearest_place(spot, places, max_radius_km=100) == "Paris"


def test_nearest_place_miss():
    """Place outside radius returns None."""
    from src.enrich import _nearest_place

    spot = {"lat": 48.0, "lon": 2.0}
    places = [{"name": "Paris", "lat": 48.8566, "lon": 2.3522}]
    assert _nearest_place(spot, places, max_radius_km=10) is None


def test_nearest_place_picks_closest():
    """Two places, returns the closer one."""
    from src.enrich import _nearest_place

    spot = {"lat": 48.0, "lon": 2.0}
    places = [
        {"name": "Lyon", "lat": 45.76, "lon": 4.83},
        {"name": "Paris", "lat": 48.8566, "lon": 2.3522},
    ]
    assert _nearest_place(spot, places, max_radius_km=200) == "Paris"


def test_nearest_place_empty_list():
    """Empty places list returns None."""
    from src.enrich import _nearest_place

    spot = {"lat": 48.0, "lon": 2.0}
    assert _nearest_place(spot, [], max_radius_km=100) is None


# --- enrich_spot tests ---

def test_enrich_spot_adds_name():
    """Places with a nearby match sets name."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9}
    places = [{"name": "Pic de Beille", "lat": 48.5, "lon": 2.1}]
    out = enrich_spot(spot, places, max_radius_km=20)
    assert out["name"] == "Pic de Beille"
    assert "id" in out
    assert out["id"] == "48.5000_2.0000"
    assert "row" not in out
    assert "col" not in out


def test_enrich_spot_no_name():
    """No places returns None for name, id present, row/col stripped."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "row": 10, "col": 20}
    out = enrich_spot(spot, [], max_radius_km=10)
    assert out["name"] is None
    assert "id" in out
    assert out["id"] == "48.5000_2.0000"
    assert "row" not in out
    assert "col" not in out


def test_enrich_spot_altitude_null():
    """Altitude field is present but None (MVP stub), id present, row/col stripped."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "row": 10, "col": 20}
    out = enrich_spot(spot, [], max_radius_km=10)
    assert out["altitude"] is None
    assert "id" in out
    assert out["id"] == "48.5000_2.0000"
    assert "row" not in out
    assert "col" not in out


def test_enrich_spot_strips_row_col():
    """row and col are stripped from the output dict."""
    from src.enrich import enrich_spot
    spot = {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "row": 10, "col": 20}
    places = []
    out = enrich_spot(spot, places, max_radius_km=10)
    assert "row" not in out
    assert "col" not in out
    assert "id" in out


def test_enrich_all_batch(mock_region):
    """3 spots, verify all are processed via batched fetch."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.5, "lon": 4.0, "darkness": 0.7},
        {"lat": 43.0, "lon": 1.0, "darkness": 0.5},
    ]
    mock_places = [{"name": "Paris", "lat": 48.8566, "lon": 2.3522}]
    with patch("src.enrich._fetch_places", return_value=mock_places):
        out = enrich_all(spots, mock_region)
    assert len(out) == 3
    for s in out:
        assert "id" in s
        assert "name" in s
        assert "altitude" in s
        assert s["altitude"] is None


def test_enrich_all_makes_one_request(mock_region):
    """enrich_all makes exactly one Overpass request."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.5, "lon": 4.0, "darkness": 0.7},
    ]
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response) as mock_get:
        enrich_all(spots, mock_region)
    mock_get.assert_called_once()


def test_enrich_all_preserves_near(mock_region):
    """Spots with near values preserve them."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9, "near": "Paris"},
    ]
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        out = enrich_all(spots, mock_region)
    assert out[0]["near"] == "Paris"


def test_enrich_all_altitude_none(mock_region):
    """All output spots have altitude=None."""
    from src.enrich import enrich_all
    spots = [
        {"lat": 48.5, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.5, "lon": 4.0, "darkness": 0.7},
    ]
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        out = enrich_all(spots, mock_region)
    for s in out:
        assert s["altitude"] is None


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

"""E2E API tests for ZipScope (plan item 5).

Runs against the live dev API (localhost:8000 by default).
"""

import pytest
import requests

API = "http://localhost:8000"


class TestHealth:
    def test_health_ok(self):
        r = requests.get(f"{API}/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["geoms_loaded"] > 0
        assert "vintages" in data["data"]


class TestCacheControl:
    """Plan item 2: /zips must have Cache-Control header."""

    def test_state_zips_has_cache_control(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "state", "region": "48"})
        assert r.status_code == 200
        assert "cache-control" in r.headers
        assert "max-age" in r.headers["cache-control"]

    def test_county_zips_has_cache_control(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "county", "region": "48113"})
        assert r.status_code == 200
        assert "cache-control" in r.headers

    def test_zip_zips_has_cache_control(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "zip", "region": "78701"})
        assert r.status_code == 200
        assert "cache-control" in r.headers


class TestGeocode:
    """Plan item: reverse geocoding returns valid ZIP + metadata."""

    def test_austin_geocode(self):
        r = requests.post(f"{API}/api/geocode", json={"lat": 30.2672, "lon": -97.7431})
        assert r.status_code == 200
        data = r.json()
        assert data["zip"] == "78701"
        assert data["county"]["name"] == "Travis"
        assert data["state"]["fips"] == "48"

    def test_dc_geocode(self):
        r = requests.post(f"{API}/api/geocode", json={"lat": 38.9072, "lon": -77.0369})
        assert r.status_code == 200
        data = r.json()
        assert data["state"]["fips"] == "11"  # DC is a district, not a state
        assert len(data["zip"]) == 5  # valid 5-digit ZIP

    def test_ocean_returns_404(self):
        r = requests.post(f"{API}/api/geocode", json={"lat": 0, "lon": -180})
        assert r.status_code == 404


class TestZips:
    """Plan item: /zips returns proper GeoJSON with correct counts."""

    def test_texas_returns_many_zips(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "state", "region": "48"})
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["zips"] > 1500  # TX has ~2000 ZIPs
        assert len(data["features"]) == data["meta"]["zips"]
        # At least one feature should have properties
        assert "properties" in data["features"][0]
        assert "zip" in data["features"][0]["properties"]

    def test_dallas_county_returns_zips(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "county", "region": "48113"})
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["zips"] > 50  # Dallas County has many ZIPs

    def test_single_zip(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "zip", "region": "78701"})
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["zips"] == 1
        assert data["features"][0]["properties"]["zip"] == "78701"

    def test_unknown_state_returns_404(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "state", "region": "99"})
        assert r.status_code == 404

    def test_unknown_zip_returns_404(self):
        r = requests.get(f"{API}/api/zips", params={"scope": "zip", "region": "00000"})
        assert r.status_code == 404


class TestRegions:
    """Plan item: region search works."""

    def test_search_state(self):
        r = requests.get(f"{API}/api/regions", params={"q": "texas", "scope": "state"})
        assert r.status_code == 200
        data = r.json()
        names = [r["name"].lower() for r in data["regions"]]
        assert "texas" in names

    def test_search_county(self):
        r = requests.get(f"{API}/api/regions", params={"q": "dallas", "scope": "county", "state": "48"})
        assert r.status_code == 200
        data = r.json()
        names = [r["name"].lower() for r in data["regions"]]
        assert any("dallas" in n for n in names)

    def test_search_place(self):
        r = requests.get(f"{API}/api/regions", params={"q": "austin", "scope": "place", "state": "48"})
        assert r.status_code == 200
        data = r.json()
        names = [r["name"].lower() for r in data["regions"]]
        assert "austin" in names

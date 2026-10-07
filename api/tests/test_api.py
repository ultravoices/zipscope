"""ZipScope API tests.

Requires the built data layer (data/out) — skip gracefully if absent.
Run: ./.venv/bin/python -m pytest api/tests -q
"""

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "api"))

HAS_DATA = os.path.exists(os.path.join(ROOT, "data", "out", "zips.db"))

pytestmark = pytest.mark.skipif(not HAS_DATA, reason="data/out not built — run data/scripts/build_data.py")

from fastapi.testclient import TestClient  # noqa: E402

import main as api_main  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(api_main.app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["geoms_loaded"] >= 0  # lazy-loaded
    assert body["data"]["zctas"] >= 30000


def test_geocode_austin(client):
    # downtown Austin, TX
    r = client.post("/api/geocode", json={"lat": 30.2672, "lon": -97.7431})
    assert r.status_code == 200
    b = r.json()
    assert b["state"]["fips"] == "48"
    assert b["state"]["name"] == "Texas"
    assert b["zip"].startswith("787")
    assert b["city"].lower() == "austin"


def test_geocode_washington_dc(client):
    r = client.post("/api/geocode", json={"lat": 38.8977, "lon": -77.0365})
    assert r.status_code == 200
    assert r.json()["state"]["fips"] == "11"


def test_geocode_ocean_returns_404(client):
    # mid-Atlantic, far from any ZCTA
    r = client.post("/api/geocode", json={"lat": 40.0, "lon": -40.0})
    assert r.status_code == 404


def test_zips_state_tx(client):
    r = client.get("/api/zips", params={"scope": "state", "region": "48"})
    assert r.status_code == 200
    b = r.json()
    assert b["meta"]["zips"] > 1000
    assert all(f["properties"]["zip"] for f in b["features"][:50])
    # every geometry is valid geojson
    f0 = b["features"][0]
    assert f0["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_zips_county_denton(client):
    r = client.get("/api/zips", params={"scope": "county", "region": "48113"})
    assert r.status_code == 200
    assert r.json()["meta"]["zips"] > 50


def test_zips_place_austin(client):
    # find Austin's place id via regions search
    rr = client.get("/api/regions", params={"q": "Austin", "scope": "place", "state": "48"})
    assert rr.status_code == 200
    regs = rr.json()["regions"]
    assert regs, "expected Austin place in regions"
    pid = regs[0]["id"]
    r = client.get("/api/zips", params={"scope": "place", "region": pid})
    assert r.status_code == 200
    assert r.json()["meta"]["zips"] > 10


def test_zips_single(client):
    r = client.get("/api/zips", params={"scope": "zip", "region": "78701"})
    assert r.status_code == 200
    assert r.json()["meta"]["zips"] == 1


def test_regions_search(client):
    r = client.get("/api/regions", params={"q": "tex", "scope": "state"})
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["regions"]]
    assert any("Texas" in n for n in names)


def test_regions_bad_state_404(client):
    r = client.get("/api/zips", params={"scope": "state", "region": "99"})
    assert r.status_code == 404

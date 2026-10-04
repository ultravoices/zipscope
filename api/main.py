"""ZipScope API — FastAPI service over the built data layer (data/out).

Endpoints:
  POST /api/geocode   {lat, lon} -> nearest ZCTA + its city/county/state
  GET  /api/zips      ?scope=state|county|place|zip&region=<fips-or-zip>[&state=<fips>]
  GET  /api/regions   ?q=<text>&scope=state|county|place[&state=<fips>]
  GET  /api/health    liveness + data provenance

Run (dev):
  ./.venv/bin/python -m uvicorn main:app --port 8000 --app-dir api
"""

import json
import math
import os
import sqlite3
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "data", "out")
DB_PATH = os.path.join(DATA, "zips.db")
ZCTA_DIR = os.path.join(DATA, "zctas")

app = FastAPI(title="ZipScope API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
_state_cache: dict = {}
_geoms: list = []            # parallel to _geom_zips
_geom_zips: list = []        # zip ids, same order as _geoms
_geom_by_zip: dict = {}      # zip -> geometry (for nearest/contains)
_state_files: dict = {}      # state_fips -> filename
_zip_state: dict = {}        # zip -> primary state_fips
_tree: Optional[STRtree] = None


def _load_state(fname: str) -> dict:
    with _lock:
        if fname in _state_cache:
            return _state_cache[fname]
    path = os.path.join(ZCTA_DIR, fname)
    if not os.path.exists(path):
        return {"type": "FeatureCollection", "features": []}
    with open(path) as f:
        fc = json.load(f)
    with _lock:
        _state_cache[fname] = fc
    return fc


def _build():
    """Load DB + geometries at startup."""
    global _tree
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"missing {DB_PATH} — run the data pipeline first: "
            "./.venv/bin/python data/scripts/build_data.py")
    for fname in sorted(os.listdir(ZCTA_DIR)):
        if fname.endswith(".geojson"):
            st = fname[: -len(".geojson")]
            _state_files[st] = fname
            for feat in _load_state(fname).get("features", []):
                z = feat.get("properties", {}).get("zip")
                if not z or z in _geom_by_zip:
                    continue
                try:
                    g = shape(feat["geometry"])
                except Exception:
                    continue
                if g and not g.is_empty:
                    _geom_by_zip[z] = g
                    _geoms.append(g)
                    _geom_zips.append(z)
                    _zip_state[z] = st
    if _geoms:
        _tree = STRtree(_geoms)


app.state.built = False


@app.on_event("startup")
def _startup():
    if os.path.exists(DB_PATH):
        _build()
        app.state.built = True
    else:
        print(f"[api] WARNING: no built data at {DATA}; endpoints will 503")
    print(f"[api] ready: {len(_geom_by_zip)} zcta geometries, "
          f"{len(_state_files)} state files")


def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _zip_row(con, z: str):
    r = con.execute(
        "SELECT zip, state_fips, state_name, county_fips, county_name, city,"
        " population, centroid_lat, centroid_lon, area_sqmi FROM zips WHERE zip=?",
        (z,)).fetchone()
    return dict(r) if r else None


def _zip_payload(row: dict) -> dict:
    return {
        "zip": row["zip"],
        "state": {"fips": row["state_fips"], "name": row["state_name"]},
        "county": {"fips": row["county_fips"], "name": row["county_name"]},
        "city": row["city"],
        "population": row["population"],
        "centroid": {"lat": row["centroid_lat"], "lon": row["centroid_lon"]},
        "area_sqmi": row["area_sqmi"],
    }


# ---------------------------------------------------------------- geocode


class GeoPoint(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


@app.post("/api/geocode")
def geocode(pt: GeoPoint):
    if not app.state.built:
        raise HTTPException(503, "data not built — run data/scripts/build_data.py")
    con = _db()
    try:
        p = Point(pt.lon, pt.lat)
        found = None
        if _tree is not None:
            for i in _tree.query(p.buffer(0.05)):
                if _geoms[i].contains(p):
                    found = _geom_zips[i]
                    break
            if found is None:
                # fall back to nearest ZCTA (water / gaps)
                i = _tree.nearest(p)
                if i is not None and _geoms[i].distance(p) < 2.0:
                    found = _geom_zips[i]
        if not found:
            raise HTTPException(404, "no ZCTA covers this location")
        row = _zip_row(con, found)
        if not row:
            raise HTTPException(404, "no ZCTA covers this location")
        return _zip_payload(row)
    finally:
        con.close()


# ---------------------------------------------------------------- zips


def _collect_zips(zips: list) -> list:
    """Fetch Feature objects for a list of zips from their state files."""
    out, seen = [], set()
    by_state: dict = {}
    for z in zips:
        st = _zip_state.get(z)
        if st and st in _state_files:
            by_state.setdefault(st, []).append(z)
    feats_cache: dict = {}
    for st, zlist in by_state.items():
        fname = _state_files[st]
        if fname not in feats_cache:
            feats_cache[fname] = {
                f.get("properties", {}).get("zip"): f
                for f in _load_state(fname).get("features", [])
            }
        for z in zlist:
            f = feats_cache[fname].get(z)
            if f and z not in seen:
                seen.add(z)
                out.append(f)
    return out


@app.get("/api/zips")
def get_zips(
    scope: str = Query("state", pattern="^(state|county|place|zip)$"),
    region: Optional[str] = Query(None, description="FIPS (state/county/place) or 5-digit zip"),
    state: Optional[str] = Query(None, description="optional state FIPS filter"),
):
    if not app.state.built:
        raise HTTPException(503, "data not built — run data/scripts/build_data.py")
    if not region:
        raise HTTPException(400, "missing region")
    con = _db()
    try:
        if scope == "zip":
            if not _zip_row(con, region):
                raise HTTPException(404, f"unknown zip {region}")
            feats = _collect_zips([region])
            return JSONResponse({"type": "FeatureCollection", "features": feats,
                                 "meta": {"scope": scope, "region": region, "zips": len(feats)}})

        if scope == "state":
            fname = _state_files.get(region)
            if not fname:
                raise HTTPException(404, f"unknown state {region}")
            feats = _load_state(fname).get("features", [])
            return JSONResponse({"type": "FeatureCollection", "features": feats,
                                 "meta": {"scope": scope, "region": region, "zips": len(feats)}})

        # county / place: resolve via zip_regions (many-to-many where applicable)
        rows = con.execute(
            "SELECT DISTINCT zip FROM zip_regions WHERE scope=? AND region_id=?",
            (scope, region)).fetchall()
        zips = [r["zip"] for r in rows]
        if state:
            zips = [z for z in zips if _zip_state.get(z) == state]
        feats = _collect_zips(zips)
        if not feats:
            raise HTTPException(404, f"no zips in {scope} {region}")
        return JSONResponse({"type": "FeatureCollection", "features": feats,
                             "meta": {"scope": scope, "region": region,
                                      "zips": len(feats), "matched": len(zips)}})
    finally:
        con.close()


# ---------------------------------------------------------------- regions


@app.get("/api/regions")
def search_regions(
    q: Optional[str] = Query(None, min_length=1, max_length=64),
    scope: Optional[str] = Query(None, pattern="^(state|county|place)$"),
    state: Optional[str] = Query(None),
    limit: int = Query(8, ge=1, le=50),
):
    if not app.state.built:
        raise HTTPException(503, "data not built — run data/scripts/build_data.py")
    con = _db()
    try:
        clauses, params = [], []
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        else:
            clauses.append("scope IN ('state','county','place')")
        if state:
            clauses.append("state_fips = ?")
            params.append(state)
        if q:
            like = f"%{q.strip()}%"
            clauses.append("(name LIKE ? OR id = ?)")
            params += [like, q.strip()]
        sql = (f"SELECT scope, id, name, state_fips, code FROM regions "
               f"WHERE {' AND '.join(clauses)} ORDER BY name LIMIT ?")
        params.append(limit)
        return {"regions": [dict(r) for r in con.execute(sql, params).fetchall()]}
    finally:
        con.close()


# ---------------------------------------------------------------- health


@app.get("/api/health")
def health():
    report = {}
    rp = os.path.join(DATA, "build-report.json")
    if os.path.exists(rp):
        with open(rp) as f:
            report = json.load(f)
    return {
        "ok": bool(app.state.built),
        "geoms_loaded": len(_geom_by_zip),
        "data": {
            "built_at": report.get("built_at"),
            "zctas": report.get("zctas"),
            "vintages": report.get("vintages"),
            "sources": report.get("sources"),
        },
    }


# ------------------------------------------------------------- static (prod)

dist = os.path.join(ROOT, "web", "dist")
if os.path.isdir(dist):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    class SPAStatic(StaticFiles):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except Exception:
                return FileResponse(os.path.join(dist, "index.html"))

    app.mount("/", SPAStatic(directory=dist, html=True), name="spa")

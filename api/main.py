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

# State FIPS (2-digit) → 2-letter abbreviation (50 states + DC; territories excluded).
_STATE_FIPS_TO_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY",
}
_STATE_ABBR_TO_FIPS = {v: k for k, v in _STATE_FIPS_TO_ABBR.items()}
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
_state_files: dict = {}       # state_fips -> filename
_zip_state: dict = {}         # zip -> primary state_fips (from DB)

# Lazy-loaded: per-state STRtree (built on first geocode for that state).
# Key: state_fips, Value: (geoms, zips_list)
_state_trees: dict = {}


def _load_state(fname: str) -> dict:
    """Load a state GeoJSON file (lazy, cached)."""
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


def _ensure_db_loaded():
    """Load ZIP metadata from DB at startup (no geometries, < 30 MB)."""
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"missing {DB_PATH} — run the data pipeline first: "
            "./.venv/bin/python data/scripts/build_data.py")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    # Read (zip, state_fips, lat, lon) for all ZIPs — small, ~33k rows.
    rows = con.execute(
        "SELECT zip, state_fips, centroid_lat, centroid_lon "
        "FROM zips WHERE centroid_lat IS NOT NULL AND centroid_lon IS NOT NULL").fetchall()
    for row in rows:
        d = dict(row)
        _zip_state[d["zip"]] = d["state_fips"]
    con.close()
    # Scan state GeoJSON filenames (no data loading).
    for fname in sorted(os.listdir(ZCTA_DIR)):
        if fname.endswith(".geojson"):
            st = fname[:-len(".geojson")]
            _state_files[st] = fname


def _ensure_tree(state_fips: str) -> tuple:
    """Build (or return cached) a per-state STRtree. Returns (geoms, zips_list, tree)."""
    with _lock:
        if state_fips in _state_trees:
            return _state_trees[state_fips]
    fname = _state_files.get(state_fips)
    if not fname:
        return ([], [], None)
    fc = _load_state(fname).get("features", [])
    geoms, zips_list = [], []
    for feat in fc:
        z = feat.get("properties", {}).get("zip")
        if not z:
            continue
        try:
            g = shape(feat["geometry"])
        except Exception:
            continue
        if g and not g.is_empty:
            geoms.append(g)
            zips_list.append(z)
    # Build STRtree for this state only (small, < 20 MB).
    tree = STRtree(geoms) if geoms else None
    with _lock:
        _state_trees[state_fips] = (geoms, zips_list, tree)
    return (geoms, zips_list, tree)


def _geocode_state_point(state_fips: str, p: Point, geoms: list, zips_list: list, tree) -> Optional[str]:
    """Try to find the ZCTA containing p using the state's STRtree. Returns zip id or None."""
    if tree is None:
        return None
    for i in tree.query(p.buffer(0.05)):
        if geoms[i].contains(p):
            return zips_list[i]
    return None


def _nearest_centroid_zip(lat: float, lon: float) -> Optional[str]:
    """Find the ZIP whose centroid is nearest to (lat, lon). Uses in-memory centroid list."""
    # Read centroids from DB (fast, no geometries loaded).
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    # Find centroids within 2 degrees (bounding box) to limit search space.
    rows = con.execute(
        "SELECT zip, centroid_lat, centroid_lon FROM zips "
        "WHERE centroid_lat BETWEEN ? AND ? AND centroid_lon BETWEEN ? AND ?",
        (lat - 2.0, lat + 2.0, lon - 2.0, lon + 2.0)).fetchall()
    con.close()
    if not rows:
        return None
    best_zip, best_dist = None, float('inf')
    for row in rows:
        d = dict(row)
        c_lat, c_lon = d["centroid_lat"], d["centroid_lon"]
        # Simple Euclidean distance (fine for ~2 degree bounding box).
        dist = math.sqrt((lat - c_lat) ** 2 + (lon - c_lon) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_zip = d["zip"]
    return best_zip


app.state.built = False


@app.on_event("startup")
def _startup():
    _ensure_db_loaded()
    app.state.built = True
    print(f"[api] ready: {len(_state_files)} state files, "
          f"{len(_zip_state)} zips (lazy-loaded geometries)")


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
        # 1. Find nearest centroid (from DB metadata, no geometries loaded).
        nearest_zip = _nearest_centroid_zip(pt.lat, pt.lon)
        if not nearest_zip:
            raise HTTPException(404, "no ZCTA covers this location")
        nearest_state = _zip_state.get(nearest_zip)
        if not nearest_state:
            raise HTTPException(404, "no ZCTA covers this location")

        # 2. Try the nearest state first (fast path).
        geoms, zips_list, tree = _ensure_tree(nearest_state)
        found = _geocode_state_point(nearest_state, p, geoms, zips_list, tree)
        if found:
            row = _zip_row(con, found)
            if row:
                return _zip_payload(row)
            raise HTTPException(404, "no ZCTA covers this location")

        # 3. If not found in nearest state, try other states (for ocean/water).
        #    Load a few nearby state trees (from the bounding-box candidates).
        candidate_states = set()
        candidate_rows = con.execute(
            "SELECT DISTINCT state_fips FROM zips "
            "WHERE centroid_lat BETWEEN ? AND ? AND centroid_lon BETWEEN ? AND ?",
            (pt.lat - 2.0, pt.lat + 2.0, pt.lon - 2.0, pt.lon + 2.0)).fetchall()
        for cr in candidate_rows:
            candidate_states.add(dict(cr)["state_fips"])

        for st_fips in candidate_states:
            if st_fips == nearest_state:
                continue
            g, zl, tr = _ensure_tree(st_fips)
            res = _geocode_state_point(st_fips, p, g, zl, tr)
            if res:
                row = _zip_row(con, res)
                if row:
                    return _zip_payload(row)
                # Still no match — try nearest from this state.
                if tr is not None:
                    # Find nearest ZCTA in this state to the point.
                    dists = [(geoms[i].distance(p), zips_list[i]) for i in range(len(geoms))]
                    dists.sort(key=lambda x: x[0])
                    if dists and dists[0][0] < 2.0:
                        row = _zip_row(con, dists[0][1])
                        if row:
                            return _zip_payload(row)

        # 4. Fallback: nearest ZCTA from the nearest state (for water/coverage gaps).
        if tree is not None and nearest_state == candidate_states.pop() or True:
            dists = [(geoms[i].distance(p), zips_list[i]) for i in range(len(geoms))]
            dists.sort(key=lambda x: x[0])
            if dists and dists[0][0] < 2.0:
                row = _zip_row(con, dists[0][1])
                if row:
                    return _zip_payload(row)

        raise HTTPException(404, "no ZCTA covers this location")
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


# Cache-Control helper for /zips (data is annual, long-lived cache is safe)
_ZIP_CACHE_SEC = 30 * 24 * 3600  # 30 days


def _zip_json_response(*, scope: str, region: str, zips: int, **kw):
    """Return a JSONResponse with a 30-day Cache-Control header."""
    return JSONResponse(
        {"type": "FeatureCollection", "features": [],
         "meta": {"scope": scope, "region": region, "zips": zips}, **kw},
        headers={"Cache-Control": f"public, max-age={_ZIP_CACHE_SEC}",
                 "Vary": ""},
    )


def _resolve_state_region(region: str) -> str:
    """Resolve state region: try FIPS first, then 2-letter abbreviation."""
    fname = _state_files.get(region)
    if fname:
        return region  # already a valid FIPS
    # Try 2-letter abbreviation (case-insensitive)
    abbr = region.strip().upper()
    if abbr in _STATE_ABBR_TO_FIPS:
        return _STATE_ABBR_TO_FIPS[abbr]
    return region  # return as-is (will 404)


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
            return _zip_json_response(scope=scope, region=region, zips=len(feats),
                                      features=feats)

        if scope == "state":
            resolved = _resolve_state_region(region)
            fname = _state_files.get(resolved)
            if not fname:
                raise HTTPException(404, f"unknown state {region}")
            feats = _load_state(fname).get("features", [])
            return _zip_json_response(scope=scope, region=region, zips=len(feats),
                                      features=feats)

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
        return _zip_json_response(scope=scope, region=region, zips=len(feats),
                                  features=feats, matched=len(zips))
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
            q_stripped = q.strip()
            # When scope=state and q is a 2-letter state code, match by FIPS
            # (avoids case-insensitive LIKE matching substrings like "Samo").
            abbr = q_stripped.upper()
            if scope == "state" and abbr in _STATE_ABBR_TO_FIPS:
                clauses.append("state_fips = ?")
                params.append(_STATE_ABBR_TO_FIPS[abbr])
            else:
                clauses.append("(name LIKE ? OR id = ?)")
                params += [like, q_stripped]
        sql = (f"SELECT scope, id, name, state_fips, code FROM regions "
               f"WHERE {' AND '.join(clauses)} ORDER BY name LIMIT ?")
        params.append(limit)
        regions = [dict(r) for r in con.execute(sql, params).fetchall()]
        # Add state_abbr to each result (FIPS → 2-letter abbreviation).
        for r in regions:
            r["state_abbr"] = _STATE_FIPS_TO_ABBR.get(r["state_fips"], "")
        return {"regions": regions}
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
        "geoms_loaded": 0,  # lazy-loaded — report actual on demand
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

    # Serve maplibre-gl workers so single-server mode doesn't catch them.
    # MapLibre requests /static/worker/maplibre-gl-worker.mjs.
    worker_dir = os.path.join(dist, "static", "worker")
    if os.path.isdir(worker_dir):
        app.mount("/static/", StaticFiles(directory=worker_dir), name="workers")

    class SPAStatic(StaticFiles):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except Exception:
                return FileResponse(os.path.join(dist, "index.html"))

    app.mount("/", SPAStatic(directory=dist, html=True), name="spa")
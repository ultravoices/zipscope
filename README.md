# ZipScope

A web app that finds **your location** and shows an interactive map of **ZIP code (ZCTA) boundaries** within the selected **city**, **county**, or **state**.

- Finds you via browser geolocation → reverse-maps you to your ZCTA (point-in-polygon, no external geocoder needed) and shows your ZIP highlighted.
- Switch scope: City / County / State with searchable regions; type any 5-digit ZIP to jump to it.
- Click any ZIP area for details; shareable deep links (`?scope=county&region=48113&zip=78701`).

See [implementation-plan.md](implementation-plan.md) for design rationale and [architecture-diagram.excalidraw](architecture-diagram.excalidraw) for the architecture (opens in the Canvas tab).

## Quickstart

Requires Python 3.9+ and Node (any recent version; a copy may already live in `tools/`).

```bash
# 1. Python env (pipeline + API)
python3 -m venv .venv
./.venv/bin/pip install -r data/scripts/requirements.txt api/requirements.txt

# 2. Data (one-time, ~95 MB from census.gov; then rebuild anytime)
./.venv/bin/python data/scripts/download_sources.py
./.venv/bin/python data/scripts/build_data.py

# 3. API  (http://localhost:8000)
./.venv/bin/python -m uvicorn main:app --port 8000 --app-dir api

# 4. Web  (http://localhost:5173, proxies /api to :8000)
cd web && npm install && npm run dev
```

Open <http://localhost:5173>. Allow location, or search for any city/county/state.

**Single-server production:** `cd web && npm run build`, then start the API — FastAPI serves `web/dist` at `/` (SPA fallback included). Geolocation requires **HTTPS** (or localhost in dev).

**Tests:** `./.venv/bin/python -m pytest api/tests -q` (10 tests; skipped automatically if data isn't built).

## Project layout

```
data/
  raw/                  downloaded Census Cartographic Boundary files (gitignored)
  out/                  built artifacts: zctas/{ST}.geojson, zips.db, build-report.json (gitignored)
  scripts/
    download_sources.py official CB downloads (idempotent)
    build_data.py       M1 pipeline: parse → simplify → spatial-join → geojson + SQLite
api/
  main.py               FastAPI: /api/geocode, /api/zips, /api/regions, /api/health + SPA hosting
  tests/test_api.py
web/                    Vite + React + TS + MapLibre GL (see web/src)
```

## Data sources (all official, key-free)

| Data | Source | Vintage |
|---|---|---|
| ZIP polygons (ZCTA) | Cartographic Boundary `cb_2020_us_zcta520_500k` | 2020, 1:500k |
| Counties | `cb_2025_us_county_5m` | 2025, 1:5m |
| States | `cb_2025_us_state_5m` | 2025, 1:5m |
| Cities (places) | `cb_2025_us_place_500k` | 2025, 1:500k |

ZCTAs are Census *Zip Code Tabulation Areas* — the standard boundary approximation of USPS ZIPs (USPS publishes no boundary file).

## Deviations from the plan (deliberate, environment-driven)

- **Population is NULL.** The Census API now returns "Missing Key" for keyless clients and doesn't support ZCTA anyway; no key-free per-ZCTA population source was available in this environment. UI handles nulls (area choropleth instead of population). Adding a Census API key unlocks population + the newer 2025 ZCTA vintage.
- **ZCTA vintage is 2020** — the 2025 CB release dropped ZCTAs; 2020 is the last release with them.
- **GeoJSON, not TopoJSON**, in `data/out` (simpler end-to-end; per-state files are 0.4–4.3 MB).
- **No boundary clipping** for multi-county ZIPs (they render whole); county/city scope lists *do* use true geometry intersection (many-to-many).

## Deployment

Any static host for `web/dist` + any Python host for `api/` (or the single-server mode above). The data layer is self-contained in `data/out` (~75 MB) — upload it next to the code. For a permanent deploy, put it behind TLS and set `VITE_API_BASE` if the API is on a different origin.

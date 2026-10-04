# ZipScope

A web app that finds **your location** and shows an interactive map of **ZIP code (ZCTA) boundaries** within the selected **city**, **county**, or **state**.

- Browser geolocation → reverse-mapped to your ZCTA via in-house point-in-polygon (no external geocoder) — your ZIP is highlighted in red.
- Switch scope: **City / County / State** with searchable regions; type any 5-digit ZIP to jump straight to it.
- Click any ZIP area for details; hover for a quick tooltip; shareable deep links (`?scope=county&region=48113&zip=78701`).
- A live **ZIP count chip** and on-map error banners make data vs. render failures visible instead of a silently blank map.

Companion docs: [implementation-plan.md](implementation-plan.md) (design rationale + full execution status, §11) · [architecture-diagram.excalidraw](architecture-diagram.excalidraw) (Canvas tab).

---

## Current state (2026-10-04)

**Fully built and running locally.** All plan milestones M1–M4 complete; M5 (hardening) mostly done.

| Layer | Status |
|---|---|
| Data pipeline (M1) | ✅ 33,791 ZCTAs · 56 states · 3,235 counties · 32,629 places · 113,146 zip↔region links. Per-state GeoJSON (0.4–4.3 MB, 62.6 MB total) + `zips.db` (14.7 MB). Validation asserts pass. |
| API (M2) | ✅ FastAPI — `/api/geocode`, `/api/zips`, `/api/regions`, `/api/health` + SPA hosting. **10/10 tests green** (`pytest api/tests`). |
| Web (M3/M4) | ✅ React 19 + Vite 8 + MapLibre GL v6: OSM basemap, area choropleth, hover/click/tooltip, scope search, ZIP list, detail card, you-are-here marker, URL deep links, mobile layout. |
| Hardening (M5) | ⚠️ README, tests, git history, single-server prod mode, loading/error states, defensive map rendering. **Not done:** automated annual-refresh cron, staging deploy (no infra in this env), perf pass on biggest states. |

**Latest fix (2026-10-04): "map loads but no ZIP polygons."** The map renderer was rewritten to be defensive and observable — layer-add failures are caught and surfaced in a red on-map banner, highlights (your ZIP = red, selected = orange) moved to a separate source, and the app now shows a "N ZIP areas" count chip plus a geocode fallback (county → state when a ZIP lacks a county FIPS). Verified: production build green, 10/10 API tests, live endpoints return data through the dev proxy, app running at <http://localhost:5173>.

### Running now (this machine)

| Service | URL | Command |
|---|---|---|
| API | <http://localhost:8000/api/health> | `./.venv/bin/python -m uvicorn main:app --port 8000 --app-dir api` |
| Web (dev) | <http://localhost:5173> | `cd web && PATH=../tools/node/bin:$PATH npm run dev` |

Node is not installed system-wide; a standalone Node 22 tarball lives in `tools/` (gitignored) and is put on `PATH` as shown.

---

## Quickstart (from scratch)

Requires Python 3.9+ and Node (recent; `tools/node` on this machine).

```bash
# 1. Python env (pipeline + API)
python3 -m venv .venv
./.venv/bin/pip install -r data/scripts/requirements.txt api/requirements.txt

# 2. Data (one-time, ~95 MB from census.gov; rebuild anytime, idempotent)
./.venv/bin/python data/scripts/download_sources.py
./.venv/bin/python data/scripts/build_data.py

# 3. API  (http://localhost:8000)
./.venv/bin/python -m uvicorn main:app --port 8000 --app-dir api

# 4. Web  (http://localhost:5173, proxies /api to :8000)
cd web && npm install && npm run dev
```

Open <http://localhost:5173>. Allow location, or search any city / county / state.

- **Single-server production:** `cd web && npm run build`, then start the API — FastAPI serves `web/dist` at `/` with SPA fallback. Geolocation requires **HTTPS** in non-localhost deployments.
- **Tests:** `./.venv/bin/python -m pytest api/tests -q` (10 tests; skipped automatically if data isn't built).

---

## Architecture

```
Census CB files ──► build_data.py ──► data/out (per-state GeoJSON + zips.db)
                                              │
Browser (React SPA) ◄── /api/* ──► FastAPI (FastAPI)
  MapView (MapLibre GL)                 point-in-polygon geocode,
  ScopeSelector                         per-state lazy cache,
  ZipList / DetailCard                  SQLite lookup + STRtree
  useGeolocation
```

**First-paint flow:** geolocation → `POST /api/geocode` → ZIP + its county (falls back to state when a county FIPS is missing) → `GET /api/zips?scope=…&region=…` → map fits to the loaded polygons. The client only ever loads the *current scope's* polygons — the server does all filtering.

### Project layout

```
data/
  raw/                  downloaded Census Cartographic Boundary zips (gitignored)
  out/                  built: zctas/{ST}.geojson, zips.db, build-report.json (gitignored)
  scripts/
    download_sources.py idempotent downloads (4 files, size-sanity checks)
    build_data.py       M1: pyshp parse → simplify → STRtree spatial joins → GeoJSON + SQLite
api/
  main.py               FastAPI: /api/geocode, /api/zips, /api/regions, /api/health + SPA hosting
  tests/test_api.py     10 endpoint tests (incl. multi-county ZIP, ocean 404, DC=11)
web/                    Vite 8 + React 19 + TS + MapLibre v6 (src/MapView, App, api, ScopeSelector, ZipList, DetailCard, useGeolocation, style.css)
tools/node              bundled Node 22 (gitignored)
```

**Database tables:** `zips` (primary state/county/city, centroid, area, pop=NULL), `regions` (states/counties/places w/ FIPS + `code`), `zip_regions` (many-to-many zip↔county/place).

---

## Data sources (all official, key-free)

| Data | Source | Vintage |
|---|---|---|
| ZIP polygons (ZCTA) | Cartographic Boundary `cb_2020_us_zcta520_500k` | 2020, 1:500k |
| Counties | `cb_2025_us_county_5m` | 2025, 1:5m |
| States | `cb_2025_us_state_5m` | 2025, 1:5m |
| Cities (places) | `cb_2025_us_place_500k` | 2025, 1:500k |

ZCTAs are Census *Zip Code Tabulation Areas* — the standard boundary approximation of USPS ZIPs (USPS publishes no boundary file).

---

## API

| Endpoint | Params | Returns |
|---|---|---|
| `POST /api/geocode` | `{lat, lon}` | ZCTA containing the point (STRtree; nearest within ~2° for gaps/water), with state/county/city + centroid. 404 if uncovered. |
| `GET /api/zips` | `scope=state\|county\|place\|zip`, `region=<fips or zip>`, `state?` | GeoJSON FeatureCollection of the scope's ZCTAs + `meta.zips` count. County/place use true geometry intersection (many-to-many). |
| `GET /api/regions` | `q?`, `scope?`, `state?`, `limit?` | Region search index for the selector (LIKE on names). |
| `GET /api/health` | — | `ok`, `geoms_loaded`, data provenance (vintages + source URLs). |

CORS is open for the dev origins; per-state GeoJSON is lazily loaded into an in-memory cache on first request.

---

## Deviations from the plan (deliberate, environment-driven)

- **Population is NULL.** The 2026 Census API redirects keyless clients to a "Missing Key" page and never supported ZCTA geography; no key-free per-ZCTA population source existed in this environment. The UI renders population *when present* and uses an **area choropleth** meanwhile. Adding a Census API key is the fastest path to population + newer ZCTA vintages.
- **ZCTA vintage is 2020** — the 2025 CB release dropped ZCTAs; 2020 is the last release that included them. (Old TIGER shapefile URLs 404; the pipeline pins the Cartographic Boundary `GENZ{year}` releases and handles both 2020 (`STATE`/`COUNTY`) and 2025 (`STATEFP`/`COUNTYFP`) field generations.)
- **GeoJSON, not TopoJSON**, in `data/out` (simpler end-to-end; per-state files 0.4–4.3 MB — MapLibre handles 1,700+ polygons fine).
- **No boundary clipping** for multi-county ZIPs (e.g. 63101 renders whole, listed under all counties it touches). v1.1 backlog.
- **pyshp + shapely** instead of GeoPandas (Python 3.9, avoids the GDAL dependency).
- **~24 ZCTAs** have no assigned state (territory/edge cases) — below the 1% validation threshold, in the `?` bucket.
- **No Node on this machine** → standalone Node 22 in `tools/` (gitignored); `VITE` + `rolldown` template, `optimizeDeps.exclude: ["maplibre-gl"]` (the dep-optimizer chokes on its worker module).

---

## Known quirks & troubleshooting

- **Blank map with no ZIPs?** Check the on-map banners: a red "Map: …" banner names the failing layer; a missing "N ZIP areas" chip means the fetch didn't land. The sidebar ZIP list works regardless of WebGL.
- **Geolocation in the in-app browser** may be simulated or denied — the manual search path (city/county/state/ZIP) is fully functional without it.
- **ZCTAs are 1:500k** — coarse/blocky at city zoom; that's the best available key-free vintage.
- **Census.gov soft-404s** (HTTP 200 + "Page not found" HTML) — `download_sources.py` sanity-checks file sizes; re-run it after each annual CB release.

## Deployment

Any static host for `web/dist` + any Python host for `api/`, or the single-server mode above. The data layer is self-contained in `data/out` (~77 MB) — ship it next to the code. For a permanent deploy, put it behind **TLS** (geolocation requirement) and set `VITE_API_BASE` at build time if the API lives on a different origin.

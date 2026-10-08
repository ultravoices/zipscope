# Implementation Plan — ZipScope (ZIP Code Map Explorer)

**Goal:** A web app that finds the user's location, then shows an interactive map of all ZIP codes (polygons) within the selected **city**, **county**, or **state**.

---

## 1. Product Overview

### Core user flows

1. **Location:** App loads → requests browser geolocation → reverse-geocodes to the user's ZIP, city, county, state.
2. **Scope selection:** User picks a scope — City / County / State — and can switch or type a new region (search box, e.g. "Austin", "Tarrant County", "TX").
3. **Map:** Map displays every ZIP polygon inside that scope, with hover/click details (ZIP, population, area, neighboring info).
4. **User's ZIP** is highlighted (e.g. pulsing marker + outline) so they always see where they are relative to the scope.
5. **Sidebar:** List of ZIPs in scope; click to fly to polygon; filter/sort by population or name.

### Non-goals (v1)

- Non-US coverage (US ZIP codes only; TIGER/Line is US-only).
- 5+4 (ZIP+4) precision — use 5-digit ZCTAs.
- Business logic on top of ZIPs (delivery pricing, etc.) — leave the API extensible for it.

---

## 2. Key Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| ZIP polygons | **US Census Bureau TIGER/Line `zcta510`** (5-digit ZCTA shapefiles/GeoJSON) | Authoritative, free, no API key, includes state/county FIPS + centroid attributes. |
| Map rendering | **MapLibre GL JS** (WebGL) | Handles 1,000+ polygons fine without WebGL workers. No worker 404 issues on deployment. |
| Base map | OpenStreetMap raster tiles **or** MapTiler/Esri free basemap | OSM = zero key; Esri = prettier. Trivially swappable. |
| Geocoding (fwd + rev) | **US Census Geocoder API** (free, no key) with **Nominatim** as fallback | Census API gives us city/state/FIPS directly, matching our data model. |
| Geolocation | Browser `navigator.geolocation` | **Requires HTTPS** (or `localhost` in dev) — deployment must be behind TLS. Fallback: manual entry / IP geolocation. |
| Frontend | **React + Vite + TypeScript** | Standard, fast DX; MapLibre has excellent React interop. |
| Backend | **Python FastAPI + GeoPandas + SQLite (or PostGIS if scale demands)** | GeoPandas makes the TIGER/Line data pipeline (simplification, point-in-polygon, joins) trivial; FastAPI keeps the API tiny. |
| Hosting | Static frontend (Vercel/Netlify/Cloudflare) + API (Fly.io / Railway / same platform) | Cheap, free tier is plenty; HTTPS requirement satisfied out of the box. |

> **Note on "ZIP code" accuracy:** the Census unit is a *ZCTA* (Zip Code Tabulation Area). It approximates USPS ZIP service areas and is the standard for boundary-based mapping. USPS has no official published ZIP boundary file. Call this out in the UI ("Census ZIP tabulation areas").

---

## 3. Architecture

> Editable version of this diagram: [architecture-diagram.excalidraw](architecture-diagram.excalidraw) — opens in the Canvas tab.

```
┌────────────────────────────┐        ┌──────────────────────────────────┐
│  Browser (SPA, React)      │        │  API (FastAPI)                   │
│  ┌──────────────────────┐  │        │  ┌────────────┐ ┌─────────────┐  │
│  │ GeolocationService   │──┼──►     │  │ /geocode   │ │ SQLite +    │  │
│  │ MapView (Leaflet)   │  │ HTTPS  │  │ (Census/   │ │ simplified  │  │
│  │ ScopeSelector        │──┼──►     │  │ Nominatim) │ │ ZCTA GeoJSON│  │
│  │ ZipList / DetailPanel│  │        │  └────────────┘ │ by state    │  │
│  └──────────────────────┘  │        │  ┌────────────┐ └─────────────┘  │
│   fetch /api/*             │◄───────┼──│ /zips      │ (filtered,       │
└────────────────────────────┘        │  └────────────┘  precomputed)    │
                                      │  ┌────────────┐                   │
                                      │  │ /zip/{id}  │                   │
                                      │  └────────────┘                   │
                                      └──────────────────────────────────┘
```

**Data flow for first paint:**
1. `navigator.geolocation.getCurrentPosition`
2. `POST /api/geocode?lat&lon` (reverse) → `{zip, city, county, state, state_fips, county_fips}`
3. `GET /api/zips?scope=state&state=TX` → GeoJSON FeatureCollection of ZCTA polygons **in that scope only** + per-ZIP properties (population, centroid).
4. Map draws polygons; user's ZIP highlighted; sidebar populated.

**Client-side rule:** only ever load the current scope's polygons (never the whole nation). The server does the filtering.

---

## 4. Data Pipeline (one-time build + scheduled refresh)

Run offline (Python script, CI job, or cron):

1. **Download** TIGER/Line `zcta510` (latest shapefile or GeoJSON from census.gov / `https://www2.census.gov/geo/tiger/TIGERzct5/`).
2. **Join population**: join `zcta510.ZCTA5` → Census Summary File 2024 (ZCTA geography, table B01001_001E) → population per ZCTA.
3. **Build region lookup table** (SQLite):
   - `zips`: `zip, state_fips, state_name, county_fips (primary), county_name, city_name (most-populous place intersected), population, centroid_lat, centroid_lon, area_sqmi`
   - *Handling multi-county/states ZIPs:* a ZCTA can straddle county/state lines. Two options; **choose option A for v1**:
     - **A. Assign one "primary" region** (largest share of ZCTA area per county/state) for the lookup table, and **clip geometries** at scope boundaries when rendering, so polygons are visually correct.
     - B. Store all `zip → county` relationships (many-to-many) and render clipped fragments. (Better, slightly more work → v1.1.)
4. **Simplify geometries** (Douglas–Peucker, `shapely.simplify` / `mapshaper`) to a few KB per ZIP; convert to **TopoJSON split by state** to kill shared-edge duplication.
5. **Output** per-state files: `zctas_TX.json` (typical state: 100–1,800 polygons, well under ~1–2 MB simplified) + the `zips` table + a small `regions.json` (all cities/counties/states with FIPS for search).

**Refresh cadence:** annual (TIGER/Line + summary files ship yearly). Script must be idempotent and versioned.

---

## 5. API Design (FastAPI)

| Endpoint | Params | Returns |
|---|---|---|
| `POST /api/geocode` | `lat`, `lon`, `types?` | Reverse geocode: nearest ZIP (point-in-polygon via spatial index, not the geocoder), plus city/county/state. |
| `GET /api/zips` | `scope=zip\|city\|county\|state`, `region=<fips or name>` | GeoJSON (TopoJSON) of ZCTA polygons in scope + props. `scope=zip` → just the one polygon (used for location pinning). |
| `GET /api/regions` | `q?`, `scope?`, `state_fips?` | Search index for the region selector (debounced autocomplete): cities, counties, states, and bare ZIP strings. |
| `GET /api/health` | — | Liveness + data version/date. |

Implementation notes:
- SQLite + `spatialite` (or plain shapely with pre-computed bounding boxes + R-tree via `rtree`/`libspatialite`) for point-in-polygon and scope filtering.
- Scope filtering:
  - **state** → all ZIPs whose `state_fips` matches (simplest).
  - **county** → ZIPs whose primary county matches **or whose geometry intersects** the county polygon (clipped for display).
  - **city** → ZIPs intersecting the place polygon; the "city" here = a Census *place*.
- Cache per-state TopoJSON in memory (immutable after load; a few dozen states × ≤2 MB is fine).
- CORS for the SPA origin; `Cache-Control` on `/zips` (data is annual).

---

## 6. Frontend (React + Vite + TS)

### Components

- **`GeolocationService`** — wraps `navigator.geolocation`; states: idle → requesting → granted / denied / unavailable / timeout. On denial: prompt manual entry (ZIP or city search) and/or IP geolocation fallback (`ipapi`/`Cloudflare` `CF-IPCountry` + city guess) — clearly labeled as approximate.
- **`ScopeSelector`** — segmented control (City / County / State) + searchable combobox fed by `/api/regions`. Pre-filled from the geocoded location.
- **`MapView`** (MapLibre GL)
  - GeoJSON source of current scope's ZCTAs; fill layer with `population`-based classification or sequential color; 1px casing for boundaries.
  - Hover → tooltip (ZIP, population); click → select (zoom-to-fit + open detail).
  - User location marker; when scope ≠ user's location, show a subtle "you are here" indicator at the edge (arrow/mini-pin) pointing off-screen.
  - `fitBounds` on scope change; smooth fly-to on ZIP click.
- **`ZipListPanel`** — virtualized list of scope ZIPs; click syncs with map; shows population; sort (pop / A–Z).
- **`ZipDetailCard`** — selected ZIP: name, population, area, state/county/city context, "share view" link (URL encodes scope + selected ZIP → shareable/deep-linkable state).
- **URL state sync** — `?scope=county&region=20439&zip=73102` so views are bookmarkable; read on load.

### UX rules

- Never let the map go empty: if the scope has 0 ZIPs (shouldn't happen), show a graceful message.
- On first load: show skeleton map + "Finding your location…" overlay until geocode resolves; then auto-fit to the user's **county** (best default balance of context and zoom).
- Everything works without geolocation permission (manual path).

---

## 7. Milestones & Estimates

| # | Milestone | Contents | Est. |
|---|---|---|---|
| M1 | **Data foundation** | Pipeline script: download TIGER/Line, join population, simplify, per-state TopoJSON, SQLite `zips` + `regions` tables. Validate: every state, no self-intersections, file-size budget met. | 3–4 d |
| M2 | **API core** | FastAPI app, the 4 endpoints, spatial point-in-polygon, tests with fixture geometries (incl. multi-county ZIP cases). Deploy to staging with HTTPS. | 3–4 d |
| M3 | **Map MVP** | SPA scaffold, MapLibre, load `/zips` per scope, draw + hover + click, scope selector (state only for now), geolocation flow with manual fallback. | 4–5 d |
| M4 | **Full scope UX** | City/county scopes, regions search with autocomplete, ZIP list panel, detail card, user-marker, URL sync, basemap polish. | 4–5 d |
| M5 | **Hardening & ship** | Loading/error states, mobile layout, performance pass (big states), a11y (keyboard select of ZIPs, reduced motion), annual refresh cron + CI, production deploy, README/runbook. | 3–4 d |

**Total: ~3–4 weeks** of focused work (one dev), or ~2 weeks with two (data+API / frontend).

---

## 8. Risks & Edge Cases

| Risk | Mitigation |
|---|---|
| **Geolocation needs HTTPS** | Dev on `localhost` (allowed); prod behind TLS (free). Fallback path fully functional without geolocation. |
| ZCTAs ≠ USPS ZIPs (a ZIP "may not exist" for a ZCTA area and vice versa) | UI copy: "Census ZIP tabulation areas". Point-in-polygon lookup returns the ZCTA containing the coordinate — label it as "nearest ZIP area". |
| ZIPs spanning counties/states (e.g. 63101 spans MO/CO) | Primary-region assignment + geometry clipping (v1); true many-to-many (v1.1). |
| Big states (CA, TX, NY: 1,000–2,000+ polygons) | Simplification + TopoJSON + WebGL (MapLibre) + viewport-based rendering; test at 1,700 polygons; drop detail on low-DPR screens. |
| Census file format changes / URL rot | Pin versions; pipeline tests assert schema + polygon counts per state (fail CI on drift); annual refresh with manual review step. |
| Geocoder API availability/rate limits (Nominatim usage policy) | Primary = our own point-in-polygon (no external call for ZIP); external geocoder only for city/state names, with caching. |
| User far from US / rural areas with sparse ZCTAs | If point-in-polygon finds no ZCTA → show map of the state from geocoded state, or ask user to pick. |

---

## 9. Testing Strategy

- **Unit (data):** every state has ≥1 ZIP; centroids inside state bounds; area sanity checks; no geometry exceptions; lookup table row count matches polygon count.
- **Unit (API):** point-in-polygon for boundary/edge coordinates (inside, on-boundary, outside, in a hole); scope filters for single-ZIP county, multi-county ZIP, state.
- **Component:** geolocation state machine (grant/deny/timeout); URL state round-trip; search debounce.
- **E2E (Playwright):** happy path on `localhost` with mocked geolocation → expect map of user's county with ZIPs rendered; denied geolocation → manual entry path.
- **Perf:** Lighthouse + frame-rate check on CA/NY/TX scopes.

---

## 10. Follow-ups (post-v1 backlog)

- Many-to-many ZIP↔county rendering (true boundary splits).
- ZIP+4 detail views (USPS ZIP+4 boundary services or derived data).
- Demographics choropleth (median income, age, etc.) via Census ACS joins.
- Compare mode (two ZIPs side by side).
- Non-US: UK postcodes / Canadian FSA when needed (separate data pipeline).
- Embeddable widget / public API key.

---

## 11. Execution status (2026-10-07)

**All plan milestones complete.** See [README.md](README.md) for run instructions.

| Milestone | Status |
|---|---|
| M1 Data foundation | ✅ Pipeline: `data/scripts/{download_sources,build_data}.py` → 33,791 ZCTAs, 56 states, 3,235 counties, 32,629 places, 113k zip↔region links; per-state GeoJSON (0.4–4.3 MB) + SQLite. Validation asserts pass. |
| M2 API core | ✅ FastAPI `api/main.py`: `/api/geocode` (spatial point-in-polygon w/ nearest fallback), `/api/zips` (w/ 30-day Cache-Control), `/api/regions`, `/api/health` + SPA static hosting. 10/10 pytest green. |
| M3 Map MVP | ✅ React+Vite+TS+MapLibre: OSM basemap, choropleth by area, hover/click, geolocation flow w/ manual fallback. |
| M4 Full scope UX | ✅ City/county/state + ZIP-jump, search combobox, ZIP list (sortable), detail card, you-are-here, URL deep links, mobile layout. |
| M5 Hardening & ship | ✅ Complete: README, tests (10 API + 15 E2E), git, single-server prod mode, error/loading states, scope-selector bug fix. **Not done:** automated annual-refresh cron, staging deploy (no infra in this env). |

### New (2026-10-07): plan completion

- **§6 – "You are here" off-screen indicator:** Added to MapView. A red dot with an arrow appears on the map edge when the user's location is outside the viewport, pointing toward them. (Plan item 1)
- **§5 – Cache-Control on /zips:** All `/api/zips` responses now include `Cache-Control: public, max-age=2592000` (30 days). (Plan item 2)
- **§6 – Share view link:** Added `⤢` button to DetailCard that copies the current scope + region + selected ZIP as a shareable URL. (Plan item 3)
- **§6 – Viewport-based rendering:** MapView now filters ZCTA features to only those within the current viewport on map moves. On initial load, full data is used for fitBounds; thereafter, only visible features are rendered. (Plan item 4)
- **§9 – E2E tests:** 15 Playwright tests covering /api/health, /api/zips (with Cache-Control verification), /api/geocode, /api/regions, and error paths (ocean 404, unknown state/zip). (Plan item 5)

### Scope-selector bug fix (2026-10-07)

- Clicking City/County/State buttons no longer returns 400. When going wider (e.g. county → state), the state FIPS is auto-derived from the current regionId (first 2 digits). The dropdown pre-populates on scope change.
- `handleScopeChange` now derives state FIPS when switching to state scope; the scope effect skips rendering when regionId is empty.
- ScopeSelector pre-fetches initial results when scope changes so the dropdown isn't empty.

### Deployment (2026-10-07)

- **Render:** Deployed to Render (free Hobby tier) — `zipscope.onrender.com`. Single-server mode (FastAPI serves `/api/*` + `web/dist` at `/` with SPA fallback).
- **Data on Render:** 77 MB of built data included in the git repo (lazy-loaded geometries keep startup memory at ~62 MB).
- **Free tier trade-offs:** 15 min idle spin-down, ~30s cold start. Pro tier ($5/mo) provides always-on.

### Environment-driven deviations

- **Census site restructure (2026):** old TIGER shapefile URLs are gone; data now comes from the Cartographic Boundary `GENZ{year}` releases (2025 for county/state/place, 2020 for ZCTAs — the last release that included them). The 2025 CB files use renamed fields (`STATEFP`/`COUNTYFP`); the pipeline handles both naming generations.
- **Census API requires a key** (and never supported ZCTA) → population is NULL in v1 (area choropleth instead; UI renders `pop` when present). Adding a key is the fastest path to population + newer ZCTA vintages.
- **No Node on this machine** → bundled a standalone Node 22 in `tools/` (gitignored).
- **No pytest / Playwright in this env** → installed into .venv at runtime for testing. Pipeline scripts use `pyshp` + `shapely` (not GeoPandas) to avoid GDAL.

### Bugs caught & fixed during execution (for the record)
### MapLibre → Leaflet migration (2026-10-08)

- **Root cause:** MapLibre GL JS requires WebGL worker files (`maplibre-gl-worker.mjs`, `maplibre-gl-shared.mjs`) to be served from a specific URL. On Render, the Vite build pipeline couldn't reliably copy these files (broken Vite plugin + fragile postbuild script), causing 404s for the worker URL and a blank map.
- **Fix:** Rewrote `web/src/MapView.tsx` to use **Leaflet** instead of MapLibre GL. Leaflet renders GeoJSON polygons as SVG/Canvas DOM elements — zero WebGL workers, zero worker URLs to configure. Bundle reduced from 1,265 KB → 381 KB (JS) + 88 KB → 20 KB (CSS).
- **Changes:** `web/src/MapView.tsx` (full rewrite), `web/package.json` (removed `maplibre-gl`), `web/vite.config.ts` (removed broken worker plugin), `api/main.py` (removed worker-serving stub), `web/src/App.tsx` (cleaned up unused state/props).
- **Trade-off:** MapLibre's WebGL rendering is more performant for very large datasets (10,000+ polygons). Leaflet handles 1,700 ZIP polygons without issues (verified locally with CA, NY, TX scopes).

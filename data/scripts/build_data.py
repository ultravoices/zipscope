#!/usr/bin/env python3
"""ZipScope M1 — data pipeline.

Reads official US Census Cartographic Boundary (CB) shapefiles from data/raw/ and builds:

  data/out/zctas/{ST}.geojson   per-state simplified ZCTA polygons (WGS84)
  data/out/zips.db              SQLite: zips, regions, zip_regions tables
  data/out/build-report.json    provenance + validation summary

Sources (all official US Census Bureau, WGS84):
  - ZCTA polygons : cb_2020_us_zcta520_500k  (2020 Cartographic Boundary release,
                       1:500,000 scale; last release that included ZCTAs)
  - Counties      : cb_2025_us_county_5m     (1:5,000,000)
  - States        : cb_2025_us_state_5m
  - Places/cities : cb_2025_us_place_500k    (1:500,000)

Population is NOT available without a Census API key in this environment, so the
`population` column is left NULL (see implementation-plan.md, Follow-ups).

Run:  ./.venv/bin/python data/scripts/build_data.py
Deps: pyshp, shapely (see data/scripts/requirements.txt)
"""

import json
import math
import os
import sqlite3
import time
import zipfile

import shapefile
from shapely.geometry import shape as shp_shape
from shapely.strtree import STRtree

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "out")

SOURCES = {
    "zctas": {
        "file": "cb_2020_us_zcta520_500k.zip",
        "filebase": "cb_2020_us_zcta520_500k",
        "url": "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip",
        "vintage": "2020 (1:500,000)",
    },
    "counties": {
        "file": "cb_2025_us_county_5m.zip",
        "filebase": "cb_2025_us_county_5m",
        "url": "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_5m.zip",
        "vintage": "2025 (1:5,000,000)",
    },
    "states": {
        "file": "cb_2025_us_state_5m.zip",
        "filebase": "cb_2025_us_state_5m",
        "url": "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_5m.zip",
        "vintage": "2025 (1:5,000,000)",
    },
    "places": {
        "file": "cb_2025_us_place_500k.zip",
        "filebase": "cb_2025_us_place_500k",
        "url": "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_place_500k.zip",
        "vintage": "2025 (1:500,000)",
    },
}

SIMP_TOL = 0.0012           # degrees (~130 m) extra generalization for web payloads
COORD_DIGITS = 5            # ~1 m coordinate rounding
PLACE_SLIVER_FRAC = 0.02    # zcta-place join threshold: >= 2% of zcta area
COUNTY_SLIVER_FRAC = 0.005  # zcta-county join threshold: >= 0.5% of zcta area


def log(msg):
    print(f"[build] {msg}", flush=True)


def extract_shape(zip_path, filebase):
    """Unzip a CB archive to a temp dir and return the pyshp Reader."""
    import tempfile
    d = tempfile.mkdtemp(prefix="zipscope_")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(d)
    shp_path = os.path.join(d, filebase + ".shp")
    if not os.path.exists(shp_path):
        # fall back to a prefix match (skip label/qualifier companions like *_l, *_q)
        matches = [f for f in os.listdir(d)
                   if f.endswith(".shp") and f.startswith(filebase.split("_")[-2] + "_")
                   and not f.endswith(("_l.shp", "_q.shp"))]
        if len(matches) == 1:
            shp_path = os.path.join(d, matches[0])
        else:
            available = [f for f in os.listdir(d) if f.endswith(".shp")]
            raise FileNotFoundError(f"{filebase}.shp not in {zip_path}; has: {available}")
    prj = os.path.join(d, filebase + ".prj")
    if os.path.exists(prj):
        prj_txt = open(prj).read()
        if "GEOGCS" not in prj_txt:
            raise ValueError(f"{filebase}: expected geographic CRS, got: {prj_txt[:120]}")
    return shapefile.Reader(shp_path)


def load_shapes(reader, field_map):
    """Return [(shapely_geom, props_dict)].

    field_map: our_key -> [candidate field names] (CB renamed fields in 2025,
    e.g. STATE -> STATEFP; 2020 files use the old names). pyshp keeps a
    DeletionFlag meta entry in Reader.fields but not in record values, so the
    value row is aligned to fields[1:].
    """
    names = [x[0] for x in reader.fields]
    out = []
    for rec in reader.iterShapeRecords():
        geo = rec.shape.__geo_interface__
        if geo["type"] not in ("Polygon", "MultiPolygon"):
            continue
        g = shp_shape(geo)
        if g is None or g.is_empty:
            continue
        if not g.is_valid:
            try:
                g = g.buffer(0)
            except Exception:
                continue
            if g is None or g.is_empty:
                continue
        vals = []
        for i in range(len(names) - 1):
            try:
                vals.append(rec.record[i])
            except (IndexError, KeyError):
                break
        d = dict(zip(names[1:], vals))
        props = {}
        for our_key, candidates in field_map.items():
            for cand in candidates:
                if d.get(cand) is not None:
                    props[our_key] = str(d[cand])
                    break
        out.append((g, props))
    return out


def approx_area_sqmi(geom, lat):
    a = abs(geom.area)
    m_per_deg = 111_320.0 * math.cos(math.radians(lat))
    return (a * m_per_deg * m_per_deg) / (1609.344 ** 2)


def round_ring(ring):
    return [[round(c, COORD_DIGITS) for c in pt] for pt in ring.coords]


def geom_to_geojson(g):
    if g.geom_type == "Polygon":
        return {"type": "Polygon",
                "coordinates": [round_ring(g.exterior)] + [round_ring(h) for h in g.interiors]}
    polys = [[round_ring(p.exterior)] + [round_ring(h) for h in p.interiors] for p in g.geoms]
    return {"type": "MultiPolygon", "coordinates": polys}


def main():
    t0 = time.time()
    os.makedirs(os.path.join(OUT, "zctas"), exist_ok=True)

    # ---------------- load ----------------
    log("loading zctas (this is the big one)...")
    zctas = load_shapes(extract_shape(os.path.join(RAW, SOURCES["zctas"]["file"]), SOURCES["zctas"]["filebase"]),
                        {"zip": ["ZCTA5CE20", "ZCTA5", "NAME20"]})
    log(f"zctas: {len(zctas)}")
    log("loading counties...")
    counties = load_shapes(extract_shape(os.path.join(RAW, SOURCES["counties"]["file"]), SOURCES["counties"]["filebase"]),
                           {"state_fips": ["STATEFP", "STATE"], "county_fips": ["COUNTYFP", "COUNTY"],
                            "name": ["NAME"]})
    log(f"counties: {len(counties)}")
    log("loading states...")
    states = load_shapes(extract_shape(os.path.join(RAW, SOURCES["states"]["file"]), SOURCES["states"]["filebase"]),
                         {"state_fips": ["STATEFP", "STATE"], "name": ["NAME"], "stusps": ["STUSPS"]})
    log(f"states: {len(states)}")
    log("loading places...")
    places = load_shapes(extract_shape(os.path.join(RAW, SOURCES["places"]["file"]), SOURCES["places"]["filebase"]),
                         {"state_fips": ["STATEFP", "STATE"], "place_fips": ["PLACEFP", "PLACE"],
                          "name": ["NAME"]})
    log(f"places: {len(places)}")

    state_name = {p["state_fips"]: p["name"] for _, p in states if p.get("state_fips") and p.get("name")}
    state_stusps = {p["state_fips"]: p.get("stusps", "") for _, p in states if p.get("state_fips")}
    county_name = {(p["state_fips"] + p["county_fips"]): p["name"]
                   for _, p in counties if p.get("state_fips") and p.get("county_fips") and p.get("name")}

    # ---------------- simplify zctas ----------------
    t = time.time()
    fixed = 0
    simplified = []
    for g, p in zctas:
        try:
            s = g.simplify(SIMP_TOL, preserve_topology=True)
        except Exception:
            continue
        if s is None or s.is_empty:
            continue
        if not s.is_valid:
            fixed += 1
            try:
                s = s.make_valid()
            except Exception:
                try:
                    s = s.buffer(0)
                except Exception:
                    continue
            if s is None or s.is_empty:
                continue
        simplified.append((s, p))
    zctas = simplified
    log(f"simplified zctas in {time.time() - t:.1f}s: {len(zctas)} (repaired {fixed} invalid)")

    # ---------------- spatial indexes ----------------
    st_tree = STRtree([g for g, _ in states])
    st_geoms = [g for g, _ in states]
    st_props = [p for _, p in states]
    co_tree = STRtree([g for g, _ in counties])
    co_geoms = [g for g, _ in counties]
    co_props = [p for _, p in counties]
    pl_tree = STRtree([g for g, _ in places])
    pl_geoms = [g for g, _ in places]
    pl_props = [p for _, p in places]

    zcta_records = []
    zip_regions = []
    unassigned_state = 0
    for g, p in zctas:
        zip5 = str(p.get("zip") or "")
        if len(zip5) != 5 or not zip5.isdigit():
            continue
        c = g.centroid
        lat, lon = c.y, c.x
        area_deg = g.area
        area_sqmi = approx_area_sqmi(g, lat) if area_deg > 0 else 0.0

        # primary state = state containing the centroid
        st_fips = ""
        for idx in st_tree.query(c.buffer(0.01)):
            if st_geoms[idx].contains(c):
                st_fips = st_props[idx]["state_fips"]
                break
        if not st_fips:
            unassigned_state += 1

        # counties intersecting (primary = largest share; keep all non-sliver for scope lists)
        co_hits = []
        for idx in co_tree.query(g):
            try:
                inter = g.intersection(co_geoms[idx])
            except Exception:
                continue
            a = inter.area if not inter.is_empty else 0.0
            if area_deg <= 0 or a >= area_deg * COUNTY_SLIVER_FRAC:
                co_hits.append((a, idx))
        co_hits.sort(reverse=True)
        primary_co = co_hits[0][1] if co_hits else None

        # places intersecting (primary = largest share; keep non-sliver)
        pl_hits = []
        for idx in pl_tree.query(g):
            try:
                inter = g.intersection(pl_geoms[idx])
            except Exception:
                continue
            a = inter.area if not inter.is_empty else 0.0
            if area_deg <= 0 or a >= area_deg * PLACE_SLIVER_FRAC:
                pl_hits.append((a, idx))
        pl_hits.sort(reverse=True)
        primary_pl = pl_hits[0][1] if pl_hits else None

        co_fips = ((str(co_props[primary_co].get("state_fips", "")) + str(co_props[primary_co].get("county_fips", "")))
                   if primary_co is not None else "")
        city = str(pl_props[primary_pl].get("name", "")) if primary_pl is not None else ""

        zcta_records.append({
            "zip": zip5,
            "state_fips": st_fips,
            "state_name": state_name.get(st_fips, ""),
            "county_fips": co_fips,
            "county_name": county_name.get(co_fips, ""),
            "city": city,
            "centroid_lat": round(lat, 5),
            "centroid_lon": round(lon, 5),
            "area_sqmi": round(area_sqmi, 2),
            "geom": g,
        })
        if st_fips:
            zip_regions.append((zip5, "state", st_fips))
        for _, idx in co_hits:
            cp = co_props[idx]
            if cp.get("state_fips") and cp.get("county_fips"):
                zip_regions.append((zip5, "county", cp["state_fips"] + cp["county_fips"]))
        for _, idx in pl_hits:
            pp = pl_props[idx]
            if pp.get("state_fips") and pp.get("place_fips"):
                zip_regions.append((zip5, "place", pp["state_fips"] + pp["place_fips"]))

    log(f"assigned zctas: {len(zcta_records)} (unassigned state: {unassigned_state})")

    # ---------------- per-state geojson ----------------
    by_state = {}
    for r in zcta_records:
        by_state.setdefault(r["state_fips"] or "?", []).append(r)

    total_bytes = 0
    st_counts = {}
    for st, recs in sorted(by_state.items()):
        feats = []
        for r in recs:
            feats.append({
                "type": "Feature",
                "id": r["zip"],
                "properties": {
                    "zip": r["zip"], "pop": None, "city": r["city"],
                    "county": r["county_name"], "state": r["state_name"],
                    "area": r["area_sqmi"], "lat": r["centroid_lat"], "lon": r["centroid_lon"],
                },
                "geometry": geom_to_geojson(r["geom"]),
            })
        fc = {"type": "FeatureCollection", "name": f"zctas_{st}", "features": feats}
        s = json.dumps(fc, separators=(",", ":"), allow_nan=False)
        path = os.path.join(OUT, "zctas", f"{st}.geojson")
        with open(path, "w") as f:
            f.write(s)
        total_bytes += len(s)
        st_counts[st] = len(feats)
    log(f"wrote {len(by_state)} state files, {total_bytes / 1e6:.1f} MB total; "
        f"largest state: {max(st_counts, key=st_counts.get)}={max(st_counts.values())} zips")

    # ---------------- sqlite ----------------
    db_path = os.path.join(OUT, "zips.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE zips (
      zip TEXT PRIMARY KEY,
      state_fips TEXT, state_name TEXT,
      county_fips TEXT, county_name TEXT,
      city TEXT,
      population REAL,
      centroid_lat REAL, centroid_lon REAL,
      area_sqmi REAL
    );
    CREATE TABLE regions (
      scope TEXT, id TEXT, name TEXT, state_fips TEXT, code TEXT, PRIMARY KEY (scope, id)
    );
    CREATE TABLE zip_regions (
      zip TEXT, scope TEXT, region_id TEXT,
      PRIMARY KEY (zip, scope, region_id)
    );
    CREATE INDEX idx_zipr_region ON zip_regions(scope, region_id);
    CREATE INDEX idx_zips_state ON zips(state_fips);
    CREATE INDEX idx_zips_city ON zips(city);
    """)
    con.executemany(
        "INSERT OR IGNORE INTO zips VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(r["zip"], r["state_fips"], r["state_name"], r["county_fips"], r["county_name"],
          r["city"], None, r["centroid_lat"], r["centroid_lon"], r["area_sqmi"]) for r in zcta_records])
    for st_fips, name in state_name.items():
        con.execute("INSERT OR IGNORE INTO regions VALUES (?,?,?,?,?)",
                    ("state", st_fips, name, st_fips, state_stusps.get(st_fips, "")))
    for key, name in county_name.items():
        con.execute("INSERT OR IGNORE INTO regions VALUES (?,?,?,?,?)",
                    ("county", key, name, key[:2], None))
    seen_places = set()
    for p in pl_props:
        if not (p.get("state_fips") and p.get("place_fips") and p.get("name")):
            continue
        key = p["state_fips"] + p["place_fips"]
        if key in seen_places:
            continue
        seen_places.add(key)
        con.execute("INSERT OR IGNORE INTO regions VALUES (?,?,?,?,?)",
                    ("place", key, p["name"], p["state_fips"], None))
    con.executemany("INSERT OR IGNORE INTO zip_regions VALUES (?,?,?)", zip_regions)
    con.commit()

    n_zips = con.execute("SELECT COUNT(*) FROM zips").fetchone()[0]
    n_regions = {s: con.execute("SELECT COUNT(*) FROM regions WHERE scope=?", (s,)).fetchone()[0]
                 for s in ("state", "county", "place")}
    n_links = con.execute("SELECT COUNT(*) FROM zip_regions").fetchone()[0]
    con.close()

    report = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": {k: v["url"] for k, v in SOURCES.items()},
        "vintages": {k: v["vintage"] for k, v in SOURCES.items()},
        "zctas": len(zcta_records),
        "unassigned_state": unassigned_state,
        "states": {k: v for k, v in sorted(st_counts.items())},
        "states_total_bytes_mb": round(total_bytes / 1e6, 1),
        "db": {"zips": n_zips, **n_regions, "zip_region_links": n_links},
        "simplify_tolerance_deg": SIMP_TOL,
        "population": None,
        "note": "population left NULL: Census API requires a key in this environment; see implementation-plan.md",
    }
    with open(os.path.join(OUT, "build-report.json"), "w") as f:
        json.dump(report, f, indent=2)

    # ---------------- validation ----------------
    assert n_zips >= 30000, f"expected >=30000 zips, got {n_zips}"
    assert n_regions["state"] >= 50, f"states: {n_regions['state']}"
    assert n_regions["county"] >= 3000, f"counties: {n_regions['county']}"
    assert n_regions["place"] >= 10000, f"places: {n_regions['place']}"
    assert unassigned_state / max(1, n_zips) < 0.01, f"too many unassigned: {unassigned_state}"
    tx = st_counts.get("48", 0)
    assert tx > 1000, f"TX should have 1000+ zips, got {tx}"

    log(f"DONE in {time.time() - t0:.0f}s")
    log(json.dumps(report, indent=2)[:1500])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download the official US Census Cartographic Boundary sources into data/raw/.

Idempotent: skips files that already exist (delete a file to force re-download).
All sources are official, key-free, WGS84.

Run:  ./.venv/bin/python data/scripts/download_sources.py
"""

import os
import sys
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RAW = os.path.join(ROOT, "data", "raw")

SOURCES = {
    "cb_2020_us_zcta520_500k.zip": (
        "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip",
        "ZIP Code Tabulation Areas (5-digit), 2020 release, 1:500,000 (~66 MB)"),
    "cb_2025_us_county_5m.zip": (
        "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_5m.zip",
        "Counties, 2025 release, 1:5,000,000 (~3 MB)"),
    "cb_2025_us_state_5m.zip": (
        "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_5m.zip",
        "States, 2025 release, 1:5,000,000 (~1 MB)"),
    "cb_2025_us_place_500k.zip": (
        "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_place_500k.zip",
        "Incorporated places (cities), 2025 release, 1:500,000 (~23 MB)"),
}


def main() -> int:
    os.makedirs(RAW, exist_ok=True)
    for fname, (url, desc) in SOURCES.items():
        path = os.path.join(RAW, fname)
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            print(f"[download] skip  {fname} (already present, {os.path.getsize(path) / 1e6:.1f} MB)")
            continue
        print(f"[download] {fname}  ({desc})", flush=True)
        t0 = time.time()
        try:
            urllib.request.urlretrieve(url, path)
        except Exception as e:
            print(f"[download] FAILED {fname}: {e}", file=sys.stderr)
            if os.path.exists(path):
                os.remove(path)
            return 1
        size = os.path.getsize(path)
        if size < 10_000:
            print(f"[download] FAILED {fname}: suspiciously small ({size} B) — likely an error page",
                  file=sys.stderr)
            os.remove(path)
            return 1
        print(f"[download] ok    {fname}  {size / 1e6:.1f} MB in {time.time() - t0:.0f}s", flush=True)
    print("[download] all sources present in data/raw/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

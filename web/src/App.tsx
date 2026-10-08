import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Region, type RegionScope, type ZipFeatureCollection, type ZipInfo } from "./api";
import { MapView } from "./MapView";
import { ScopeSelector } from "./ScopeSelector";
import { ZipList } from "./ZipList";
import { DetailCard } from "./DetailCard";
import { useGeolocation } from "./useGeolocation";

type ViewScope = RegionScope | "zip";

interface ScopeState {
  scope: ViewScope;
  regionId: string;
  regionName: string;
}

function readUrlState(): Partial<ScopeState> & { zip?: string } {
  const p = new URLSearchParams(window.location.search);
  const out: Partial<ScopeState> & { zip?: string } = {};
  const s = p.get("scope");
  if (s === "city" || s === "county" || s === "state") out.scope = s === "city" ? "place" : s;
  if (p.get("region")) out.regionId = p.get("region")!;
  if (p.get("regionName")) out.regionName = decodeURIComponent(p.get("regionName")!);
  if (p.get("zip")) out.zip = p.get("zip")!;
  return out;
}

function writeUrlState(scope: ScopeState | null, zip: string | null) {
  const p = new URLSearchParams();
  if (scope) {
    p.set("scope", scope.scope === "place" ? "city" : scope.scope);
    p.set("region", scope.regionId);
    if (scope.regionName) p.set("regionName", scope.regionName);
  }
  if (zip) p.set("zip", zip);
  const qs = p.toString();
  const url = qs ? `?${qs}` : window.location.pathname;
  window.history.replaceState(null, "", url);
}

export default function App() {
  const [userZip, setUserZip] = useState<ZipInfo | null>(null);
  const [scope, setScope] = useState<ScopeState | null>(null);
  const [data, setData] = useState<ZipFeatureCollection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedZip, setSelectedZip] = useState<string | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);

  // restore shared state from URL
  useEffect(() => {
    const u = readUrlState();
    if (u.scope && u.regionId) {
      setScope({ scope: u.scope, regionId: u.regionId, regionName: u.regionName ?? "" });
    }
    setSelectedZip(u.zip ?? null);
  }, []);

  // API liveness
  useEffect(() => {
    api.health().catch(() => setApiDown(true));
  }, []);

  // geolocation → geocode → default scope (user's county, per the plan)
  const onFix = useCallback((lat: number, lon: number) => {
    api
      .geocode(lat, lon)
      .then((z) => {
        setUserZip(z);
        setScope((cur) => {
          if (cur) return cur;
          if (z.county.fips)
            return { scope: "county", regionId: z.county.fips, regionName: `${z.county.name} County` };
          if (z.state.fips)
            return { scope: "state", regionId: z.state.fips, regionName: `${z.state.name} (state)` };
          return { scope: "state", regionId: "US", regionName: "United States" };
        });
      })
      .catch(() => {
        /* outside ZCTA coverage — user can pick manually */
      });
  }, []);
  const geoStatus = useGeolocation(onFix);

  // load zips whenever the scope changes
  useEffect(() => {
    // skip until a region is selected or derived (null scope or empty regionId)
    if (!scope || scope.regionId === "") return;
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .zips(scope.scope, scope.regionId)
      .then((fc) => {
        if (!alive) return;
        console.log('[app] data received:', fc.features?.length, 'features for scope', fc.meta?.scope, fc.meta?.region);
        setData(fc);
        setMapError(null);
      })
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [scope]);

  // keep URL shareable
  useEffect(() => {
    writeUrlState(scope, selectedZip);
  }, [scope, selectedZip]);

  const selectedFeature = useMemo(
    () => data?.features.find((f) => f.properties.zip === selectedZip) ?? null,
    [data, selectedZip]
  );

  const handlePick = (r: Region) => {
    setScope({ scope: r.scope, regionId: r.id, regionName: r.name });
    setSelectedZip(null);
  };
  const handlePickZip = (zip: string) => {
    setScope({ scope: "zip", regionId: zip, regionName: `ZIP ${zip}` });
    setSelectedZip(zip);
  };
  const handleScopeChange = (s: RegionScope) => {
    setScope((cur) => {
      if (!cur) return null;
      if (s === cur.scope) return null;

      // When going wider (narrower scope → state), derive the state FIPS
      // from the current regionId (first 2 digits of county/place FIPS).
      // Going narrower or lateral (state → county, county ↔ place):
      //   cannot reliably derive the child region, so clear (user must pick).
      let newRegionId = "";
      if (s === "state" && cur.regionId.length >= 2) {
        newRegionId = cur.regionId.slice(0, 2);
      }
      // (narrower/lateral scopes cleared — user must pick from dropdown)
      return { scope: s, regionId: newRegionId, regionName: "" };
    });
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>◧</span> ZipScope
          <span className="tagline">ZIP areas in your city, county, or state</span>
        </div>
        <ScopeSelector
          scope={scope && scope.scope !== "zip" ? scope.scope : "state"}
          regionName={scope?.regionName ?? ""}
          onScopeChange={handleScopeChange}
          onPick={handlePick}
          onPickZip={handlePickZip}
        />
        <div className={`status status-${geoStatus}`}>
          {geoStatus === "requesting" && "Finding your location…"}
          {geoStatus === "granted" && userZip && `Located: ${userZip.city || userZip.county.name} (${userZip.zip})`}
          {geoStatus === "denied" && "Location off — search for a city, county, or state"}
        </div>
      </header>

      <div className="body">
        <main className="map-pane">
          <MapView
            data={data}
            user={userZip ? userZip.centroid : null}
            userZip={userZip?.zip ?? null}
            selectedZip={selectedZip}
            onSelectZip={setSelectedZip}
            onMapError={(m) => setMapError(m)}
          />
          {apiDown && (
            <div className="banner">
              API unreachable — start it with <code>./.venv/bin/python -m uvicorn main:app --port 8000 --app-dir api</code>
            </div>
          )}
          {loading && <div className="banner banner-load">Loading ZIP areas…</div>}
          {error && <div className="banner banner-err">{error}</div>}
          {mapError && <div className="banner banner-err banner-map">Map: {mapError}</div>}
          {data && !loading && (
            <div className="zip-count">{(data.meta?.zips ?? data.features.length).toLocaleString()} ZIP areas</div>
          )}
          {(!scope || (scope && scope.regionId === "")) && !apiDown && (
            <div className="banner banner-hint">
              {!scope
                ? <span>Pick a <b>city</b>, <b>county</b>, or <b>state</b> above (or wait for location).</span>
                : <span>Select a <b>{scope.scope}</b> from the dropdown above to see its ZIP areas.</span>}
            </div>
          )}
        </main>

        <aside className="sidebar">
          <DetailCard feature={selectedFeature} userZip={userZip} onClear={() => setSelectedZip(null)} scope={scope} selectedZip={selectedZip} />
          <ZipList
            features={data?.features ?? []}
            selectedZip={selectedZip}
            userZip={userZip?.zip ?? null}
            onSelect={(z) => setSelectedZip(z)}
          />
          <footer className="foot">
            Boundaries: US Census ZCTAs (2020) · cities/counties (2025)
          </footer>
        </aside>
      </div>
    </div>
  );
}

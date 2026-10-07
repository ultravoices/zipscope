import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection } from "geojson";
import type { GeoFeature, ZipFeatureCollection, ZipFeatureProps } from "./api";

// Minimal MapLibre style: dark background + OpenStreetMap raster (no API key).
const BASE_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#11151c" } },
    { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.35, "raster-opacity": 0.9 } },
  ],
} as unknown as maplibregl.StyleSpecification;

interface MapViewProps {
  data: ZipFeatureCollection | null;
  user: { lat: number; lon: number } | null;
  userZip: string | null;
  selectedZip: string | null;
  onSelectZip: (zip: string | null) => void;
  onMapError?: (msg: string) => void;
}

const emptyFC = (): FeatureCollection => ({ type: "FeatureCollection", features: [] });

export function MapView({ data, user, userZip, selectedZip, onSelectZip, onMapError }: MapViewProps) {
  const divRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const onSelectRef = useRef(onSelectZip);
  onSelectRef.current = onSelectZip;
  const onErrRef = useRef(onMapError);
  onErrRef.current = onMapError;

  // ---- init once ----
  useEffect(() => {
    if (!divRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: divRef.current,
      style: BASE_STYLE,
      center: [-98.5, 39.5],
      zoom: 3.4,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

    // addLayer can throw on spec issues; guard each so one bad layer can't
    // silently drop everything behind it (and the user marker).
    const safeAdd = (spec: maplibregl.LayerSpecification, fallback?: maplibregl.LayerSpecification) => {
      try {
        map.addLayer(spec);
      } catch (e) {
        console.error("[map] addLayer failed:", (e as Error).message);
        if (fallback) {
          try {
            map.addLayer(fallback);
            return;
          } catch (e2) {
            onErrRef.current?.(`map layer "${spec.id}" failed: ${(e2 as Error).message}`);
          }
        } else {
          onErrRef.current?.(`map layer "${spec.id}" failed: ${(e as Error).message}`);
        }
      }
    };

    map.on("load", () => {
      try {
        if (!map.getSource("zctas")) {
          map.addSource("zctas", { type: "geojson", data: emptyFC() });
          safeAdd(
            {
              id: "zctas-fill",
              type: "fill",
              source: "zctas",
              paint: {
                "fill-color": [
                  "step", ["get", "area"],
                  "#9ecae9", 1, "#6baed6", 4, "#4292c6", 12, "#2171b5", 40, "#08519c",
                ] as any,
                "fill-opacity": 0.3,
              },
            },
            { id: "zctas-fill", type: "fill", source: "zctas", paint: { "fill-color": "#4292c6", "fill-opacity": 0.3 } }
          );
        }
        if (!map.getLayer("zctas-line")) {
          safeAdd(
            {
              id: "zctas-line",
              type: "line",
              source: "zctas",
              paint: { "line-color": "#08519c", "line-width": 0.5, "line-opacity": 0.85 },
            }
          );
        }
        if (!map.getSource("highlight")) {
          map.addSource("highlight", { type: "geojson", data: emptyFC() });
          safeAdd({
            id: "highlight-fill",
            type: "fill",
            source: "highlight",
            paint: {
              "fill-color": ["case", ["==", ["get", "kind"], "selected"], "#f59f00", "#ff2d55"] as any,
              "fill-opacity": ["case", ["==", ["get", "kind"], "selected"], 0.55, 0.4] as any,
            },
          });
          safeAdd({
            id: "highlight-line",
            type: "line",
            source: "highlight",
            paint: {
              "line-color": ["case", ["==", ["get", "kind"], "selected"], "#f59f00", "#ff2d55"] as any,
              "line-width": 2,
            },
          });
        }
        if (!map.getSource("user")) {
          map.addSource("user", { type: "geojson", data: emptyFC() });
          safeAdd({
            id: "user-halo",
            type: "circle",
            source: "user",
            paint: { "circle-radius": 14, "circle-color": "#ffffff", "circle-opacity": 0.35 },
          });
          safeAdd({
            id: "user-dot",
            type: "circle",
            source: "user",
            paint: { "circle-radius": 5, "circle-color": "#ff2d55", "circle-stroke-width": 2, "circle-stroke-color": "#ffffff" },
          });
        }
      } catch (e) {
        onErrRef.current?.(`map setup failed: ${(e as Error).message}`);
      }
    });

    map.on("mousemove", "zctas-fill", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const f = e.features?.[0] as GeoFeature | undefined;
      const tip = tooltipRef.current;
      if (f && tip) {
        const p = f.properties;
        tip.innerHTML = `<b>${p.zip}</b> ${p.city ? `· ${p.city}` : ""}<br>${p.area.toFixed(1)} sq mi${p.pop != null ? ` · pop ${p.pop.toLocaleString()}` : ""}`;
        tip.style.display = "block";
        tip.style.left = `${e.point.x + 14}px`;
        tip.style.top = `${e.point.y + 14}px`;
      }
    });
    map.on("mouseleave", "zctas-fill", () => {
      map.getCanvas().style.cursor = "";
      if (tooltipRef.current) tooltipRef.current.style.display = "none";
    });
    map.on("click", "zctas-fill", (e) => {
      const f = e.features?.[0] as GeoFeature | undefined;
      onSelectRef.current(f ? f.properties.zip : null);
    });
    map.on("click", (e) => {
      // click on empty map clears selection
      if (!map.queryRenderedFeatures(e.point, { layers: ["zctas-fill"] }).length) {
        onSelectRef.current(null);
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- data updates (viewport-based: only render visible features, plan item 4) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const src = map.getSource("zctas") as maplibregl.GeoJSONSource | undefined;
    const fullData = (data as FeatureCollection) ?? emptyFC();

    const push = () => {
      if (fullData.features.length) {
        const b = new maplibregl.LngLatBounds();
        for (const f of fullData.features) {
          const props = f.properties ?? ({} as ZipFeatureProps);
          b.extend([props.lon, props.lat]);
        }
        map.fitBounds(b, { padding: 48, duration: 650, maxZoom: 11 });
      }
    };

    // On scope change (data updates): render ALL features (don't filter), then fit map.
    // On viewport moves: only render visible features (optimization for big states).
    const setData = (features: GeoFeature[]) => {
      const currentSrc = map.getSource("zctas") as maplibregl.GeoJSONSource | undefined;
      if (!currentSrc) return;
      if (!features.length) { currentSrc.setData(emptyFC()); return; }
      currentSrc.setData({ type: "FeatureCollection", features });
    };

    // Scope change handler: always render all, never filter.
    const onScopeChange = () => {
      push();  // fitBounds to the new scope's features
      setData(fullData.features as GeoFeature[]);
    };

    // Viewport move handler: filter to visible features only.
    const onMoveEnd = () => {
      const bounds = map.getBounds();
      const visible = fullData.features.filter((f) => {
        const props = f.properties ?? ({} as ZipFeatureProps);
        const c = [props.lon, props.lat] as [number, number];
        return bounds.contains(c);
      });
      setData(visible as GeoFeature[]);
    };

    if (src) {
      // Source exists (map already loaded): apply data immediately
      if (data) {  // scope changed (has new data)
        onScopeChange();
      }
      map.on("moveend", onMoveEnd);
      return () => { map.off("moveend", onMoveEnd); };
    } else {
      // Source not yet (map not loaded): wait for it.
      const onLoad = () => {
        if (data) {
          // Map loaded → sources exist → apply data immediately
          onScopeChange();
        }
      };
      map.once("load", onLoad);
      return () => { map.off("load", onLoad); };
    }
  }, [data]);

  // ---- user marker ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("user") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    if (user) {
      src.setData({
        type: "FeatureCollection",
        features: [
          { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [user.lon, user.lat] } },
        ],
      });
    } else {
      src.setData(emptyFC());
    }
  }, [user]);

  // ---- highlight selected / user zips (separate source, no feature-state) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("highlight") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    const feats: Feature[] = [];
    for (const f of data?.features ?? []) {
      if (selectedZip && f.properties.zip === selectedZip) {
        feats.push({ type: "Feature", properties: { kind: "selected" }, geometry: f.geometry });
      } else if (userZip && f.properties.zip === userZip) {
        feats.push({ type: "Feature", properties: { kind: "mine" }, geometry: f.geometry });
      }
    }
    src.setData({ type: "FeatureCollection", features: feats });
  }, [data, selectedZip, userZip]);

  return (
    <div className="map-wrap">
      <div ref={divRef} className="map" />
      <div ref={tooltipRef} className="map-tooltip" style={{ display: "none" }} />
    </div>
  );
}

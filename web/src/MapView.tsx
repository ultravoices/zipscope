import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection } from "geojson";
import type { GeoFeature, ZipFeatureCollection } from "./api";

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

const AREA_COLOR = [
  "step",
  ["get", "area"],
  "#9ecae9",
  1, "#6baed6",
  4, "#4292c6",
  12, "#2171b5",
  40, "#08519c",
] as any;

interface MapViewProps {
  data: ZipFeatureCollection | null;
  user: { lat: number; lon: number } | null;
  userZip: string | null;
  selectedZip: string | null;
  onSelectZip: (zip: string | null) => void;
}

const emptyFC = (): FeatureCollection => ({ type: "FeatureCollection", features: [] });

export function MapView({ data, user, userZip, selectedZip, onSelectZip }: MapViewProps) {
  const divRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const onSelectRef = useRef(onSelectZip);
  onSelectRef.current = onSelectZip;

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

    map.on("load", () => {
      if (!map.getSource("zctas")) {
        map.addSource("zctas", { type: "geojson", data: emptyFC() });
        map.addLayer({
          id: "zctas-fill",
          type: "fill",
          source: "zctas",
          paint: {
            "fill-color": AREA_COLOR,
            "fill-opacity": [
              "case",
              ["boolean", ["feature-state", "selected"]], 0.7,
              ["boolean", ["feature-state", "mine"]], 0.55,
              ["boolean", ["feature-state", "hover"]], 0.5,
              0.3,
            ] as any,
          },
        });
        map.addLayer({
          id: "zctas-line",
          type: "line",
          source: "zctas",
          paint: {
            "line-color": [
              "case",
              ["boolean", ["feature-state", "selected"]], "#f59f00",
              ["boolean", ["feature-state", "mine"]], "#ff6b35",
              "#1864ab",
            ] as any,
            "line-width": ["case", ["boolean", ["feature-state", "selected"]], 2, 0.5] as any,
            "line-opacity": 0.85,
          },
        });
      }
      if (!map.getSource("user")) {
        map.addSource("user", { type: "geojson", data: emptyFC() });
        map.addLayer({
          id: "user-halo",
          type: "circle",
          source: "user",
          paint: { "circle-radius": 14, "circle-color": "#ffffff", "circle-opacity": 0.35 },
        });
        map.addLayer({
          id: "user-dot",
          type: "circle",
          source: "user",
          paint: { "circle-radius": 5, "circle-color": "#ff2d55", "circle-stroke-width": 2, "circle-stroke-color": "#ffffff" },
        });
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
    // click on empty map clears selection
    map.on("click", (e) => {
      if (e.defaultPrevented) return;
      if (!map.queryRenderedFeatures(e.point, { layers: ["zctas-fill"] }).length) {
        onSelectRef.current(null);
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- data updates ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const push = () => {
      const src = map.getSource("zctas") as maplibregl.GeoJSONSource | undefined;
      if (!src) return;
      src.setData((data as FeatureCollection) ?? emptyFC());
      if (data && data.features.length) {
        const b = new maplibregl.LngLatBounds();
        for (const f of data.features) b.extend([f.properties.lon, f.properties.lat]);
        map.fitBounds(b, { padding: 48, duration: 650, maxZoom: 11 });
      }
    };
    if (map.isStyleLoaded()) push();
    else map.once("load", push);
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

  // ---- feature-state highlights ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const feats = (data?.features ?? []).filter((f) => f.id != null);
    for (const f of feats) {
      const fid = f.id as string;
      map.setFeatureState({ source: "zctas", id: fid }, { selected: 0, hover: 0, mine: 0 });
      if (selectedZip && fid === selectedZip) map.setFeatureState({ source: "zctas", id: fid }, { selected: 1 });
      if (userZip && fid === userZip) map.setFeatureState({ source: "zctas", id: fid }, { mine: 1 });
    }
  }, [data, selectedZip, userZip]);

  // ---- hover feature-state ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const move = (e: maplibregl.MapMouseEvent) => {
      if (!map.isStyleLoaded()) return;
      const f = map.queryRenderedFeatures(e.point, { layers: ["zctas-fill"] })[0];
      if (f && f.id != null) {
        const cur = map.getFeatureState({ source: "zctas", id: f.id as number }) as Record<string, number>;
        if (!cur.hover) map.setFeatureState({ source: "zctas", id: f.id as number }, { hover: 1 });
      }
    };
    const leave = () => {
      for (const f of map.queryRenderedFeatures({ layers: ["zctas-fill"] })) {
        if (f.id != null && (map.getFeatureState({ source: "zctas", id: f.id as number }) as Record<string, number>).hover) {
          map.setFeatureState({ source: "zctas", id: f.id as number }, { hover: 0 });
        }
      }
    };
    map.on("mousemove", move);
    map.on("mouseout", leave);
    return () => {
      map.off("mousemove", move);
      map.off("mouseout", leave);
    };
  }, [data]);

  return (
    <div className="map-wrap">
      <div ref={divRef} className="map" />
      <div ref={tooltipRef} className="map-tooltip" style={{ display: "none" }} />
    </div>
  );
}

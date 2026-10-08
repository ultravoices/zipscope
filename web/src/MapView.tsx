import { useEffect, useRef } from "react";
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { FeatureCollection } from "geojson";
import type { ZipFeatureCollection } from "./api";

interface MapViewProps {
  data: ZipFeatureCollection | null;
  user: { lat: number; lon: number } | null;
  selectedZip: string | null;
  onSelectZip: (zip: string) => void;
}

export function MapView({ data, user, selectedZip, onSelectZip }: MapViewProps) {
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    const map = mapRef.current || L.map("map-container", {
      center: [39.5, -98.35],
      zoom: 4,
      zoomControl: false,
    });
    if (!mapRef.current) {
      mapRef.current = map;
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(map);
      L.control.zoom({ position: "topright" }).addTo(map);
    }

    // Remove old GeoJSON layer (when data changes)
    const layers = (map as any).getLayers?.();
    if (layers) {
      for (const layer of layers) {
        const childLayers = (layer as any)?.getLayers?.();
        if (Array.isArray(childLayers) && childLayers.length > 0) {
          map.removeLayer(layer);
          break;
        }
      }
    }

    // Build GeoJSON layer with area-based choropleth
    const style = (feature: any) => {
      const props = (feature && feature.properties) || {};
      const area: number = props.area || 0;
      const zip: string = props.zip || (feature && (feature as any).id) || "";
      let fillColor = "#9ecae9";
      if (area > 40) fillColor = "#08519c";
      else if (area > 12) fillColor = "#4292c6";
      else if (area > 4) fillColor = "#6baed6";

      if (selectedZip && zip === selectedZip) {
        return { fillColor: "#ff2d55", fillOpacity: 0.5, color: "#ff2d55", weight: 2 };
      }
      return { fillColor, fillOpacity: 0.3, color: "#08519c", weight: 0.5, opacity: 0.85 };
    };

    const onEachFeature = (feature: any, layer: any) => {
      const props = (feature && feature.properties) || {};
      const zip: string = props.zip || (feature && (feature as any).id) || "";

      layer.on("click", () => {
        onSelectZip(zip);
      });

      layer.on("mouseover", (e: any) => {
        const city: string = props.city || "";
        const area: number = props.area || 0;
        const pop: number | null = props.pop ?? null;
        const tip = `${zip}${city ? " · " + city : ""}${area ? ` · ${area.toFixed(1)} sq mi` : ""}${pop ? ` · ${pop.toLocaleString()}` : ""}`;
        layer.bindTooltip(tip, { sticky: true }).openTooltip(e.latlng!);
      });
      layer.on("mouseout", () => {
        layer.closeTooltip();
      });
    };

    L.geoJSON(data as FeatureCollection, {
      style,
      onEachFeature,
    }).addTo(map);

    // Zoom to fit bounds of the data
    if (data && data.features.length) {
      const bounds = L.geoJSON(data as FeatureCollection).getBounds();
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [50, 50] });
      }
    }

    // Add user marker (always on top)
    if (user) {
      L.circleMarker([user.lat, user.lon], {
        radius: 5,
        color: "#ff2d55",
        fillColor: "#ffffff",
        fillOpacity: 1,
        weight: 2,
      }).bindTooltip("You", { direction: "top" }).addTo(map);
    }
  }, [data, selectedZip, user]);

  return (
    <div id="map-container" className="map" style={{ width: "100%", height: "100%" }}>
      <style>{`
        .leaflet-container {
          background: #11151c !important;
        }
        .leaflet-control-zoom a {
          background: #222 !important;
          color: #fff !important;
          border: 1px solid #333 !important;
        }
        .leaflet-control-zoom a:hover {
          background: #333 !important;
        }
      `}</style>
    </div>
  );
}

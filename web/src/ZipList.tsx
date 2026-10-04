import { useMemo, useState } from "react";
import type { GeoFeature } from "./api";

interface ZipListProps {
  features: GeoFeature[];
  selectedZip: string | null;
  userZip: string | null;
  onSelect: (zip: string) => void;
}

type SortKey = "zip" | "area";

export function ZipList({ features, selectedZip, userZip, onSelect }: ZipListProps) {
  const [sort, setSort] = useState<SortKey>("zip");
  const sorted = useMemo(() => {
    const f = [...features];
    f.sort((a, b) => (sort === "area" ? b.properties.area - a.properties.area : a.properties.zip.localeCompare(b.properties.zip)));
    return f;
  }, [features, sort]);

  if (!features.length) return <div className="list-empty">No ZIP areas found for this scope.</div>;

  return (
    <div className="zip-list">
      <div className="list-head">
        <span>{features.length} ZIP areas</span>
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort ZIPs">
          <option value="zip">sort: ZIP #</option>
          <option value="area">sort: area</option>
        </select>
      </div>
      <ul>
        {sorted.map((f) => {
          const p = f.properties;
          const cls = ["zip-row"];
          if (p.zip === selectedZip) cls.push("selected");
          if (p.zip === userZip) cls.push("mine");
          return (
            <li key={p.zip} className={cls.join(" ")}>
              <button onClick={() => onSelect(p.zip)}>
                <span className="z">{p.zip}</span>
                <span className="z-city">{p.city || p.county || "—"}</span>
                <span className="z-area">{p.area.toFixed(1)} mi²</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

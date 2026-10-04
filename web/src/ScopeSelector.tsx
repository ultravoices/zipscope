import { useEffect, useRef, useState } from "react";
import { api, type Region, type RegionScope } from "./api";

const SCOPES: { key: RegionScope; label: string }[] = [
  { key: "place", label: "City" },
  { key: "county", label: "County" },
  { key: "state", label: "State" },
];

interface ScopeSelectorProps {
  scope: RegionScope;
  regionName: string;
  onScopeChange: (s: RegionScope) => void;
  onPick: (r: Region) => void;
  onPickZip: (zip: string) => void;
}

/** Segmented scope control + debounced region search combobox. */
export function ScopeSelector({ scope, regionName, onScopeChange, onPick, onPickZip }: ScopeSelectorProps) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Region[]>([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const seq = useRef(0);

  // 5-digit zip typed anywhere → jump straight to it
  useEffect(() => {
    const t = setTimeout(() => {
      if (/^\d{5}$/.test(q.trim())) onPickZip(q.trim());
    }, 350);
    return () => clearTimeout(t);
  }, [q, onPickZip]);

  // debounced search
  useEffect(() => {
    const n = ++seq.current;
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(async () => {
      try {
        const r = await api.regions(q, scope, undefined, 10);
        if (n === seq.current) {
          setResults(r);
          setOpen(true);
        }
      } catch {
        /* keep old results on error */
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q, scope]);

  // click outside closes the dropdown
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className="scope-bar">
      <div className="scope-tabs" role="tablist" aria-label="Map scope">
        {SCOPES.map((s) => (
          <button
            key={s.key}
            role="tab"
            aria-selected={scope === s.key}
            className={scope === s.key ? "tab active" : "tab"}
            onClick={() => onScopeChange(s.key)}
          >
            {s.label}
          </button>
        ))}
      </div>
      <div className="region-box" ref={boxRef}>
        <input
          value={q}
          placeholder={`Search ${SCOPES.find((s) => s.key === scope)?.label?.toLowerCase()}s… (or a 5-digit ZIP)`}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => setOpen(results.length > 0)}
          aria-label="Search region"
        />
        {open && results.length > 0 && (
          <ul className="results" role="listbox">
            {results.map((r) => (
              <li key={`${r.scope}-${r.id}`}>
                <button
                  role="option"
                  aria-selected={false}
                  onClick={() => {
                    onPick(r);
                    setOpen(false);
                    setQ("");
                  }}
                >
                  <span className="r-name">{r.name}</span>
                  <span className="r-sub">
                    {r.code ? `${r.code} · ` : ""}
                    {r.scope === "place" ? "city" : r.scope}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {regionName && <div className="region-current">showing: {regionName}</div>}
      </div>
    </div>
  );
}

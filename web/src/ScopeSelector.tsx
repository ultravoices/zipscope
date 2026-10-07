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

  // 2-letter state abbreviation when scope=state → resolve and pick that state.
  useEffect(() => {
    const t = setTimeout(() => {
      const trimmed = q.trim().toUpperCase();
      if (/^[A-Z]{2}$/.test(trimmed) && scope === "state") {
        (async () => {
          try {
            const r = await api.regions(trimmed, "state", "", 1);
            if (r.length) {
              onPick(r[0]);
              setOpen(false);  // close dropdown, DON'T clear q (avoids race with pre-fetch)
            }
          } catch {
            /* not a valid state — ignore */
          }
        })();
      }
    }, 500);
    return () => clearTimeout(t);
  }, [q, scope]);

  // Debounced search (when typing) + pre-fetch initial results when scope changes.
  // When q < 2 chars, fetches a page of the current scope so the dropdown isn't empty.
  useEffect(() => {
    const n = ++seq.current;
    const isInitial = q.trim().length < 2;
    const t = setTimeout(async () => {
      try {
        const r = isInitial
          ? await api.regions("", scope, "", 20)
          : await api.regions(q, scope, "", 10);
        if (n === seq.current) {
          setResults(r);
          setOpen(r.length > 0);
        }
      } catch {
        /* keep old results on error */
      }
    }, isInitial ? 150 : 250);
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
          placeholder={
            scope === "state"
              ? "Type a state (e.g. MO, california) or a 5-digit ZIP"
              : `Search ${SCOPES.find((s) => s.key === scope)?.label?.toLowerCase()}s… (or a 5-digit ZIP)`
          }
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
                    {r.scope === "state"
                      ? r.state_abbr
                      : [r.state_abbr, r.code].filter(Boolean).join(" · ") || (r.scope === "place" ? "city" : r.scope)}
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

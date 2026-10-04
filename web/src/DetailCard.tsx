import type { GeoFeature, ZipInfo } from "./api";

interface DetailCardProps {
  feature: GeoFeature | null;
  userZip: ZipInfo | null;
  onClear: () => void;
}

function fmtArea(sqmi: number): string {
  return `${sqmi >= 100 ? Math.round(sqmi) : sqmi.toFixed(1)} sq mi`;
}

export function DetailCard({ feature, userZip, onClear }: DetailCardProps) {
  return (
    <div className="detail-card">
      {feature ? (
        <>
          <div className="d-head">
            <h2>{feature.properties.zip}</h2>
            <button className="ghost" onClick={onClear} aria-label="Clear selection">
              ✕
            </button>
          </div>
          <dl>
            {feature.properties.city && <Row k="City" v={feature.properties.city} />}
            <Row k="County" v={feature.properties.county} />
            <Row k="State" v={feature.properties.state} />
            <Row k="Area" v={fmtArea(feature.properties.area)} />
            {feature.properties.pop != null && <Row k="Population" v={feature.properties.pop.toLocaleString()} />}
          </dl>
        </>
      ) : (
        <div className="d-empty">
          <h3>Select a ZIP</h3>
          <p>Click a ZIP area on the map or in the list.</p>
        </div>
      )}
      {userZip && (
        <div className="you-here">
          <span className="dot" aria-hidden />
          <div>
            <b>You</b> · {userZip.city || userZip.county.name} · ZIP {userZip.zip}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  if (!v) return null;
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

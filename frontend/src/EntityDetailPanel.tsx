import { type CSSProperties } from "react";
import { type DataState, type EntityPosition } from "./api";

interface Props {
  entityPosition: EntityPosition;
  isDemo: boolean;
  onClose: () => void;
  onFocus: () => void;
}

export default function EntityDetailPanel({ entityPosition, isDemo, onClose, onFocus }: Props) {
  const { entity, position } = entityPosition;

  return (
    <div style={styles.info}>
      <div style={styles.infoHeader}>
        <h2 style={styles.title}>{entity.name || entity.identifier}</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" style={styles.button} onClick={onFocus} aria-label="Focus">◎</button>
          <button type="button" style={styles.button} onClick={onClose} aria-label="Close">×</button>
        </div>
      </div>
      
      {entity.type === "aircraft" && (
        <div style={styles.row}>
          {entity.callsign ? `Callsign ${entity.callsign}` : "No callsign"}
          {entity.registration ? ` · ${entity.registration}` : ""}
          {entity.icao24 ? ` · ${entity.icao24}` : ""}
        </div>
      )}
      
      {entity.type === "ship" && (
        <div style={styles.row}>
          {entity.mmsi ? `MMSI ${entity.mmsi}` : ""}
          {entity.imo ? ` · ${entity.imo}` : ""}
          {entity.callsign ? ` · ${entity.callsign}` : ""}
        </div>
      )}
      
      {position ? (
        <>
          <div style={styles.row}>
            Position: {position.latitude.toFixed(3)}, {position.longitude.toFixed(3)}
          </div>
          <DataQualityBadge
            dataState={entityPosition.data_state}
            dataAgeS={entityPosition.data_age_s}
          />
          <div style={styles.row}>Altitude: {meters(position.altitude)}</div>
          <div style={styles.row}>
            Speed: {position.speed == null ? "—" : `${position.speed.toFixed(1)} m/s`}
          </div>
          <div style={styles.row}>Heading: {degrees(position.heading)}</div>
          {position.course != null && (
            <div style={styles.row}>Course: {degrees(position.course)}</div>
          )}
          <div style={styles.row}>
            Timestamp: {new Date(position.timestamp).toLocaleTimeString()}
          </div>
          <div style={styles.row}>
            Data Quality: {position.quality}
          </div>
        </>
      ) : (
        <div style={styles.row}>No position available</div>
      )}
      <div style={styles.row}>Source: {entity.source}</div>
      {isDemo && <div style={styles.demo}>SIMULATION / DEMO DATA</div>}
    </div>
  );
}

const DATA_STATE_LABEL: Record<DataState, string> = {
  live: "LIVE",
  recent: "RECENT",
  stale: "STALE",
  lost: "LOST",
  unknown: "UNKNOWN",
};

const DATA_STATE_COLOR: Record<DataState, string> = {
  live: "#86efac",
  recent: "#fde68a",
  stale: "#fdba74",
  lost: "#fca5a5",
  unknown: "#cbd5e1",
};

/** Tracking-data freshness badge (data state, never a safety assessment). */
function DataQualityBadge({
  dataState,
  dataAgeS,
}: {
  dataState: DataState;
  dataAgeS: number | null;
}) {
  const ageText =
    dataAgeS == null ? "age unknown" : `data age ${Math.max(0, Math.round(dataAgeS))}s`;
  return (
    <div
      style={styles.row}
      title="Tracking-data freshness (server-classified from data age). Not a safety assessment."
    >
      Data:{" "}
      <span style={{ color: DATA_STATE_COLOR[dataState], fontWeight: 600 }}>
        {DATA_STATE_LABEL[dataState]}
      </span>{" "}
      <span style={{ color: "#94a3b8" }}>({ageText})</span>
    </div>
  );
}

function meters(val?: number | null) {
  if (val == null) return "—";
  return `${Math.round(val)}m`;
}

function degrees(val?: number | null) {
  if (val == null) return "—";
  return `${Math.round(val)}°`;
}

const styles: Record<string, CSSProperties> = {
  info: {
    position: "fixed", bottom: 12, left: 12, zIndex: 10, width: 300,
    padding: "12px 14px", borderRadius: 10, background: "rgba(2, 6, 23, 0.85)",
    color: "#e2e8f0", border: "1px solid rgba(148, 163, 184, 0.3)",
    font: "13px/1.5 system-ui, sans-serif",
  },
  infoHeader: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    marginBottom: 6, fontSize: 15,
  },
  title: { margin: "0 0 8px", fontSize: 16, letterSpacing: 0.2 },
  row: { marginBottom: 6, color: "#cbd5e1" },
  button: {
    background: "#334155", border: "none", color: "#e2e8f0",
    borderRadius: 4, cursor: "pointer", padding: "2px 8px", fontSize: 14,
  },
  demo: {
    marginTop: 8, padding: "4px 8px", borderRadius: 6,
    background: "rgba(250, 204, 21, 0.15)", border: "1px solid rgba(250, 204, 21, 0.5)",
    color: "#fde68a", fontSize: 12, letterSpacing: 0.4,
  },
};

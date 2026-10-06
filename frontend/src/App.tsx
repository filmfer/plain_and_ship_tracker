import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { CesiumGlobeHandle } from "./globe/CesiumGlobe";
import {
  fetchAircraft,
  fetchHealth,
  fetchShips,
  type EntityPosition,
  type HealthResponse,
} from "./api";
import CesiumGlobe from "./globe/CesiumGlobe";
import AlertPanel from "./AlertPanel";
import EntityDetailPanel from "./EntityDetailPanel";
import SearchBox from "./SearchBox";
import { applyUpdates, LiveClient, pairEntities, type LiveStatus } from "./live";

const REFRESH_MS = 5000;

export default function App() {
  const globeRef = useRef<CesiumGlobeHandle>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [aircraft, setAircraft] = useState<EntityPosition[]>([]);
  const [ships, setShips] = useState<EntityPosition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [liveStatus, setLiveStatus] = useState<LiveStatus>("connecting");

  // Initial bootstrap only: after this, the live WebSocket owns all entity
  // state (snapshot on connect + changed-entity batches). Re-polling the
  // full REST state on a timer would duplicate every WS update and force
  // the globe to re-sync unchanged entities, so there is deliberately no
  // interval here. Health is still refreshed lightly to track backend mode.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [healthData, aircraftData, shipsData] = await Promise.all([
          fetchHealth(),
          fetchAircraft(),
          fetchShips(),
        ]);
        if (cancelled) return;
        setHealth(healthData);
        setAircraft(aircraftData);
        setShips(shipsData);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    }

    async function refreshHealth() {
      try {
        const healthData = await fetchHealth();
        if (!cancelled) setHealth(healthData);
      } catch {
        /* live status indicator already surfaces outages */
      }
    }

    void load();
    const timer = setInterval(() => void refreshHealth(), REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const client = new LiveClient({
      onSnapshot: (message) => {
        const paired = pairEntities(message.entities, message.positions);
        setAircraft(paired.filter((item) => item.entity.type === "aircraft"));
        setShips(paired.filter((item) => item.entity.type === "ship"));
      },
      onUpdates: (updates) => {
        setAircraft((prev) => applyUpdates(prev, updates));
        setShips((prev) => applyUpdates(prev, updates));
      },
      onStatus: setLiveStatus,
    });
    client.start();
    return () => client.stop();
  }, []);

  const selected: EntityPosition | null =
    [...aircraft, ...ships].find((item) => item.entity.id === selectedId) ?? null;
  const isDemo = health?.app_mode === "demo";
  const showDemoIndicator = isDemo === true;

  return (
    <div style={styles.root}>
      <CesiumGlobe
        ref={globeRef}
        aircraft={aircraft}
        ships={ships}
        onSelect={setSelectedId}
      />
      {showDemoIndicator && (
        <div data-testid="demo-mode-indicator" role="status" style={styles.modeIndicator}>
          SIMULATION MODE — DATA IS NOT LIVE
        </div>
      )}
      <div style={styles.panel}>
        <h1 style={styles.title}>Plain Boat Radar</h1>
        {health && (
          <div style={styles.row}>
            Backend: {health.status} · mode {health.app_mode} · v{health.version}
          </div>
        )}
        <div style={styles.row}>
          Aircraft: {aircraft.length} · Ships: {ships.length} · Live: {liveStatus}
        </div>
        <div style={styles.legend}>
          <span style={styles.legendItem}>
            <span style={{ ...styles.dot, background: "#38bdf8" }} /> aircraft
          </span>
          <span style={styles.legendItem}>
            <span style={{ ...styles.dot, background: "#fb923c" }} /> ships
          </span>
        </div>
        {error && <div style={styles.error}>Backend unavailable: {error}</div>}
      </div>
      <SearchBox onSelect={setSelectedId} />
      {selected && (
        <EntityDetailPanel
          entityPosition={selected}
          isDemo={isDemo === true}
          onClose={() => setSelectedId(null)}
          onFocus={() => globeRef.current?.focusEntity(selected.entity.id)}
        />
      )}
      <AlertPanel onSelect={setSelectedId} />
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  root: { position: "relative", width: "100vw", height: "100vh", margin: 0 },
  panel: {
    position: "fixed", top: 12, left: 12, zIndex: 10, width: 300,
    padding: "10px 12px", borderRadius: 10, background: "rgba(2, 6, 23, 0.85)",
    color: "#e2e8f0", border: "1px solid rgba(148, 163, 184, 0.3)",
    font: "13px/1.5 system-ui, sans-serif",
  },
  modeIndicator: {
    position: "fixed", top: 12, left: "50%", transform: "translateX(-50%)",
    zIndex: 12, padding: "6px 14px", borderRadius: 999,
    background: "rgba(120, 53, 15, 0.92)", border: "1px solid #fbbf24",
    color: "#fef3c7", font: "bold 12px/1.4 system-ui, sans-serif",
    letterSpacing: 0.6,
  },
  title: { margin: "0 0 6px", fontSize: 16, letterSpacing: 0.2 },
  row: { marginBottom: 4, color: "#cbd5e1" },
  legend: { display: "flex", gap: 12, marginTop: 6, color: "#94a3b8", fontSize: 12 },
  legendItem: { display: "inline-flex", alignItems: "center", gap: 4 },
  dot: { display: "inline-block", width: 8, height: 8, borderRadius: "50%" },
  error: { marginTop: 8, color: "#fca5a5", fontSize: 12 },
};

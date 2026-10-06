import { useState, type CSSProperties, type FormEvent } from "react";
import { searchEntities, type EntityPosition } from "./api";

/**
 * Primary search interface.
 *
 * One box searches everything the backend indexes: flight number (primary),
 * callsign, ICAO24, registration, ship name, MMSI and IMO. Normalization
 * (``TP1824`` / ``tp 1824`` / ``TP-1824`` ...) happens server-side.
 */
export default function SearchBox({ onSelect }: { onSelect: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EntityPosition[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      setResults(await searchEntities(q));
      setSearched(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={styles.box}>
      <form onSubmit={runSearch} role="search">
        <input
          type="search"
          aria-label="Search flight number, callsign, ship name, MMSI or IMO"
          placeholder="Flight no., callsign, ship, MMSI, IMO…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={styles.input}
        />
        <button type="submit" disabled={loading || !query.trim()} style={styles.button}>
          {loading ? "…" : "Search"}
        </button>
      </form>
      {error && <div style={styles.error}>Search failed: {error}</div>}
      {searched && !loading && !error && (
        <div style={styles.results} role="status">
          {results.length === 0 ? (
            <span style={styles.empty}>No matches</span>
          ) : (
            results.map((item) => (
              <button
                key={item.entity.id}
                type="button"
                style={styles.result}
                onClick={() => onSelect(item.entity.id)}
              >
                <span style={styles.id}>{resultTitle(item)}</span>
                <span style={styles.sub}>{resultSubtitle(item)}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function resultTitle(item: EntityPosition): string {
  const { entity } = item;
  if (entity.type === "aircraft") return entity.identifier;
  return entity.name ?? entity.identifier;
}

function resultSubtitle(item: EntityPosition): string {
  const { entity } = item;
  if (entity.type === "aircraft") {
    return [entity.callsign, entity.registration, entity.icao24].filter(Boolean).join(" · ");
  }
  return [
    entity.mmsi ? `MMSI ${entity.mmsi}` : "",
    entity.imo ?? "",
    entity.callsign ?? "",
  ]
    .filter((part) => part.trim() !== "")
    .join(" · ");
}

const styles: Record<string, CSSProperties> = {
  box: {
    position: "fixed", top: 12, right: 12, zIndex: 10, width: 280,
    padding: "10px 12px", borderRadius: 10, background: "rgba(2, 6, 23, 0.85)",
    color: "#e2e8f0", border: "1px solid rgba(148, 163, 184, 0.3)",
    font: "13px/1.5 system-ui, sans-serif",
  },
  input: {
    width: "100%", boxSizing: "border-box", padding: "6px 8px", borderRadius: 6,
    border: "1px solid #475569", background: "#0f172a", color: "#e2e8f0", marginBottom: 8,
  },
  button: {
    width: "100%", padding: "6px 8px", borderRadius: 6, border: "none",
    background: "#0284c7", color: "#fff", cursor: "pointer", fontWeight: 600,
  },
  results: { marginTop: 8, display: "flex", flexDirection: "column", gap: 4, maxHeight: 260, overflowY: "auto" },
  result: {
    display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2,
    padding: "6px 8px", borderRadius: 6, border: "1px solid #334155",
    background: "#0f172a", color: "#e2e8f0", cursor: "pointer", textAlign: "left", width: "100%",
  },
  id: { fontWeight: 700, fontSize: 13 },
  sub: { fontSize: 11, color: "#94a3b8" },
  empty: { fontSize: 12, color: "#94a3b8" },
  error: { marginTop: 8, color: "#fca5a5", fontSize: 12 },
};
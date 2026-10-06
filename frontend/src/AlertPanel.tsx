import { useEffect, useState } from "react";
import { fetchAlerts, acknowledgeAlert, type Alert } from "./api";

export default function AlertPanel({ onSelect }: { onSelect: (id: string) => void }) {
  const [alerts, setAlerts] = useState<Alert[]>([]);

  const load = async () => {
    const data = await fetchAlerts(true); // Active only
    setAlerts(data);
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleAcknowledge = async (id: string) => {
    await acknowledgeAlert(id);
    load();
  };

  if (alerts.length === 0) return null;

  return (
    <div style={styles.panel}>
      <h2 style={styles.title}>Alerts ({alerts.length})</h2>
      {alerts.map((alert) => (
        <div 
          key={alert.id} 
          style={{...styles.alert, cursor: "pointer"}} 
          onClick={() => onSelect(alert.entity_id)}
        >
          <div style={styles.header}>
            <span style={{...styles.severity, color: getSeverityColor(alert.severity)}}>
              {alert.severity}
            </span>
            <span style={styles.type}>{alert.type}</span>
          </div>
          <div style={styles.description}>{alert.description}</div>
          <div style={styles.footer}>
            <span>{alert.entity_id}</span>
            <button onClick={(e) => { e.stopPropagation(); handleAcknowledge(alert.id); }} style={styles.ack}>
              Ack
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function getSeverityColor(severity: string) {
  switch (severity) {
    case "CRITICAL": return "#f87171";
    case "WARNING": return "#fbbf24";
    case "WATCH": return "#60a5fa";
    default: return "#94a3b8";
  }
}

const styles: Record<string, any> = {
  panel: {
    position: "fixed",
    bottom: 12,
    right: 12,
    zIndex: 10,
    width: 300,
    maxHeight: 400,
    overflowY: "auto",
    padding: 12,
    borderRadius: 10,
    background: "rgba(2, 6, 23, 0.9)",
    color: "#e2e8f0",
    border: "1px solid rgba(148, 163, 184, 0.3)",
    font: "13px system-ui, sans-serif",
  },
  title: { fontSize: 14, margin: "0 0 8px" },
  alert: { padding: 8, borderBottom: "1px solid #334155", marginBottom: 4 },
  header: { display: "flex", gap: 8, marginBottom: 4 },
  severity: { fontSize: 10, fontWeight: "bold", textTransform: "uppercase" },
  type: { fontSize: 10, color: "#94a3b8" },
  description: { marginBottom: 4 },
  footer: { display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, color: "#64748b" },
  ack: { fontSize: 10, cursor: "pointer", background: "#334155", border: "none", color: "white", borderRadius: 4, padding: "2px 6px" },
};

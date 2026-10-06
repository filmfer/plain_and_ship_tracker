/**
 * Thin API client for the backend.
 *
 * The frontend must never depend on external providers directly; every call
 * goes through this backend client.
 */

export type AppMode = "demo" | "local" | "production";

export interface HealthResponse {
  status: string;
  app_mode: AppMode;
  version: string;
}

export interface TrackedEntity {
  id: string;
  type: "aircraft" | "ship";
  identifier: string;
  source: string;
  name?: string | null;
  callsign?: string | null;
  registration?: string | null;
  icao24?: string | null;
  mmsi?: string | null;
  imo?: string | null;
}

export type DataState = "live" | "recent" | "stale" | "lost" | "unknown";

export interface Position {
  entity_id: string;
  timestamp: string;
  latitude: number;
  longitude: number;
  source: string;
  altitude?: number | null;
  speed?: number | null;
  heading?: number | null;
  course?: number | null;
  vertical_rate?: number | null;
  signal_age?: number | null;
  quality: string;
}

export interface EntityPosition {
  entity: TrackedEntity;
  position: Position | null;
  /** Tracking-data freshness (server-classified, app-clock based). */
  data_state: DataState;
  /** Seconds between app-clock now and the position timestamp (null if none). */
  data_age_s: number | null;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${path}`);
  }
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/api/health");
}

export function fetchAircraft(): Promise<EntityPosition[]> {
  return getJson<EntityPosition[]>("/api/aircraft");
}

export function fetchShips(): Promise<EntityPosition[]> {
  return getJson<EntityPosition[]>("/api/ships");
}

export function searchEntities(query: string): Promise<EntityPosition[]> {
  return getJson<EntityPosition[]>(`/api/search?q=${encodeURIComponent(query)}`);
}

export interface Alert {
  id: string;
  entity_id: string;
  timestamp: string;
  type: string;
  score: number;
  severity: "NORMAL" | "WATCH" | "WARNING" | "CRITICAL";
  description: string;
  detected_at: string;
  resolved_at?: string | null;
  acknowledged: boolean;
}

export function fetchAlerts(activeOnly = false): Promise<Alert[]> {
  const url = activeOnly ? "/api/alerts/active" : "/api/alerts";
  return getJson<Alert[]>(url);
}

export function acknowledgeAlert(id: string): Promise<{ status: string }> {
  return fetch(`/api/alerts/${id}/acknowledge`, { method: "POST" }).then((res) =>
    res.json()
  );
}

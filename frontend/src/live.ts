/**
 * Live WebSocket client for `/ws/live`.
 *
 * The frontend only ever talks to our own backend; it has no knowledge of
 * providers. Two message types are handled:
 *
 *   - `snapshot`           full current state, sent once on connect
 *   - `position_updates`   batch of only the positions that changed
 *
 * Reconnection uses exponential backoff and is fully controllable via
 * `start()` / `stop()` (tests inject their own socket factory).
 */

import type { DataState, EntityPosition, Position, TrackedEntity } from "./api";

export type LiveStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface PositionUpdate extends Position {
  entity_type: "aircraft" | "ship" | "unknown";
  /** Tracking-data freshness (server-classified, app-clock based). */
  data_state: DataState;
  /** Seconds between app-clock now and the position timestamp. */
  data_age_s: number | null;
}

export interface SnapshotMessage {
  type: "snapshot";
  timestamp?: string | null;
  entities: TrackedEntity[];
  positions: Position[];
}

export interface PositionUpdatesMessage {
  type: "position_updates";
  timestamp?: string | null;
  updates: PositionUpdate[];
}

export interface LiveHandlers {
  onSnapshot?: (message: SnapshotMessage) => void;
  onUpdates?: (updates: PositionUpdate[]) => void;
  onStatus?: (status: LiveStatus) => void;
}

export const LIVE_PATH = "/ws/live";

/** Absolute ws(s) URL for the live endpoint, derived from the page origin. */
export function liveUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${LIVE_PATH}`;
}

/** Position payloads enriched with server-side freshness fields. */
export type FreshPosition = Position & {
  data_state?: DataState;
  data_age_s?: number | null;
};

/** Pair entities with their latest positions (used for snapshots). */
export function pairEntities(
  entities: TrackedEntity[],
  positions: FreshPosition[],
): EntityPosition[] {
  const byId = new Map(positions.map((position) => [position.entity_id, position]));
  return entities.map((entity) => {
    const position = byId.get(entity.id) ?? null;
    return {
      entity,
      position,
      data_state: position?.data_state ?? "unknown",
      data_age_s: position?.data_age_s ?? null,
    };
  });
}

/** Immutably merge a batch of position updates into an existing list. */
export function applyUpdates(
  list: EntityPosition[],
  updates: PositionUpdate[],
): EntityPosition[] {
  if (updates.length === 0) {
    return list;
  }
  const byId = new Map(updates.map((update) => [update.entity_id, update]));
  let changed = false;
  const next = list.map((item) => {
    const update = byId.get(item.entity.id);
    if (!update) {
      return item;
    }
    changed = true;
    return {
      ...item,
      position: update,
      data_state: update.data_state,
      data_age_s: update.data_age_s,
    };
  });
  return changed ? next : list;
}

/** The minimal socket surface `LiveClient` depends on (injectable for tests). */
export interface SocketLike {
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  close: () => void;
}

export type SocketFactory = (url: string) => SocketLike;

export interface LiveClientOptions {
  url?: string;
  socketFactory?: SocketFactory;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

function defaultSocketFactory(url: string): SocketLike {
  return new WebSocket(url) as unknown as SocketLike;
}

/**
 * Reconnecting WebSocket client.
 *
 * Status flow: `connecting` → `open` → (`reconnecting` → `open`)* → `closed`.
 * Backoff is exponential (`baseDelayMs * 2^attempt`, capped at `maxDelayMs`)
 * and resets as soon as a connection opens.
 */
export class LiveClient {
  private socket: SocketLike | null = null;
  private timer: number | null = null;
  private attempt = 0;
  private stopped = true;

  constructor(
    private readonly handlers: LiveHandlers = {},
    private readonly options: LiveClientOptions = {},
  ) {}

  /** Open the first connection (no-op if already running). */
  start(): void {
    if (!this.stopped) {
      return;
    }
    this.stopped = false;
    this.attempt = 0;
    this.connect();
  }

  /** Close permanently: cancels the backoff timer and stops reconnecting. */
  stop(): void {
    this.stopped = true;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      try {
        socket.close();
      } catch {
        /* already closed */
      }
    }
    this.setStatus("closed");
  }

  private connect(): void {
    if (this.stopped) {
      return;
    }
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");
    let socket: SocketLike;
    try {
      const factory = this.options.socketFactory ?? defaultSocketFactory;
      socket = factory(this.options.url ?? liveUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
    };
    socket.onmessage = (event) => this.handleMessage(event.data);
    socket.onerror = () => this.handleClose(socket);
    socket.onclose = () => this.handleClose(socket);
  }

  /** Handle a failed/closed socket exactly once (error often precedes close). */
  private handleClose(socket: SocketLike): void {
    if (this.stopped || this.socket !== socket) {
      return;
    }
    this.socket = null;
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.timer !== null) {
      return;
    }
    const base = this.options.baseDelayMs ?? 500;
    const max = this.options.maxDelayMs ?? 15_000;
    const delay = Math.min(base * 2 ** this.attempt, max);
    this.attempt += 1;
    this.setStatus("reconnecting");
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  private handleMessage(data: unknown): void {
    if (typeof data !== "string") {
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return;
    }
    if (!parsed || typeof parsed !== "object") {
      return;
    }
    const type = (parsed as { type?: unknown }).type;
    if (type === "snapshot") {
      this.handlers.onSnapshot?.(parsed as SnapshotMessage);
    } else if (type === "position_updates") {
      const updates = (parsed as PositionUpdatesMessage).updates;
      if (Array.isArray(updates) && updates.length > 0) {
        this.handlers.onUpdates?.(updates);
      }
    }
    // Unknown message types are ignored (forward compatibility).
  }

  private setStatus(status: LiveStatus): void {
    this.handlers.onStatus?.(status);
  }
}


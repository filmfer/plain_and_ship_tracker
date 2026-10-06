import { afterEach, describe, expect, it, vi } from "vitest";

import type { DataState, EntityPosition, Position, TrackedEntity } from "./api";
import {
  applyUpdates,
  LiveClient,
  liveUrl,
  pairEntities,
  type LiveStatus,
  type PositionUpdate,
  type SocketLike,
} from "./live";

const AIRCRAFT: TrackedEntity = {
  id: "aircraft:DEMO001",
  type: "aircraft",
  identifier: "DEMO001",
  source: "mock",
};

const SHIP: TrackedEntity = {
  id: "ship:DEMO-SHIP-001",
  type: "ship",
  identifier: "DEMO-SHIP-001",
  source: "mock",
  mmsi: "000DEMO001",
};

function position(entityId: string, lat = 38): Position {
  return {
    entity_id: entityId,
    timestamp: "2024-01-01T00:00:00+00:00",
    latitude: lat,
    longitude: -28,
    source: "mock",
    quality: "good",
  };
}

class FakeSocket implements SocketLike {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  open(): void {
    this.onopen?.();
  }

  fail(): void {
    this.onerror?.();
    this.onclose?.();
  }

  emit(data: unknown): void {
    this.onmessage?.({ data });
  }

  close(): void {
    this.onclose?.();
  }
}

afterEach(() => {
  vi.useRealTimers();
});

describe("liveUrl", () => {
  it("derives the endpoint from the page origin", () => {
    expect(liveUrl()).toBe(`ws://${window.location.host}/ws/live`);
  });
});

describe("pairEntities", () => {
  it("pairs entities with their positions and tolerates missing ones", () => {
    const paired = pairEntities([AIRCRAFT, SHIP], [position(AIRCRAFT.id)]);
    expect(paired).toHaveLength(2);
    expect(paired[0].position?.latitude).toBe(38);
    expect(paired[1].position).toBeNull();
  });

  it("propagates server-side freshness and defaults to unknown", () => {
    const paired = pairEntities(
      [AIRCRAFT, SHIP],
      [{ ...position(AIRCRAFT.id), data_state: "live", data_age_s: 3 }],
    );
    expect(paired[0].data_state).toBe("live");
    expect(paired[0].data_age_s).toBe(3);
    expect(paired[1].data_state).toBe("unknown");
    expect(paired[1].data_age_s).toBeNull();
  });
});

describe("applyUpdates", () => {
  const list: EntityPosition[] = [
    { entity: AIRCRAFT, position: position(AIRCRAFT.id), data_state: "live" as DataState, data_age_s: 2 },
    { entity: SHIP, position: position(SHIP.id), data_state: "live" as DataState, data_age_s: 2 },
  ];

  it("merges changed positions immutably", () => {
    const update: PositionUpdate = {
      ...position(AIRCRAFT.id, 39),
      entity_type: "aircraft",
      data_state: "live",
      data_age_s: 1,
    };
    const next = applyUpdates(list, [update]);

    expect(next).not.toBe(list);
    expect(next[0].position?.latitude).toBe(39);
    expect(next[0].entity).toBe(AIRCRAFT); // metadata preserved
    expect(next[1]).toBe(list[1]); // untouched entry keeps identity
    expect(list[0].position?.latitude).toBe(38); // original untouched
  });

  it("returns the same reference when nothing matches", () => {
    const update: PositionUpdate = {
      ...position("aircraft:OTHER", 10),
      entity_type: "aircraft",
      data_state: "live",
      data_age_s: 1,
    };
    expect(applyUpdates(list, [update])).toBe(list);
    expect(applyUpdates(list, [])).toBe(list);
  });
});

describe("LiveClient", () => {
  it("dispatches snapshot and position_updates messages", () => {
    const sockets: FakeSocket[] = [];
    const snapshots: string[] = [];
    const batches: PositionUpdate[][] = [];
    const client = new LiveClient(
      {
        onSnapshot: (message) => snapshots.push(message.type),
        onUpdates: (updates) => batches.push(updates),
      },
      {
        socketFactory: () => {
          const socket = new FakeSocket();
          sockets.push(socket);
          return socket;
        },
      },
    );

    client.start();
    const socket = sockets[0];
    socket.open();

    socket.emit(
      JSON.stringify({
        type: "snapshot",
        entities: [AIRCRAFT],
        positions: [position(AIRCRAFT.id)],
      }),
    );
    socket.emit(
      JSON.stringify({
        type: "position_updates",
        updates: [
          {
            ...position(AIRCRAFT.id, 41),
            entity_type: "aircraft",
            data_state: "live",
            data_age_s: 1,
          },
        ],
      }),
    );
    socket.emit("not json"); // ignored
    socket.emit(JSON.stringify({ type: "unknown_future_kind" })); // ignored
    socket.emit(JSON.stringify({ type: "position_updates", updates: [] })); // ignored

    expect(snapshots).toEqual(["snapshot"]);
    expect(batches).toHaveLength(1);
    expect(batches[0][0].latitude).toBe(41);
    client.stop();
  });

  it("reconnects with exponential backoff and stops cleanly", () => {
    vi.useFakeTimers();
    const sockets: FakeSocket[] = [];
    const statuses: LiveStatus[] = [];
    const client = new LiveClient(
      { onStatus: (status) => statuses.push(status) },
      {
        socketFactory: () => {
          const socket = new FakeSocket();
          sockets.push(socket);
          return socket;
        },
        baseDelayMs: 100,
        maxDelayMs: 1000,
      },
    );

    client.start();
    expect(sockets).toHaveLength(1);
    expect(statuses[statuses.length - 1]).toBe("connecting");

    sockets[0].open();
    expect(statuses[statuses.length - 1]).toBe("open");

    sockets[0].fail();
    expect(statuses[statuses.length - 1]).toBe("reconnecting");
    expect(sockets).toHaveLength(1); // nothing yet: waiting out the backoff

    vi.advanceTimersByTime(100); // first retry after 100ms
    expect(sockets).toHaveLength(2);

    sockets[1].fail(); // backoff doubles to 200ms
    vi.advanceTimersByTime(100);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(100);
    expect(sockets).toHaveLength(3);

    client.stop();
    expect(statuses[statuses.length - 1]).toBe("closed");
    vi.advanceTimersByTime(5000);
    expect(sockets).toHaveLength(3); // no reconnects after stop()
    expect(sockets[2].onclose).toBeNull(); // handlers detached
  });
});

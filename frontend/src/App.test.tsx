import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";

// Cesium needs a real WebGL canvas; stub the globe with selectable buttons.
vi.mock("./globe/CesiumGlobe", () => ({
  default: ({ onSelect }: { onSelect: (id: string | null) => void }) => (
    <>
      <button type="button" onClick={() => onSelect("aircraft:DEMO001")}>
        select-aircraft
      </button>
      <button type="button" onClick={() => onSelect("ship:DEMO-SHIP-001")}>
        select-ship
      </button>
    </>
  ),
}));

// LiveClient opens a real WebSocket in the browser; silence it in tests.
vi.mock("./live", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./live")>();
  return {
    ...actual,
    LiveClient: class {
      start() {}
      stop() {}
    },
  };
});

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data };
}

function aircraftItem() {
  return {
    entity: {
      id: "aircraft:DEMO001", type: "aircraft", identifier: "DEMO001",
      source: "mock", callsign: "DEM1",
    },
    position: {
      entity_id: "aircraft:DEMO001", timestamp: "2024-01-01T00:00:00Z",
      latitude: 38.5, longitude: -28.0, source: "mock",
      altitude: 10668, speed: 245.5, heading: 90, quality: "good",
    },
    data_state: "live", data_age_s: 3,
  };
}

function shipItem() {
  return {
    entity: {
      id: "ship:DEMO-SHIP-001", type: "ship", identifier: "DEMO-SHIP-001",
      source: "mock", name: "Atlantic Trader", mmsi: "999000001", imo: "IMO9990001",
    },
    position: {
      entity_id: "ship:DEMO-SHIP-001", timestamp: "2024-01-01T00:00:00Z",
      latitude: 38.0, longitude: -28.5, source: "mock",
      speed: 6.2, heading: 90, course: 90, quality: "good",
    },
    data_state: "live", data_age_s: 3,
  };
}

function stubFetch(appMode: "demo" | "local" | "production" = "demo") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/health") {
        return jsonResponse({ status: "ok", app_mode: appMode, version: "0.1.0" });
      }
      if (url === "/api/aircraft") return jsonResponse([aircraftItem()]);
      if (url === "/api/ships") return jsonResponse([shipItem()]);
      if (url === "/api/alerts/active") return jsonResponse([]);
      if (url.startsWith("/api/search")) {
        const q = new URL(url, "http://localhost").searchParams.get("q") ?? "";
        const items = [aircraftItem(), shipItem()];
        const hits = items.filter((i) =>
          JSON.stringify(i).toLowerCase().includes(q.toLowerCase()));
        return jsonResponse(hits);
      }
      return { ok: false, status: 404, json: async () => ({}) };
    }),
  );
}

test("renders status, entity counts and the demo banner", async () => {
  stubFetch();

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/Aircraft: 1/)).toBeTruthy();
  });
  expect(screen.getByText(/Backend: ok/)).toBeTruthy();
  expect(screen.getByText(/mode demo/)).toBeTruthy();
  expect(screen.getByTestId("demo-mode-indicator")).toBeTruthy();
});

test("shows the demo mode indicator with exact wording in demo mode", async () => {
  stubFetch("demo");

  render(<App />);

  await waitFor(() => {
    expect(screen.getByTestId("demo-mode-indicator")).toBeTruthy();
  });
  expect(screen.getByTestId("demo-mode-indicator").textContent).toBe(
    "SIMULATION MODE — DATA IS NOT LIVE",
  );
  expect(screen.getByRole("status")).toBeTruthy();
});

test("never shows the demo mode indicator in production mode", async () => {
  stubFetch("production");

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/mode production/)).toBeTruthy();
  });
  expect(screen.queryByTestId("demo-mode-indicator")).toBeNull();
});

test("does not claim not live in local mode (may carry real data)", async () => {
  stubFetch("local");

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/mode local/)).toBeTruthy();
  });
  expect(screen.queryByTestId("demo-mode-indicator")).toBeNull();
});

test("shows an error when the backend is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/Backend unavailable/)).toBeTruthy();
  });
});

test("selecting an entity shows the detail panel", async () => {
  stubFetch();

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/Aircraft: 1/)).toBeTruthy();
  });

  fireEvent.click(screen.getByText("select-aircraft"));

  expect(screen.getByText("DEMO001")).toBeTruthy();
  expect(screen.getByText(/10668m/)).toBeTruthy();
  expect(screen.getByText(/245.5 m\/s/)).toBeTruthy();
  expect(screen.getByText(/SIMULATION \/ DEMO DATA/)).toBeTruthy();
});

test("selecting a ship shows name, MMSI and course", async () => {
  stubFetch();

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/Ships: 1/)).toBeTruthy();
  });

  fireEvent.click(screen.getByText("select-ship"));

  expect(screen.getByText(/Atlantic Trader/)).toBeTruthy();
  expect(screen.getByText(/MMSI 999000001/)).toBeTruthy();
  expect(screen.getByText(/Course: 90°/)).toBeTruthy();
});

test("search finds a flight number and selects the entity", async () => {
  stubFetch();

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/Aircraft: 1/)).toBeTruthy();
  });

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "DEMO001" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => {
    expect(screen.getByText("DEMO001", { selector: "span" })).toBeTruthy();
  });

  fireEvent.click(screen.getByText("DEMO001", { selector: "span" }));
  expect(screen.getByText("DEMO001", { selector: "h2" })).toBeTruthy();
});

test("search shows a no-matches message for unknown queries", async () => {
  stubFetch();

  render(<App />);

  await waitFor(() => {
    expect(screen.getByText(/Aircraft: 1/)).toBeTruthy();
  });

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ZZZ9" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => {
    expect(screen.getByText(/No matches/)).toBeTruthy();
  });
});

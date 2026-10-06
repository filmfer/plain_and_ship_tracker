import { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import {
  Cartesian2,
  Cartesian3,
  Color,
  ConstantPositionProperty,
  ConstantProperty,
  CustomDataSource,
  Math as CesiumMath,
  NearFarScalar,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  VerticalOrigin,
  Viewer,
  type Entity,
} from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";
import type { EntityPosition } from "../api";

interface CesiumGlobeProps {
  aircraft: EntityPosition[];
  ships: EntityPosition[];
  onSelect: (entityId: string | null) => void;
}

export interface CesiumGlobeHandle {
  focusEntity: (id: string) => void;
}

const AIRCRAFT_COLOR = "#38bdf8";
const SHIP_COLOR = "#fb923c";

const AIRCRAFT_ICON =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">` +
      `<path d="M12 2 L16.5 21 L12 17 L7.5 21 Z" fill="${AIRCRAFT_COLOR}" ` +
      `stroke="#0b1f3a" stroke-width="1.2"/></svg>`,
  );

const SHIP_ICON =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">` +
      `<path d="M12 2 L17.5 14 L14 14 L14 21 L10 21 L10 14 L6.5 14 Z" fill="${SHIP_COLOR}" ` +
      `stroke="#0b1f3a" stroke-width="1.2"/></svg>`,
  );

function aircraftLabel(item: EntityPosition): string {
  const sim = item.entity.source === "mock" ? " (SIM)" : "";
  return `${item.entity.identifier}${sim}`;
}

function shipLabel(item: EntityPosition): string {
  const { entity } = item;
  const sim = entity.source === "mock" ? " (SIM)" : "";
  const name = entity.name ?? entity.identifier;
  const mmsi = entity.mmsi ? `\nMMSI ${entity.mmsi}` : "";
  return `${name}${sim}${mmsi}`;
}

function syncAircraft(source: CustomDataSource | null, items: EntityPosition[]): void {
  if (!source) return;
  // Incremental in-place sync: reuse entities by id so Cesium never rebuilds
  // the whole collection on a tick. Only changed positions/labels are written.
  const seen = new Set<string>();
  for (const item of items) {
    const { entity, position } = item;
    if (!position) continue;
    seen.add(entity.id);
    const heading = position.heading ?? position.course ?? 0;
    const cartesian = Cartesian3.fromDegrees(
      position.longitude, position.latitude, position.altitude ?? 0,
    );
    const existing = source.entities.getById(entity.id);
    if (existing) {
      const pos = existing.position as ConstantPositionProperty | undefined;
      if (pos?.setValue) pos.setValue(cartesian);
      const rot = existing.billboard?.rotation as ConstantProperty | undefined;
      if (rot?.setValue) rot.setValue(-CesiumMath.toRadians(heading));
      const label = existing.label?.text as ConstantProperty | undefined;
      if (label?.setValue) label.setValue(aircraftLabel(item));
      continue;
    }
    source.entities.add({
      id: entity.id,
      name: entity.identifier,
      position: cartesian,
      billboard: {
        image: AIRCRAFT_ICON,
        width: 24, height: 24,
        rotation: -CesiumMath.toRadians(heading),
        verticalOrigin: VerticalOrigin.CENTER,
        scaleByDistance: new NearFarScalar(1.5e6, 1.1, 1.5e7, 0.45),
      },
      label: {
        text: aircraftLabel(item),
        font: "600 12px system-ui, sans-serif",
        fillColor: Color.WHITE,
        outlineColor: Color.BLACK,
        outlineWidth: 2,
        pixelOffset: new Cartesian2(0, -16),
        verticalOrigin: VerticalOrigin.BOTTOM,
        scaleByDistance: new NearFarScalar(1.5e6, 1.0, 1.5e7, 0.4),
      },
    });
  }
  // Drop entities that vanished from the feed (rare, keeps memory bounded).
  for (const stale of source.entities.values.filter((e) => !seen.has(e.id))) {
    source.entities.remove(stale);
  }
}

function syncShips(source: CustomDataSource | null, items: EntityPosition[]): void {
  if (!source) return;
  const seen = new Set<string>();
  for (const item of items) {
    const { entity, position } = item;
    if (!position) continue;
    seen.add(entity.id);
    const course = position.course ?? position.heading ?? 0;
    const cartesian = Cartesian3.fromDegrees(position.longitude, position.latitude, 0);
    const existing = source.entities.getById(entity.id);
    if (existing) {
      const pos = existing.position as ConstantPositionProperty | undefined;
      if (pos?.setValue) pos.setValue(cartesian);
      const rot = existing.billboard?.rotation as ConstantProperty | undefined;
      if (rot?.setValue) rot.setValue(-CesiumMath.toRadians(course));
      const label = existing.label?.text as ConstantProperty | undefined;
      if (label?.setValue) label.setValue(shipLabel(item));
      continue;
    }
    source.entities.add({
      id: entity.id,
      name: entity.name ?? entity.identifier,
      position: cartesian,
      billboard: {
        image: SHIP_ICON,
        width: 24, height: 24,
        rotation: -CesiumMath.toRadians(course),
        verticalOrigin: VerticalOrigin.CENTER,
        scaleByDistance: new NearFarScalar(1.5e6, 1.0, 1.5e7, 0.4),
      },
      label: {
        text: shipLabel(item),
        font: "600 12px system-ui, sans-serif",
        fillColor: Color.WHITE,
        outlineColor: Color.BLACK,
        outlineWidth: 2,
        pixelOffset: new Cartesian2(0, 16),
        verticalOrigin: VerticalOrigin.TOP,
        scaleByDistance: new NearFarScalar(1.5e6, 1.0, 1.5e7, 0.4),
      },
    });
  }
  for (const stale of source.entities.values.filter((e) => !seen.has(e.id))) {
    source.entities.remove(stale);
  }
}

const CesiumGlobe = forwardRef<CesiumGlobeHandle, CesiumGlobeProps>(({ aircraft, ships, onSelect }, ref) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<Viewer | null>(null);
  const aircraftRef = useRef<CustomDataSource | null>(null);
  const shipsRef = useRef<CustomDataSource | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useImperativeHandle(ref, () => ({
    focusEntity: (id: string) => {
      const viewer = viewerRef.current;
      if (!viewer) return;
      let entity: Entity | undefined = aircraftRef.current?.entities.getById(id);
      if (!entity) entity = shipsRef.current?.entities.getById(id);
      if (entity) viewer.zoomTo(entity);
    },
  }));

  useEffect(() => {
    if (!containerRef.current) return;
    const viewer = new Viewer(containerRef.current, {
      baseLayer: false, animation: false, timeline: false, baseLayerPicker: false,
      geocoder: false, homeButton: false, sceneModePicker: true, navigationHelpButton: false,
      fullscreenButton: false, infoBox: false, selectionIndicator: false,
    });
    viewer.scene.globe.baseColor = Color.fromCssColorString("#0b1f3a");
    viewer.scene.backgroundColor = Color.fromCssColorString("#020617");
    viewer.camera.setView({ destination: Cartesian3.fromDegrees(-28.0, 30.0, 1.8e7) });

    const aircraftSource = new CustomDataSource("aircraft");
    const shipSource = new CustomDataSource("ships");
    void viewer.dataSources.add(aircraftSource);
    void viewer.dataSources.add(shipSource);

    const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction((movement: { position: Cartesian2 }) => {
      const picked = viewer.scene.pick(movement.position);
      const id = (picked?.id as { id?: string } | undefined)?.id;
      onSelectRef.current(typeof id === "string" ? id : null);
    }, ScreenSpaceEventType.LEFT_CLICK);

    viewerRef.current = viewer;
    aircraftRef.current = aircraftSource;
    shipsRef.current = shipSource;

    return () => {
      handler.destroy();
      viewerRef.current = null;
      aircraftRef.current = null;
      shipsRef.current = null;
      viewer.destroy();
    };
  }, []);

  useEffect(() => {
    syncAircraft(aircraftRef.current, aircraft);
  }, [aircraft]);

  useEffect(() => {
    syncShips(shipsRef.current, ships);
  }, [ships]);

  return <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />;
});

export default CesiumGlobe;

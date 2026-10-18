"use client";

import { useEffect, useRef, useState } from "react";

import { lineStyle } from "@/lib/lines";
import { decodePolyline } from "@/lib/polyline";
import type { Route } from "@/lib/types";

import styles from "./RouteMap.module.css";

export const MAP_STYLE_URL =
  process.env.NEXT_PUBLIC_MAP_STYLE_URL ?? "https://tiles.openfreemap.org/styles/positron";

/**
 * The route drawn on an OpenStreetMap base map (MapLibre + OpenFreeMap
 * tiles, no API key). The step list beside it carries the same
 * information, so the map is a picture for screen readers, not a control.
 */
export function RouteMap({ route, label }: { route: Route; label: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let map: import("maplibre-gl").Map | undefined;
    let cancelled = false;

    (async () => {
      try {
        const maplibre = await import("maplibre-gl");
        if (cancelled) return;
        map = new maplibre.Map({
          container: element,
          style: MAP_STYLE_URL,
          interactive: true,
          attributionControl: { compact: true },
          cooperativeGestures: true,
        });
        map.on("error", () => undefined);
        const coordinates: [number, number][] = [];
        const features = route.legs.map((leg, index) => {
          const line = leg.geometry
            ? decodePolyline(leg.geometry)
            : [
                [leg.origin.lon, leg.origin.lat] as [number, number],
                [leg.destination.lon, leg.destination.lat] as [number, number],
              ];
          coordinates.push(...line);
          const style = lineStyle(leg.route_id, leg.route);
          return {
            type: "Feature" as const,
            properties: {
              index,
              walk: leg.mode === "WALK" ? 1 : 0,
              color: leg.mode === "WALK" ? "#5e5850" : style.background,
            },
            geometry: { type: "LineString" as const, coordinates: line },
          };
        });
        const stops = route.legs
          .filter((leg) => leg.route_id)
          .flatMap((leg) => [leg.origin, leg.destination])
          .map((place) => ({
            type: "Feature" as const,
            properties: {},
            geometry: { type: "Point" as const, coordinates: [place.lon, place.lat] },
          }));

        map.on("load", () => {
          if (!map) return;
          map.addSource("route", { type: "geojson", data: { type: "FeatureCollection", features } });
          map.addSource("stops", { type: "geojson", data: { type: "FeatureCollection", features: stops } });
          map.addLayer({
            id: "route-casing",
            type: "line",
            source: "route",
            filter: ["==", ["get", "walk"], 0],
            paint: { "line-color": "#111111", "line-width": 9 },
            layout: { "line-cap": "round", "line-join": "round" },
          });
          map.addLayer({
            id: "route-transit",
            type: "line",
            source: "route",
            filter: ["==", ["get", "walk"], 0],
            paint: { "line-color": ["get", "color"], "line-width": 5 },
            layout: { "line-cap": "round", "line-join": "round" },
          });
          map.addLayer({
            id: "route-walk",
            type: "line",
            source: "route",
            filter: ["==", ["get", "walk"], 1],
            paint: { "line-color": "#5e5850", "line-width": 3, "line-dasharray": [1, 1.5] },
            layout: { "line-cap": "round" },
          });
          map.addLayer({
            id: "route-stops",
            type: "circle",
            source: "stops",
            paint: { "circle-radius": 5, "circle-color": "#ffffff", "circle-stroke-color": "#111111", "circle-stroke-width": 3 },
          });
        });

        if (coordinates.length) {
          const bounds = coordinates.reduce(
            (b, c) => b.extend(c),
            new maplibre.LngLatBounds(coordinates[0], coordinates[0]),
          );
          map.fitBounds(bounds, { padding: 40, duration: 0, maxZoom: 15 });
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      map?.remove();
    };
  }, [route]);

  if (failed) {
    return (
      <div className={`${styles.map} ${styles.fallback}`}>
        <p>The map can’t be shown on this device. Every step is listed below.</p>
      </div>
    );
  }
  return <div ref={container} className={styles.map} role="img" aria-label={label} />;
}

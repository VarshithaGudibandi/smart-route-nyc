/**
 * The trip a rider asked for, kept in the page URL so a search survives a
 * reload, can be shared, and works with the back button. Nothing is stored
 * anywhere else; coordinates are rounded to about 11 m.
 */
import type { SearchRequest } from "./types";

export type Endpoint = { lat: number; lon: number; label: string };

export type Needs = {
  stepFree: boolean;
  wheelchair: boolean;
  maxWalk: number | null;
  lessWalking: boolean;
  fewerTransfers: boolean;
};

export type Trip = {
  from: Endpoint | null;
  to: Endpoint | null;
  // ISO time, or null for "leave now".
  departAt: string | null;
  needs: Needs;
  routeId: string | null;
};

export const NO_NEEDS: Needs = {
  stepFree: false,
  wheelchair: false,
  maxWalk: null,
  lessWalking: false,
  fewerTransfers: false,
};

const round = (value: number) => Math.round(value * 1e4) / 1e4;

function parseEndpoint(value: string | null): Endpoint | null {
  if (!value) return null;
  const [lat, lon, ...label] = value.split(",");
  const point = { lat: Number(lat), lon: Number(lon), label: label.join(",") || "Selected place" };
  return Number.isFinite(point.lat) && Number.isFinite(point.lon) ? point : null;
}

function formatEndpoint(point: Endpoint): string {
  return `${round(point.lat)},${round(point.lon)},${point.label}`;
}

export function parseTrip(params: URLSearchParams): Trip {
  const walk = Number(params.get("walk"));
  return {
    from: parseEndpoint(params.get("from")),
    to: parseEndpoint(params.get("to")),
    departAt: params.get("at") || null,
    needs: {
      wheelchair: params.get("wc") === "1",
      // Wheelchair users also need step-free travel.
      stepFree: params.get("sf") === "1" || params.get("wc") === "1",
      maxWalk: Number.isFinite(walk) && walk >= 100 ? walk : null,
      lessWalking: params.get("lw") === "1",
      fewerTransfers: params.get("ft") === "1",
    },
    routeId: params.get("route"),
  };
}

export function tripParams(trip: Trip): URLSearchParams {
  const params = new URLSearchParams();
  if (trip.from) params.set("from", formatEndpoint(trip.from));
  if (trip.to) params.set("to", formatEndpoint(trip.to));
  if (trip.departAt) params.set("at", trip.departAt);
  if (trip.needs.stepFree) params.set("sf", "1");
  if (trip.needs.wheelchair) params.set("wc", "1");
  if (trip.needs.maxWalk) params.set("walk", String(trip.needs.maxWalk));
  if (trip.needs.lessWalking) params.set("lw", "1");
  if (trip.needs.fewerTransfers) params.set("ft", "1");
  if (trip.routeId) params.set("route", trip.routeId);
  return params;
}

/** A stable key for "the same search", ignoring which route is open. */
export function searchKey(trip: Trip): string | null {
  if (!trip.from || !trip.to) return null;
  const params = tripParams({ ...trip, routeId: null });
  return params.toString();
}

export function searchRequest(trip: Trip): SearchRequest | null {
  if (!trip.from || !trip.to) return null;
  return {
    origin: { lat: trip.from.lat, lon: trip.from.lon, label: trip.from.label },
    destination: { lat: trip.to.lat, lon: trip.to.lon, label: trip.to.label },
    depart_at: trip.departAt,
    accessibility: {
      step_free_required: trip.needs.stepFree,
      wheelchair_required: trip.needs.wheelchair,
      max_walk_meters: trip.needs.maxWalk,
    },
    optimization: {
      primary: "FASTEST",
      prefer_less_walking: trip.needs.lessWalking,
      prefer_fewer_transfers: trip.needs.fewerTransfers,
    },
  };
}

export function needsFromPreferences(state: {
  accessibility?: { step_free_required?: boolean; wheelchair_required?: boolean; max_walk_meters?: number | null };
  optimization?: { prefer_less_walking?: boolean; prefer_fewer_transfers?: boolean; primary?: string };
}): Needs {
  const a = state.accessibility ?? {};
  const o = state.optimization ?? {};
  return {
    wheelchair: Boolean(a.wheelchair_required),
    stepFree: Boolean(a.step_free_required || a.wheelchair_required),
    maxWalk: a.max_walk_meters ?? null,
    lessWalking: Boolean(o.prefer_less_walking || o.primary === "LEAST_WALKING"),
    fewerTransfers: Boolean(o.prefer_fewer_transfers || o.primary === "FEWEST_TRANSFERS"),
  };
}

export function preferencesFromNeeds(needs: Needs) {
  return {
    accessibility: {
      step_free_required: needs.stepFree,
      wheelchair_required: needs.wheelchair,
      max_walk_meters: needs.maxWalk,
    },
    optimization: {
      primary: "FASTEST" as const,
      prefer_less_walking: needs.lessWalking,
      prefer_fewer_transfers: needs.fewerTransfers,
    },
  };
}

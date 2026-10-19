import type { AccessibilityStatus, Finding, Leg, Route } from "./types";

export function transitLegs(route: Route): Leg[] {
  return route.legs.filter((leg) => leg.route_id);
}

/** Where the rider changes vehicles, in order. */
export function transferPlaces(route: Route): string[] {
  const legs = transitLegs(route);
  return legs.slice(1).map((leg) => leg.origin.name ?? "a station");
}

export function findingCounts(route: Route): Record<AccessibilityStatus, number> {
  const counts: Record<AccessibilityStatus, number> = { meets: 0, violates: 0, unknown: 0 };
  for (const finding of route.accessibility_findings ?? []) counts[finding.status] += 1;
  return counts;
}

/** Station findings for a stop, matched by the name the route uses for it. */
export function stationFinding(route: Route, name: string | null | undefined): Finding | undefined {
  return (route.accessibility_findings ?? []).find((f) => f.kind === "station" && f.location === name);
}

const byArrival = (a: Route, b: Route) =>
  Date.parse(a.arrival) - Date.parse(b.arrival) || a.duration_seconds - b.duration_seconds;

/** Soonest arrival first; with "Fewer transfers", fewest changes first, then soonest. */
export function sortRoutes(routes: Route[], fewerTransfers: boolean): Route[] {
  return [...routes].sort(
    fewerTransfers ? (a, b) => a.transfers - b.transfers || byArrival(a, b) : byArrival,
  );
}

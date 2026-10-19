import { clock, distance, duration } from "@/lib/format";
import { findingCounts, transferPlaces, transitLegs } from "@/lib/route";
import type { Route } from "@/lib/types";

import { LineBullet } from "./LineBullet";
import styles from "./RouteCard.module.css";
import { StatusChip, StatusMark } from "./StatusMark";

type Props = {
  route: Route;
  selected: boolean;
  onSelect: () => void;
  savesSeconds?: number;
  fastest?: boolean;
  accessibilityRequired: boolean;
};

export function RouteCard({ route, selected, onSelect, savesSeconds, fastest = false, accessibilityRequired }: Props) {
  const legs = transitLegs(route);
  const changes = transferPlaces(route);
  const counts = findingCounts(route);
  const hasFindings = (route.accessibility_findings ?? []).length > 0;
  const saved = savesSeconds ? Math.floor(savesSeconds / 60) : 0;
  const showAccess = hasFindings && (accessibilityRequired || route.accessibility_status !== "meets");

  return (
    <button
      type="button"
      className={`${styles.card} ${savesSeconds ? styles.featured : ""} ${selected ? styles.selected : ""}`}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
    >
      <span className={styles.top}>
        <span className={styles.times}>
          {clock(route.departure)} – {clock(route.arrival)}
        </span>
        <span className={styles.duration}>{duration(route.duration_seconds)}</span>
      </span>

      <span className={styles.lines}>
        {legs.map((leg, index) => (
          <span key={index} className={styles.lineStep}>
            {index > 0 && (
              <span className={styles.dash} aria-hidden="true">
                ›
              </span>
            )}
            <LineBullet routeId={leg.route_id} name={leg.route} mode={leg.mode} size="small" />
          </span>
        ))}
        <span className={styles.via}>
          {changes.length === 0
            ? "No transfer"
            : changes.length <= 2
              ? `Change at ${changes.join(", then ")}`
              : `${changes.length} transfers`}
          {route.walk_meters >= 400 && ` · ${distance(route.walk_meters)} walk`}
        </span>
      </span>

      {(saved > 0 || fastest || showAccess) && (
        <span className={styles.badges}>
          {saved > 0 && <span className={styles.faster}>{saved} MIN FASTER</span>}
          {fastest && <span className={styles.fastest}>Fastest</span>}
          {showAccess && route.accessibility_status === "meets" && <StatusChip status="meets" subject="Step-free" />}
          {showAccess && route.accessibility_status !== "meets" && (
            <span className={styles.counts}>
              {counts.violates > 0 && (
                <span className={styles.count}>
                  <StatusMark status="violates" size="small" />
                  {counts.violates} {counts.violates === 1 ? "barrier" : "barriers"}
                </span>
              )}
              {counts.unknown > 0 && (
                <span className={styles.count}>
                  <StatusMark status="unknown" size="small" />
                  {counts.unknown} not confirmed
                </span>
              )}
            </span>
          )}
        </span>
      )}
    </button>
  );
}

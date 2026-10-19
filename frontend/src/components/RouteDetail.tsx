"use client";

import { useEffect, useRef } from "react";

import { clock, clockWithPeriod, distance, duration, friendlyText, minutes, placeName } from "@/lib/format";
import { lineName } from "@/lib/lines";
import { stationFinding } from "@/lib/route";
import type { DataFreshness, Notice, Route } from "@/lib/types";

import { Explanation } from "./Explanation";
import { Feedback } from "./Feedback";
import { LineBullet } from "./LineBullet";
import styles from "./RouteDetail.module.css";
import { RouteMap } from "./RouteMap";
import { STATUS_WORD, StatusMark } from "./StatusMark";

type Props = {
  route: Route;
  searchId: string;
  freshness: DataFreshness[];
  notices: Notice[];
  accessibilityRequired: boolean;
  onBack?: () => void;
};

const CHECK_TITLE = {
  meets: "Step-free check · confirmed",
  violates: "Step-free check · barrier found",
  unknown: "Step-free check · not confirmed",
} as const;

export function RouteDetail({ route, searchId, freshness, notices, accessibilityRequired, onBack }: Props) {
  const title = useRef<HTMLHeadingElement>(null);

  // A route the rider chose gets focus, so keyboard and screen-reader users
  // land on it. On a phone the detail replaces the list, so it is also
  // scrolled into view; beside the list on wider screens, nothing jumps.
  const chosen = Boolean(onBack);
  useEffect(() => {
    if (!chosen || !title.current) return;
    const narrow = window.matchMedia("(max-width: 720px)").matches;
    title.current.focus({ preventScroll: !narrow });
    if (narrow) title.current.scrollIntoView({ block: "start" });
  }, [route.route_id, chosen]);

  const elevators = freshness.find((f) => f.source === "mta_elevators");
  const findings = route.accessibility_findings ?? [];
  const first = route.legs[0];
  const last = route.legs[route.legs.length - 1];

  return (
    <article className={styles.panel} aria-labelledby="route-title">
      <RouteMap
        route={route}
        label={`Map of the route from ${placeName(first?.origin.name, "the start")} to ${placeName(last?.destination.name, "the destination")}`}
      />
      <div className={styles.body}>
        {onBack && (
          <button type="button" className={styles.back} onClick={onBack}>
            ← All routes
          </button>
        )}
        <div>
          <h2 id="route-title" ref={title} className={styles.title} tabIndex={-1}>
            {clock(route.departure)} – {clock(route.arrival)}
          </h2>
          <p className={styles.summary}>
            {duration(route.duration_seconds)} · {route.transfers} {route.transfers === 1 ? "transfer" : "transfers"} ·{" "}
            {distance(route.walk_meters)} walking
          </p>
          {route.origin_type !== "otp_baseline" && (
            <p className={styles.origin}>
              {route.origin_type === "model_suggestion" ? "Suggested by Claude: a" : "A"} transfer the planner did not
              pick by default, checked against the timetable.
            </p>
          )}
        </div>

        {notices.length > 0 && (
          <ul className={styles.alerts} aria-label="Service alerts">
            {notices.map((notice, index) => (
              <li key={index}>
                <strong>Service alert.</strong> {notice.message}
              </li>
            ))}
          </ul>
        )}

        <ol className={styles.steps}>
          {route.legs.map((leg, index) => {
            const previous = route.legs[index - 1];
            const change =
              leg.route_id && route.legs.slice(0, index).some((l) => l.route_id) ? leg.origin.name : null;
            const elevator = change ? stationFinding(route, change) : undefined;
            if (leg.mode === "WALK") {
              return (
                <li key={index} className={styles.step}>
                  <span className={styles.time}>{clock(leg.start_time)}</span>
                  <span className={styles.icon} aria-hidden="true">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="13" cy="4" r="2" /><path d="M11 21l2-6 3 3v3M9 12l2-4h3l2 4 3 1M10 8l-3 5" /></svg>
                  </span>
                  <span>
                    Walk {distance(leg.distance_meters)} (about {Math.max(1, minutes(Date.parse(leg.end_time) / 1000 - Date.parse(leg.start_time) / 1000))} min) to{" "}
                    <strong>{placeName(leg.destination.name, "your destination")}</strong>
                    {index === route.legs.length - 1 && <>, arrive <strong>{clock(leg.end_time)}</strong></>}
                  </span>
                </li>
              );
            }
            return (
              <li key={index} className={`${styles.step} ${styles.ride}`}>
                {change && previous && (
                  <span className={styles.change}>
                    <strong>Change at {change}</strong>
                    {elevator && (
                      <span className={styles.changeStatus}>
                        <StatusMark status={elevator.status} size="small" /> {elevator.description}
                      </span>
                    )}
                  </span>
                )}
                <span className={styles.time}>{clock(leg.start_time)}</span>
                <LineBullet routeId={leg.route_id} name={leg.route} mode={leg.mode} size="small" />
                <span>
                  <strong>{lineName(leg.route_id, leg.route, leg.mode)}</strong>
                  {leg.headsign && <> toward {leg.headsign}</>} from {leg.origin.name} to <strong>{leg.destination.name}</strong>,
                  arrive {clock(leg.end_time)}
                  {leg.realtime && <span className={styles.live}> · live time</span>}
                </span>
              </li>
            );
          })}
        </ol>

        {findings.length > 0 && (
          <section className={styles.check} aria-labelledby="check-title">
            <h3 id="check-title" className="label-caps">
              {CHECK_TITLE[route.accessibility_status]}
            </h3>
            <ul className={styles.findings}>
              {findings.map((finding, index) => (
                <li key={index} className={styles.finding}>
                  <StatusMark status={finding.status} />
                  <span>
                    <span className="visually-hidden">{STATUS_WORD[finding.status]}: </span>
                    <strong>{friendlyText(finding.location)}</strong> · {finding.description}
                  </span>
                </li>
              ))}
            </ul>
            <p className={styles.small}>
              {elevators?.as_of
                ? `MTA elevator status as of ${clockWithPeriod(elevators.as_of)} (${elevators.freshness}); it can change before you travel.`
                : "MTA elevator status is not available right now."}{" "}
              Walks are checked in OpenStreetMap, which can be incomplete.
            </p>
            {!accessibilityRequired && route.accessibility_status !== "meets" && (
              <p className={styles.small}>Turn on Step-free above to see only routes that meet it.</p>
            )}
          </section>
        )}

        <Explanation key={`explain-${route.route_id}`} searchId={searchId} routeId={route.route_id} />
        <Feedback key={`feedback-${route.route_id}`} searchId={searchId} routeId={route.route_id} />
      </div>
    </article>
  );
}

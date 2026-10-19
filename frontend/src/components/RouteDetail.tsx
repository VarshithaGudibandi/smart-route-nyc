"use client";

import { useEffect, useRef } from "react";

import { clock, clockWithPeriod, distance, duration, findingPlace, minutes, placeName } from "@/lib/format";
import { lineName } from "@/lib/lines";
import { findingCounts, stationFinding } from "@/lib/route";
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
  // Why a suggested route changes where it does, in the planner's words.
  suggestion?: string;
  accessibilityRequired: boolean;
  onBack?: () => void;
};

// Walks shorter than this are leaving a station or crossing a street; the
// time matters, the distance does not.
const SHORT_WALK_M = 50;

export function RouteDetail({ route, searchId, freshness, notices, suggestion, accessibilityRequired, onBack }: Props) {
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
  const counts = findingCounts(route);
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
            {distance(route.walk_meters)} walk
          </p>
          {route.origin_type !== "otp_baseline" && (
            <p className={styles.origin}>
              <strong>{route.origin_type === "model_suggestion" ? "AI plan" : "Faster plan"}:</strong>{" "}
              {suggestion || "a transfer the planner skipped"} Timed against the schedule.
            </p>
          )}
        </div>

        {notices.length > 0 && (
          <ul className={styles.alerts} aria-label="Service alerts">
            {notices.map((notice, index) => (
              <li key={index}>{notice.message}</li>
            ))}
          </ul>
        )}

        <ol className={styles.steps}>
          {route.legs.map((leg, index) => {
            const change =
              leg.route_id && route.legs.slice(0, index).some((l) => l.route_id) ? leg.origin.name : null;
            const elevator = change ? stationFinding(route, change) : undefined;
            if (leg.mode === "WALK") {
              const walkMinutes = Math.max(1, minutes(Date.parse(leg.end_time) / 1000 - Date.parse(leg.start_time) / 1000));
              const final = index === route.legs.length - 1;
              return (
                <li key={index} className={styles.step}>
                  <span className={styles.time}>{clock(leg.start_time)}</span>
                  <span className={styles.icon} aria-hidden="true">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="13" cy="4" r="2" /><path d="M11 21l2-6 3 3v3M9 12l2-4h3l2 4 3 1M10 8l-3 5" /></svg>
                  </span>
                  <span>
                    Walk {walkMinutes} min
                    {leg.distance_meters >= SHORT_WALK_M && <span className={styles.muted}> · {distance(leg.distance_meters)}</span>}
                    <span className={styles.sub}>to {placeName(leg.destination.name, "your destination")}</span>
                  </span>
                  {final && (
                    <>
                      <span className={styles.time}>{clock(leg.end_time)}</span>
                      <span className={styles.icon} aria-hidden="true">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z" /><circle cx="12" cy="10" r="2.5" /></svg>
                      </span>
                      <strong>Arrive</strong>
                    </>
                  )}
                </li>
              );
            }
            return (
              <li key={index} className={`${styles.step} ${styles.ride}`}>
                {change && (
                  <span className={styles.change}>
                    <strong>Change at {change}</strong>
                    {elevator && (
                      <span className={styles.changeStatus}>
                        <StatusMark status={elevator.status} size="small" />
                        <span className="visually-hidden">{STATUS_WORD[elevator.status]}: </span>
                        {elevator.description}
                      </span>
                    )}
                  </span>
                )}
                <span className={styles.time}>{clock(leg.start_time)}</span>
                <LineBullet routeId={leg.route_id} name={leg.route} mode={leg.mode} size="small" />
                <span>
                  <strong>{leg.origin.name}</strong> → <strong>{leg.destination.name}</strong>
                  <span className={styles.sub}>
                    {lineName(leg.route_id, leg.route, leg.mode)}
                    {leg.headsign && <> to {leg.headsign}</>} · arrive {clock(leg.end_time)}
                    {leg.realtime && <span className={styles.live}> · live</span>}
                  </span>
                </span>
              </li>
            );
          })}
        </ol>

        {findings.length > 0 && (
          <details className={styles.check} open={accessibilityRequired}>
            <summary className={styles.checkSummary}>
              <StatusMark status={route.accessibility_status} />
              <span>
                <strong>Step-free access</strong>
                <span className={styles.checkCounts}>
                  {route.accessibility_status === "meets"
                    ? "Every station and walk checked"
                    : [
                        counts.violates && `${counts.violates} ${counts.violates === 1 ? "barrier" : "barriers"}`,
                        counts.unknown && `${counts.unknown} not confirmed`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                </span>
              </span>
            </summary>
            <ul className={styles.findings}>
              {findings.map((finding, index) => (
                <li key={index} className={styles.finding}>
                  <StatusMark status={finding.status} size="small" />
                  <span>
                    <span className="visually-hidden">{STATUS_WORD[finding.status]}: </span>
                    <strong>{findingPlace(finding.location)}</strong>
                    <span className={styles.muted}> · {finding.description}</span>
                  </span>
                </li>
              ))}
            </ul>
            <p className={styles.small}>
              {elevators?.as_of ? `Elevators as of ${clockWithPeriod(elevators.as_of)}.` : "Elevator status unavailable."}
            </p>
          </details>
        )}

        <Explanation key={`explain-${route.route_id}`} searchId={searchId} routeId={route.route_id} />
        <Feedback key={`feedback-${route.route_id}`} searchId={searchId} routeId={route.route_id} />
      </div>
    </article>
  );
}

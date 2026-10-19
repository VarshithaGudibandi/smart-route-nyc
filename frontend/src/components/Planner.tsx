"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { useInvestigation } from "@/hooks/useInvestigation";
import { useRouteSearch } from "@/hooks/useRouteSearch";
import { describeError } from "@/lib/api";
import { clockWithPeriod, dayAndTime } from "@/lib/format";
import { parseTrip, searchKey, tripParams, type Endpoint, type Needs, type Trip } from "@/lib/trip";
import type { Notice, Route, SearchResponse } from "@/lib/types";

import styles from "./Planner.module.css";
import { RouteCard } from "./RouteCard";
import { RouteDetail } from "./RouteDetail";
import { SearchPanel } from "./SearchPanel";
import { StatusMark } from "./StatusMark";

export function Planner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const trip = useMemo(() => parseTrip(new URLSearchParams(params.toString())), [params]);
  const key = searchKey(trip);
  const [attempt, setAttempt] = useState(0);
  const search = useRouteSearch(trip, key, attempt);
  const response = search.status === "done" ? search.response : null;
  const investigation = useInvestigation(response);

  const navigate = (next: Trip, mode: "push" | "replace" = "push") => {
    const query = tripParams(next).toString();
    const url = query ? `${pathname}?${query}` : pathname;
    if (mode === "push") router.push(url, { scroll: false });
    else router.replace(url, { scroll: false });
  };

  const suggestions = investigation?.status === "completed" ? (investigation.suggestions ?? []) : [];
  const allRoutes: Route[] = [
    ...suggestions.map((s) => s.route),
    ...(response?.routes ?? []),
    ...(response?.review_only_routes ?? []),
  ];
  const selected = allRoutes.find((r) => r.route_id === trip.routeId) ?? allRoutes[0] ?? null;
  const selectedNotices: Notice[] =
    suggestions.find((s) => s.route.route_id === selected?.route_id)?.notices ?? [];
  const accessibilityRequired = trip.needs.stepFree || trip.needs.wheelchair;

  return (
    <div className={styles.page}>
      <SearchPanel
        from={trip.from}
        to={trip.to}
        departAt={trip.departAt}
        needs={trip.needs}
        onSearch={({ from, to, departAt }: { from: Endpoint; to: Endpoint; departAt: string | null }) =>
          navigate({ ...trip, from, to, departAt, routeId: null })
        }
        onNeedsChange={(needs: Needs) => navigate({ ...trip, needs, routeId: null }, "replace")}
      />

      <div className={`${styles.layout} ${trip.routeId ? styles.showDetail : ""}`}>
        <main id="results" className={styles.results} tabIndex={-1} aria-busy={search.status === "loading"}>
          <Status message={statusMessage(search.status, response, trip)} />
          {search.status === "idle" && <Welcome />}
          {search.status === "loading" && <Loading />}
          {search.status === "error" && (
            <div className={styles.error} role="alert">
              <p>{describeError(search.error)}</p>
              <button type="button" className={styles.retry} onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            </div>
          )}
          {response && (
            <ResultsList
              response={response}
              trip={trip}
              suggestions={suggestions}
              investigating={Boolean(response.investigation) && !investigation?.status.match(/completed|failed|cancelled/)}
              selectedId={selected?.route_id ?? null}
              onSelect={(routeId) => navigate({ ...trip, routeId })}
              accessibilityRequired={accessibilityRequired}
            />
          )}
        </main>

        {response && selected && (
          <section className={styles.detail} aria-label="Selected route">
            <RouteDetail
              key={selected.route_id}
              route={selected}
              searchId={response.search_id}
              freshness={response.data_freshness ?? []}
              notices={selectedNotices}
              accessibilityRequired={accessibilityRequired}
              onBack={trip.routeId ? () => navigate({ ...trip, routeId: null }, "replace") : undefined}
            />
          </section>
        )}
      </div>
      <footer className={styles.footer}>Data: MTA, PATH, © OpenStreetMap contributors. Tiles: OpenFreeMap.</footer>
    </div>
  );
}

function statusMessage(status: string, response: SearchResponse | null, trip: Trip): string {
  if (status === "loading") return "Finding routes…";
  if (status === "error") return "The search did not complete.";
  if (!response) return "";
  const count = response.routes.length;
  const review = response.review_only_routes?.length ?? 0;
  const when = trip.departAt ? ` leaving ${dayAndTime(trip.departAt)}` : "";
  return `${count} ${count === 1 ? "route" : "routes"} found${when}${review ? `, ${review} to review` : ""}.`;
}

function Status({ message }: { message: string }) {
  return (
    <p className="visually-hidden" role="status" aria-live="polite">
      {message}
    </p>
  );
}

function Welcome() {
  return (
    <div className={styles.welcome}>
      <h1 className={styles.welcomeTitle}>Subway, bus and PATH trips that fit your needs</h1>
      <p>Choose a start and a destination. Turn on Step-free or Wheelchair to see only routes that work for you.</p>
      <ul className={styles.legend}>
        <li>
          <StatusMark status="meets" /> Step-free
        </li>
        <li>
          <StatusMark status="violates" /> Barrier
        </li>
        <li>
          <StatusMark status="unknown" /> Not confirmed
        </li>
      </ul>
    </div>
  );
}

function Loading() {
  return (
    <div className={styles.loading} aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className={styles.skeleton} />
      ))}
    </div>
  );
}

type ListProps = {
  response: SearchResponse;
  trip: Trip;
  suggestions: { route: Route; saves_seconds: number }[];
  investigating: boolean;
  selectedId: string | null;
  onSelect: (routeId: string) => void;
  accessibilityRequired: boolean;
};

function ResultsList({ response, trip, suggestions, investigating, selectedId, onSelect, accessibilityRequired }: ListProps) {
  // Soonest arrival first: for a rider leaving now, that is the fastest way there.
  const routes = [...response.routes].sort(byArrival);
  const review = [...(response.review_only_routes ?? [])].sort(byArrival);
  const fastestId = routes[0]?.route_id;
  const elevators = (response.data_freshness ?? []).find((f) => f.source === "mta_elevators");
  const notices = response.notices.filter((n) => n.code !== "NO_CONFIRMED_ACCESSIBLE_ROUTE");
  const noConfirmed = response.notices.some((n) => n.code === "NO_CONFIRMED_ACCESSIBLE_ROUTE");
  const noun = trip.needs.wheelchair ? "wheelchair-accessible" : trip.needs.stepFree ? "step-free" : "";
  const total = routes.length + suggestions.length;

  return (
    <div className={styles.list}>
      <div className={styles.listHead}>
        <h1 className={styles.count}>
          {total === 0 ? "No" : total} {noun} {total === 1 ? "route" : "routes"}
        </h1>
        {elevators?.as_of && <span className={styles.meta}>Elevators as of {clockWithPeriod(elevators.as_of)}</span>}
      </div>

      {notices.map((notice) => (
        <p key={notice.code} className={styles.notice}>
          {notice.message}
        </p>
      ))}

      {investigating && (
        <p className={styles.investigating}>
          <span className={styles.pulse} aria-hidden="true" /> Checking for a faster transfer…
        </p>
      )}

      {suggestions.length > 0 && (
        <Section title="Faster with a transfer">
          {suggestions.map((s) => (
            <RouteCard
              key={s.route.route_id}
              route={s.route}
              savesSeconds={s.saves_seconds}
              selected={s.route.route_id === selectedId}
              onSelect={() => onSelect(s.route.route_id)}
              accessibilityRequired={accessibilityRequired}
            />
          ))}
        </Section>
      )}

      {routes.length > 0 && (
        <Section title={suggestions.length ? "Other routes" : "Routes"} hideTitle={!suggestions.length}>
          {routes.map((route) => (
            <RouteCard
              key={route.route_id}
              route={route}
              fastest={route.route_id === fastestId && routes.length > 1 && !suggestions.length}
              selected={route.route_id === selectedId}
              onSelect={() => onSelect(route.route_id)}
              accessibilityRequired={accessibilityRequired}
            />
          ))}
        </Section>
      )}

      {review.length > 0 && (
        <>
          {noConfirmed && (
            <div className={styles.reviewNotice} role="status">
              <StatusMark status="violates" />
              <div>
                <p className={styles.reviewTitle}>No confirmed {noun || "step-free"} route</p>
                <p>These options have a barrier or a station we couldn’t check.</p>
              </div>
            </div>
          )}
          <Section title="Needs review">
            {review.map((route) => (
              <RouteCard
                key={route.route_id}
                route={route}
                selected={route.route_id === selectedId}
                onSelect={() => onSelect(route.route_id)}
                accessibilityRequired={accessibilityRequired}
              />
            ))}
          </Section>
        </>
      )}

      {total === 0 && review.length === 0 && (
        <p className={styles.notice}>No routes were found for this trip and time. Try a different time or place.</p>
      )}
    </div>
  );
}

const byArrival = (a: Route, b: Route) =>
  Date.parse(a.arrival) - Date.parse(b.arrival) || a.duration_seconds - b.duration_seconds;

function Section({ title, hideTitle = false, children }: { title: string; hideTitle?: boolean; children: React.ReactNode }) {
  return (
    <section className={styles.section} aria-label={title}>
      <h2 className={hideTitle ? "visually-hidden" : "label-caps"}>{title}</h2>
      <ul className={styles.cards}>
        {Array.isArray(children)
          ? children.map((child, index) => <li key={index}>{child}</li>)
          : <li>{children}</li>}
      </ul>
    </section>
  );
}

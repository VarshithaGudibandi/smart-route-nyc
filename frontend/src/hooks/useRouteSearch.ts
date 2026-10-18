"use client";

import { useEffect, useState } from "react";

import { api, unwrap } from "@/lib/api";
import { searchRequest, type Trip } from "@/lib/trip";
import type { SearchResponse } from "@/lib/types";

export type SearchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; response: SearchResponse }
  | { status: "error"; error: unknown };

type Settled = { key: string; response?: SearchResponse; error?: unknown };

/**
 * Runs a route search whenever the searched trip changes; a newer search
 * cancels an older one. Results are kept with the search they answer, so a
 * late answer to an old search is never shown for a new one.
 */
export function useRouteSearch(trip: Trip, key: string | null, attempt = 0): SearchState {
  const [settled, setSettled] = useState<Settled | null>(null);
  const requestKey = key ? `${key}#${attempt}` : null;

  useEffect(() => {
    const body = searchRequest(trip);
    if (!requestKey || !body) return;
    const controller = new AbortController();
    api
      .POST("/api/v1/routes/search", { body, signal: controller.signal })
      .then(unwrap)
      .then((response) => setSettled({ key: requestKey, response }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, error });
      });
    return () => controller.abort();
    // The key captures every field of the trip that changes the search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  if (!requestKey) return { status: "idle" };
  if (settled?.key !== requestKey) return { status: "loading" };
  if (settled.response) return { status: "done", response: settled.response };
  return { status: "error", error: settled.error };
}

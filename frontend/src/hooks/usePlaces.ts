"use client";

import { useEffect, useState } from "react";

import { api, unwrap } from "@/lib/api";
import type { PlacesResponse } from "@/lib/types";

const DEBOUNCE_MS = 250;

type Settled = { query: string; data?: PlacesResponse; failed?: boolean };

/** Place suggestions for what the rider is typing, debounced and cancelled when stale. */
export function usePlaces(query: string): { data: PlacesResponse | null; loading: boolean; failed: boolean } {
  const [settled, setSettled] = useState<Settled | null>(null);
  const q = query.trim();
  const active = q.length >= 2;

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .GET("/api/v1/places", { params: { query: { q, limit: 6 } }, signal: controller.signal })
        .then(unwrap)
        .then((data) => setSettled({ query: q, data }))
        .catch(() => {
          if (!controller.signal.aborted) setSettled({ query: q, failed: true });
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [q, active]);

  if (!active) return { data: null, loading: false, failed: false };
  const current = settled?.query === q ? settled : null;
  return {
    // Keep showing the previous suggestions while the next ones load.
    data: current?.data ?? settled?.data ?? null,
    loading: !current,
    failed: Boolean(current?.failed),
  };
}

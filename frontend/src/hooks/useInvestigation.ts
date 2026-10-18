"use client";

import { useEffect, useState } from "react";

import { api, unwrap } from "@/lib/api";
import type { InvestigationView, SearchResponse } from "@/lib/types";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const MAX_POLLS = 20;

/**
 * Polls the background search for faster transfers that a route search
 * started. Stops when it finishes, after a bounded number of polls, or when
 * the rider starts another search (the old investigation is then cancelled
 * on the server, so it stops spending routing time).
 */
export function useInvestigation(response: SearchResponse | null): InvestigationView | null {
  const [latest, setLatest] = useState<InvestigationView | null>(null);
  const ref = response?.investigation ?? null;

  useEffect(() => {
    if (!ref) return;
    const id = ref.id;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    let finished = false;
    const controller = new AbortController();

    const poll = async () => {
      polls += 1;
      try {
        const next = unwrap(
          await api.GET("/api/v1/routes/investigations/{investigation_id}", {
            params: { path: { investigation_id: id } },
            signal: controller.signal,
          }),
        );
        setLatest(next);
        finished = TERMINAL.has(next.status);
      } catch {
        finished = controller.signal.aborted || polls >= 3;
      }
      if (!finished && polls < MAX_POLLS) {
        timer = setTimeout(poll, Math.max(ref.poll_after_ms, 500));
      }
    };
    timer = setTimeout(poll, ref.status === "completed" ? 0 : ref.poll_after_ms);

    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
      if (!finished) {
        void api.DELETE("/api/v1/routes/investigations/{investigation_id}", {
          params: { path: { investigation_id: id } },
        });
      }
    };
  }, [ref?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Only ever report the investigation of the current search.
  return latest && ref && latest.id === ref.id ? latest : null;
}

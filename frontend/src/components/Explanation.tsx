"use client";

import { useState } from "react";

import { ApiError, api, describeError, unwrap } from "@/lib/api";
import { providerName } from "@/lib/format";
import type { Explanation as ExplanationData } from "@/lib/types";

import styles from "./Explanation.module.css";

/** A short summary built from, and checked against, the route's facts. */
export function Explanation({ searchId, routeId }: { searchId: string; routeId: string }) {
  const [data, setData] = useState<ExplanationData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setData(
        unwrap(await api.POST("/api/v1/routes/explain", { body: { search_id: searchId, route_id: routeId } })),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 404
          ? "This result has expired. Search again to see an explanation."
          : describeError(caught),
      );
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <div className={styles.start}>
        <button type="button" className={styles.button} onClick={load} disabled={busy}>
          {busy ? "Summarizing…" : "Summarize this route"}
        </button>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <section className={styles.panel} aria-labelledby="explain-title" aria-live="polite">
      <h3 id="explain-title" className="label-caps">
        Summary
        {data.source === "model" && <span className={styles.tag}>AI · {providerName(data.provider)}</span>}
      </h3>
      <p className={styles.text}>{data.sentences.map((sentence) => sentence.text).join(" ")}</p>
      {data.notice && <p className={styles.note}>{data.notice}</p>}
    </section>
  );
}

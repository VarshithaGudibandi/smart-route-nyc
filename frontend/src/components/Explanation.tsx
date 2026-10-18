"use client";

import { useState } from "react";

import { ApiError, api, describeError, unwrap } from "@/lib/api";
import type { Explanation as ExplanationData } from "@/lib/types";

import styles from "./Explanation.module.css";

/** "Explain this route": sentences built from, and citing, the route's facts. */
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
        <button type="button" className={styles.button} onClick={load} disabled={busy} aria-describedby="explain-help">
          {busy ? "Explaining…" : "Explain this route"}
        </button>
        <span id="explain-help" className={styles.help}>
          Every step, barrier and unknown in plain sentences.
        </span>
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
        About this route
      </h3>
      <ul className={styles.sentences}>
        {data.sentences.map((sentence, index) => (
          <li key={index}>{sentence.text}</li>
        ))}
      </ul>
      {data.notice && <p className={styles.note}>{data.notice}</p>}
      <p className={styles.note}>
        {data.source === "model"
          ? `Worded by ${data.provider}, and checked sentence by sentence against the route data.`
          : "Built directly from the route data."}
      </p>
    </section>
  );
}

"use client";

import { useId, useState } from "react";

import { ApiError, api, describeError, unwrap } from "@/lib/api";

import styles from "./Feedback.module.css";

const LABELS = ["Very poor", "Poor", "OK", "Good", "Very good"];

/** Anonymous 1–5 star rating with an optional comment. */
export function Feedback({ searchId, routeId }: { searchId: string; routeId: string }) {
  const id = useId();
  const [stars, setStars] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!stars) return;
    setState("sending");
    try {
      unwrap(
        await api.POST("/api/v1/feedback", {
          body: { search_id: searchId, route_id: routeId, stars, comment: comment.trim() || null },
        }),
      );
      setState("sent");
    } catch (caught) {
      setState("error");
      setError(
        caught instanceof ApiError && caught.status === 404
          ? "This result has expired. Search again to rate it."
          : describeError(caught),
      );
    }
  }

  if (state === "sent") {
    return (
      <p className={styles.thanks} role="status">
        Thanks. Your rating was saved without any account or location.
      </p>
    );
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <fieldset className={styles.fieldset}>
        <legend className="label-caps">How was this route?</legend>
        <div className={styles.stars}>
          {LABELS.map((label, index) => {
            const value = index + 1;
            return (
              <label key={value} className={`${styles.star} ${stars && value <= stars ? styles.filled : ""}`}>
                <input
                  type="radio"
                  name={`${id}-stars`}
                  value={value}
                  checked={stars === value}
                  onChange={() => setStars(value)}
                  className="visually-hidden"
                />
                <span aria-hidden="true">★</span>
                <span className="visually-hidden">
                  {value} {value === 1 ? "star" : "stars"}, {label}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      {stars && (
        <>
          <label htmlFor={`${id}-comment`} className={styles.commentLabel}>
            Anything we should know? (optional, don’t include contact details)
          </label>
          <textarea
            id={`${id}-comment`}
            className={styles.comment}
            rows={2}
            maxLength={1000}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <button type="submit" className={styles.send} disabled={state === "sending"}>
            {state === "sending" ? "Sending…" : "Send rating"}
          </button>
        </>
      )}
      {state === "error" && error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

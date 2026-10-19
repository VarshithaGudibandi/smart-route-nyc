"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, api, unwrap } from "@/lib/api";
import { providerName } from "@/lib/format";
import { needsFromPreferences, preferencesFromNeeds, type Needs } from "@/lib/trip";
import type { InterpretResponse } from "@/lib/types";

import styles from "./NeedsDialog.module.css";

const FIELD_NAMES: Record<string, string> = {
  step_free_required: "Step-free travel",
  wheelchair_required: "Wheelchair access",
  max_walk_meters: "Walking limit",
  primary: "Main priority",
  prefer_fewer_transfers: "Fewer transfers",
  prefer_less_walking: "Less walking",
};

function describeValue(value: unknown): string {
  if (value === true) return "on";
  if (value === false) return "off";
  if (value === null || value === undefined) return "none";
  if (typeof value === "number") return `${value} m`;
  return String(value).toLowerCase().replace(/_/g, " ");
}

type Props = { open: boolean; needs: Needs; onClose: () => void; onApply: (needs: Needs) => void };

/**
 * Lets a rider describe their needs in their own words. The answer is only a
 * proposal: it is shown change by change and applied only if the rider
 * chooses to. Text can add requirements but never remove one.
 */
export function NeedsDialog({ open, needs, onClose, onApply }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");
  const [result, setResult] = useState<InterpretResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  async function interpret(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const response = unwrap(
        await api.POST("/api/v1/preferences/interpret", {
          body: { text, current: preferencesFromNeeds(needs) },
        }),
      );
      setResult(response);
    } catch (caught) {
      setResult(null);
      if (caught instanceof ApiError && caught.status === 503) {
        setError("Typed needs are turned off. Use the options on the page.");
      } else if (caught instanceof ApiError && caught.status === 502) {
        setError("We couldn’t read that. Try different words, or use the options on the page.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  function close() {
    setResult(null);
    setError(null);
    onClose();
  }

  const byRules = result?.source.provider === "baseline";

  return (
    <dialog ref={dialog} className={styles.dialog} onClose={close} aria-labelledby="needs-title">
      <form onSubmit={interpret} className={styles.body}>
        <h2 id="needs-title" className={styles.title}>
          Describe your needs
        </h2>
        <label htmlFor="needs-text" className={styles.label}>
          For example: “I use a wheelchair and would rather not change trains.”
        </label>
        <textarea
          id="needs-text"
          className={styles.text}
          rows={3}
          maxLength={500}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={busy || !text.trim()}>
            {busy ? "Reading…" : "Suggest settings"}
          </button>
          <button type="button" className={styles.secondary} onClick={close}>
            Cancel
          </button>
        </div>

        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}

        {result && (
          <section className={styles.result} aria-live="polite">
            <h3 className="label-caps">Suggested changes</h3>
            {result.changes.length === 0 ? (
              <p>Your settings already cover this. Routes are listed fastest first.</p>
            ) : (
              <ul className={styles.list}>
                {result.changes.map((change) => (
                  <li key={change.field}>
                    <strong>{FIELD_NAMES[change.field] ?? change.field}</strong>: {describeValue(change.previous)} →{" "}
                    {describeValue(change.proposed)}
                  </li>
                ))}
              </ul>
            )}
            {result.conflicts.map((conflict) => (
              <p key={conflict.field} className={styles.note}>
                <strong>{FIELD_NAMES[conflict.field] ?? conflict.field}</strong> kept as you set it. {conflict.message}
              </p>
            ))}
            {result.uncertain_fields.length > 0 && (
              <p className={styles.note}>
                Not sure about: {result.uncertain_fields.map((f) => FIELD_NAMES[f] ?? f).join(", ")}. Set these yourself if
                they matter.
              </p>
            )}
            {result.unsupported_requests.length > 0 && (
              <p className={styles.note}>Can’t plan for: {result.unsupported_requests.join(", ")}.</p>
            )}
            <p className={styles.source}>
              {byRules ? "Read with keyword rules." : `Read by ${providerName(result.source.provider)}.`}
            </p>
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.primary}
                onClick={() => {
                  onApply(needsFromPreferences(result.proposed));
                  setText("");
                  setResult(null);
                }}
              >
                Apply these settings
              </button>
            </div>
          </section>
        )}
      </form>
    </dialog>
  );
}

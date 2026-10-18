"use client";

import { useState } from "react";

import { isoToNewYorkLocal, newYorkLocalToIso } from "@/lib/format";
import type { Endpoint, Needs } from "@/lib/trip";

import { Logo } from "./Logo";
import { NeedsDialog } from "./NeedsDialog";
import { PlaceField } from "./PlaceField";
import styles from "./SearchPanel.module.css";

type Props = {
  from: Endpoint | null;
  to: Endpoint | null;
  departAt: string | null;
  needs: Needs;
  onSearch: (search: { from: Endpoint; to: Endpoint; departAt: string | null }) => void;
  onNeedsChange: (needs: Needs) => void;
};

const WALK_LIMITS = [
  { value: "", label: "Any walking" },
  { value: "400", label: "Walk up to 400 m" },
  { value: "800", label: "Walk up to 800 m" },
  { value: "1200", label: "Walk up to 1.2 km" },
];

export function SearchPanel({ from, to, departAt, needs, onSearch, onNeedsChange }: Props) {
  const [origin, setOrigin] = useState<Endpoint | null>(from);
  const [destination, setDestination] = useState<Endpoint | null>(to);
  const [when, setWhen] = useState(departAt ? isoToNewYorkLocal(departAt) : "");
  const [problem, setProblem] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  // Follow trips chosen elsewhere (the back button, a shared link).
  const [seen, setSeen] = useState({ from, to });
  if (seen.from !== from || seen.to !== to) {
    setSeen({ from, to });
    setOrigin(from);
    setDestination(to);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!origin || !destination) {
      setProblem(!origin ? "Choose where you are starting from." : "Choose where you are going.");
      return;
    }
    setProblem(null);
    onSearch({ from: origin, to: destination, departAt: when ? newYorkLocalToIso(when) : null });
  }

  const toggle = (key: keyof Needs, value: boolean) => {
    const next = { ...needs, [key]: value };
    if (key === "wheelchair" && value) next.stepFree = true;
    if (key === "stepFree" && !value) next.wheelchair = false;
    onNeedsChange(next);
  };

  return (
    <header className={styles.header}>
      <div className={styles.top}>
        <Logo />
        <form className={styles.form} onSubmit={submit} aria-label="Plan a trip" noValidate>
          <PlaceField label="From" value={origin} onChange={setOrigin} allowCurrentLocation />
          <button
            type="button"
            className={styles.swap}
            aria-label="Swap start and destination"
            onClick={() => {
              setOrigin(destination);
              setDestination(origin);
            }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 4v16M3 8l4-4 4 4M17 20V4M21 16l-4 4-4-4" /></svg>
          </button>
          <PlaceField label="To" value={destination} onChange={setDestination} />
          <div className={styles.when}>
            <label htmlFor="depart-at" className="label-caps">
              Leave
            </label>
            <input
              id="depart-at"
              type="datetime-local"
              className={styles.time}
              value={when}
              onChange={(event) => setWhen(event.target.value)}
              aria-describedby="depart-help"
            />
            <span id="depart-help" className={styles.help}>
              {when ? "New York time" : "Leave now if empty"}
            </span>
          </div>
          <button type="submit" className={styles.search}>
            Search
          </button>
        </form>
      </div>
      {problem && (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      )}

      <div className={styles.needs} role="group" aria-label="Your needs">
        <NeedChip pressed={needs.stepFree} onPress={(v) => toggle("stepFree", v)}>
          Step-free
        </NeedChip>
        <NeedChip pressed={needs.wheelchair} onPress={(v) => toggle("wheelchair", v)}>
          Wheelchair
        </NeedChip>
        <NeedChip pressed={needs.lessWalking} onPress={(v) => toggle("lessWalking", v)}>
          Less walking
        </NeedChip>
        <NeedChip pressed={needs.fewerTransfers} onPress={(v) => toggle("fewerTransfers", v)}>
          Fewer transfers
        </NeedChip>
        <label className="visually-hidden" htmlFor="walk-limit">
          Walking limit
        </label>
        <select
          id="walk-limit"
          className={styles.select}
          value={needs.maxWalk ? String(needs.maxWalk) : ""}
          onChange={(event) => onNeedsChange({ ...needs, maxWalk: event.target.value ? Number(event.target.value) : null })}
        >
          {WALK_LIMITS.map((limit) => (
            <option key={limit.value} value={limit.value}>
              {limit.label}
            </option>
          ))}
        </select>
        <button type="button" className={styles.describe} onClick={() => setDialogOpen(true)}>
          Describe your needs…
        </button>
      </div>

      <NeedsDialog
        open={dialogOpen}
        needs={needs}
        onClose={() => setDialogOpen(false)}
        onApply={(next) => {
          onNeedsChange(next);
          setDialogOpen(false);
        }}
      />
    </header>
  );
}

function NeedChip({ pressed, onPress, children }: { pressed: boolean; onPress: (value: boolean) => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-pressed={pressed} className={`${styles.chip} ${pressed ? styles.on : ""}`} onClick={() => onPress(!pressed)}>
      {pressed && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fccc0a" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7" /></svg>
      )}
      {children}
    </button>
  );
}

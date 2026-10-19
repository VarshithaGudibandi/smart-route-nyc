"use client";

import { useId, useState } from "react";

import { usePlaces } from "@/hooks/usePlaces";
import type { Endpoint } from "@/lib/trip";
import type { PlaceResult } from "@/lib/types";

import styles from "./PlaceField.module.css";

type Props = {
  label: string;
  value: Endpoint | null;
  onChange: (value: Endpoint | null) => void;
};

export const CURRENT_LOCATION = "Your location";

type LocationState = "idle" | "locating" | "denied" | "unavailable";

/**
 * A place picker following the WAI-ARIA combobox pattern (listbox popup).
 * "Your location" is always the first option; the position is used for this
 * search only and never stored.
 */
export function PlaceField({ label, value, onChange }: Props) {
  const id = useId();
  const [text, setText] = useState(value?.label ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [location, setLocation] = useState<LocationState>("idle");
  const { data, loading, failed } = usePlaces(open && text !== CURRENT_LOCATION ? text : "");
  const results = data?.results ?? [];
  const listId = `${id}-list`;
  const searching = text.trim().length >= 2 && text !== CURRENT_LOCATION;
  // Option 0 is "Your location"; places follow.
  const count = 1 + (searching ? results.length : 0);

  // Keep the visible text in step with a value chosen elsewhere (URL, swap).
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    setText(value?.label ?? "");
  }

  function choose(place: PlaceResult) {
    const label = place.kind === "station" ? place.name : [place.name, place.detail].filter(Boolean).join(", ");
    onChange({ lat: place.lat, lon: place.lon, label });
    setText(label);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, count - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter" && open && active === 0) {
      event.preventDefault();
      locate();
    } else if (event.key === "Enter" && open && active > 0 && results[active - 1]) {
      event.preventDefault();
      choose(results[active - 1]);
    } else if (event.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  }

  function locate() {
    setOpen(false);
    setActive(-1);
    if (!("geolocation" in navigator)) {
      setLocation("unavailable");
      return;
    }
    setLocation("locating");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocation("idle");
        onChange({ lat: position.coords.latitude, lon: position.coords.longitude, label: CURRENT_LOCATION });
      },
      (error) => setLocation(error.code === error.PERMISSION_DENIED ? "denied" : "unavailable"),
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    );
  }

  const showList = open;

  return (
    <div className={styles.field}>
      <label htmlFor={id} className={`label-caps ${styles.label}`}>
        {label}
      </label>
      <div className={styles.row}>
        <input
          id={id}
          className={styles.input}
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && active >= 0 ? `${listId}-${active}` : undefined}
          autoComplete="off"
          placeholder="Station, address or place"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setOpen(true);
            setActive(-1);
            if (value) onChange(null);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
        />
      </div>

      {location === "locating" && <p className={styles.hint} role="status">Finding your location…</p>}
      {location === "denied" && (
        <p className={styles.hint} role="status">
          Location access is off. Search for a place instead.
        </p>
      )}
      {location === "unavailable" && (
        <p className={styles.hint} role="status">
          Location unavailable. Search for a place instead.
        </p>
      )}

      {showList && (
        <div className={styles.popup}>
          <ul id={listId} role="listbox" aria-label={`${label} suggestions`} className={styles.list}>
            <li
              id={`${listId}-0`}
              role="option"
              aria-selected={active === 0}
              className={`${styles.option} ${styles.here} ${active === 0 ? styles.active : ""}`}
              onMouseDown={(event) => {
                event.preventDefault();
                locate();
              }}
            >
              <span className={styles.kind} aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></svg>
              </span>
              <span className={styles.name}>{CURRENT_LOCATION}</span>
            </li>
            {searching && results.map((place, i) => {
              const index = i + 1;
              return (
              <li
                key={place.id + index}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                className={`${styles.option} ${index === active ? styles.active : ""}`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(place);
                }}
              >
                <span className={styles.kind} aria-hidden="true">
                  {place.kind === "station" ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="3" width="14" height="14" rx="3" /><path d="M8 21l2-4M16 21l-2-4M9 13h.01M15 13h.01M5 9h14" /></svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z" /><circle cx="12" cy="10" r="2.5" /></svg>
                  )}
                </span>
                <span>
                  <span className={styles.name}>{place.name}</span>
                  {place.detail && <span className={styles.detail}>{place.detail}</span>}
                </span>
              </li>
              );
            })}
          </ul>
          {searching && !loading && results.length === 0 && !failed && <p className={styles.empty}>No matching places.</p>}
          {searching && failed && <p className={styles.empty}>Search is unavailable right now.</p>}
          {searching &&
            data?.notices.map((n) => (
              <p key={n.code} className={styles.empty}>
                {n.message}
              </p>
            ))}
          {searching && data?.attribution && <p className={styles.attribution}>Places {data.attribution}</p>}
        </div>
      )}
    </div>
  );
}

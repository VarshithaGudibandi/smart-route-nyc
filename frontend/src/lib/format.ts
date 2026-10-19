/** Times are always shown in New York time, whatever the device's zone. */
const ZONE = "America/New_York";

const clockFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: ZONE,
  hour: "numeric",
  minute: "2-digit",
});
const dayFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: ZONE,
  weekday: "short",
  hour: "numeric",
  minute: "2-digit",
});

/** "8:34" (no AM/PM, as on a departure board). */
export function clock(iso: string): string {
  return clockFormat.format(new Date(iso)).replace(/\s?[AP]M$/, "");
}

/** "8:34 AM". */
export function clockWithPeriod(iso: string): string {
  return clockFormat.format(new Date(iso));
}

/** "Mon 8:30 AM". */
export function dayAndTime(iso: string): string {
  return dayFormat.format(new Date(iso));
}

export function minutes(seconds: number): number {
  return Math.round(seconds / 60);
}

export function duration(seconds: number): string {
  const total = minutes(seconds);
  if (total < 60) return `${total} min`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function distance(meters: number): string {
  if (meters < 950) return `${Math.round(meters / 10) * 10 || Math.round(meters)} m`;
  return `${(Math.round(meters / 100) / 10).toFixed(1)} km`;
}

/** OTP names coordinate endpoints "Origin" and "Destination". */
export function placeName(name: string | null | undefined, fallback: string): string {
  if (name === "Origin") return "your starting point";
  if (name === "Destination") return "your destination";
  return name || fallback;
}

/**
 * Interpret a `datetime-local` value ("2026-10-19T08:30") as New York time,
 * whatever the device's zone, and return it as an ISO instant.
 */
export function newYorkLocalToIso(value: string): string {
  const [date, time] = value.split("T");
  const [y, mo, d] = date.split("-").map(Number);
  const [h, mi] = time.split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const offset = (instant: number) => {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", {
        timeZone: ZONE,
        hourCycle: "h23",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
        .formatToParts(new Date(instant))
        .map((p) => [p.type, p.value]),
    );
    const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
    return asUtc - instant;
  };
  // Two passes settle the offset across a daylight-saving change.
  let instant = guess - offset(guess);
  instant = guess - offset(instant);
  return new Date(instant).toISOString();
}

/** The `datetime-local` value for an instant, in New York time. */
export function isoToNewYorkLocal(iso: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: ZONE,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    })
      .formatToParts(new Date(iso))
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

const PROVIDER_NAMES: Record<string, string> = { anthropic: "Claude" };

/** The model's name as riders know it, rather than the API's provider key. */
export function providerName(provider: string | null | undefined): string {
  return PROVIDER_NAMES[provider ?? ""] ?? provider ?? "a language model";
}

/** Replace OTP's names for coordinate endpoints inside a longer text. */
export function friendlyText(text: string): string {
  return text.replace(/\bOrigin\b/g, "your starting point").replace(/\bDestination\b/g, "your destination");
}

import type { AccessibilityStatus } from "@/lib/types";

import styles from "./StatusMark.module.css";

/**
 * The three accessibility states. Each has its own shape and symbol as well
 * as a color, so the state is never conveyed by color alone.
 */
const SYMBOL: Record<AccessibilityStatus, string> = { meets: "✓", violates: "!", unknown: "?" };
export const STATUS_WORD: Record<AccessibilityStatus, string> = {
  meets: "Confirmed",
  violates: "Barrier",
  unknown: "Not confirmed",
};

export function StatusMark({ status, size = "regular" }: { status: AccessibilityStatus; size?: "regular" | "small" }) {
  return (
    <span className={`${styles.mark} ${styles[status]} ${size === "small" ? styles.small : ""}`} aria-hidden="true">
      {SYMBOL[status]}
    </span>
  );
}

/** A labelled status chip, for cards ("✓ Step-free confirmed"). */
export function StatusChip({ status, subject }: { status: AccessibilityStatus; subject: string }) {
  const text = {
    meets: `${subject} confirmed`,
    violates: `${subject}: barrier found`,
    unknown: `${subject}: not confirmed`,
  }[status];
  return (
    <span className={`${styles.chip} ${styles[`chip_${status}`]}`}>
      <StatusMark status={status} size="small" />
      {text}
    </span>
  );
}

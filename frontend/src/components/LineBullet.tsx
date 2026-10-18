import { lineName, lineStyle } from "@/lib/lines";

import styles from "./LineBullet.module.css";

type Props = { routeId: string | null | undefined; name: string | null | undefined; mode: string; size?: "regular" | "small" };

export function LineBullet({ routeId, name, mode, size = "regular" }: Props) {
  const style = lineStyle(routeId, name);
  return (
    <span
      className={`${styles.bullet} ${style.shape === "pill" ? styles.pill : styles.circle} ${size === "small" ? styles.small : ""}`}
      style={{ background: style.background, color: style.color }}
      role="img"
      aria-label={lineName(routeId, name, mode)}
    >
      {style.label}
    </span>
  );
}

import styles from "./Logo.module.css";

export function Logo({ size = "regular" }: { size?: "regular" | "small" }) {
  return (
    <span className={`${styles.logo} ${size === "small" ? styles.small : ""}`}>
      SmartRoute<span className={styles.nyc}>NYC</span>
    </span>
  );
}

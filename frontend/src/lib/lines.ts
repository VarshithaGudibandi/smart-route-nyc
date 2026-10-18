/**
 * Line colors for route bullets. Subway colors follow the MTA's published
 * line colors; PATH colors follow its route colors. Text is black or white,
 * whichever keeps at least 4.5:1 contrast on that color.
 */
export type LineStyle = { background: string; color: string; label: string; shape: "circle" | "pill" };

const SUBWAY: Record<string, string> = {
  "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
  "4": "#00933C", "5": "#00933C", "6": "#00933C", "6X": "#00933C",
  "7": "#B933AD", "7X": "#B933AD",
  A: "#0039A6", C: "#0039A6", E: "#0039A6",
  B: "#FF6319", D: "#FF6319", F: "#FF6319", FX: "#FF6319", M: "#FF6319",
  G: "#6CBE45",
  J: "#996633", Z: "#996633",
  L: "#A7A9AC",
  N: "#FCCC0A", Q: "#FCCC0A", R: "#FCCC0A", W: "#FCCC0A",
  S: "#808183", GS: "#808183", FS: "#808183", H: "#808183",
  SI: "#1D2E86",
}; // prettier-ignore

const DARK_TEXT = new Set(["N", "Q", "R", "W", "L", "G"]);

const PATH: Record<string, { background: string; color: string }> = {
  RED: { background: "#D93A30", color: "#FFFFFF" },
  GRN: { background: "#65C100", color: "#111111" },
  YEL: { background: "#FF9900", color: "#111111" },
  BLU: { background: "#2F6FD6", color: "#FFFFFF" },
};

export function lineStyle(routeId: string | null | undefined, name: string | null | undefined): LineStyle {
  const [feed] = (routeId ?? "").split(":");
  const short = name ?? "";
  if (feed === "MTASBWY") {
    const bullet = short.replace(/X$/, "");
    return {
      background: SUBWAY[short] ?? SUBWAY[bullet] ?? "#808183",
      color: DARK_TEXT.has(bullet) ? "#111111" : "#FFFFFF",
      label: bullet,
      shape: "circle",
    };
  }
  if (feed === "PATH") {
    const path = PATH[short] ?? { background: "#4A4A4A", color: "#FFFFFF" };
    return { background: path.background, color: path.color, label: "PATH", shape: "pill" };
  }
  return { background: "#0062CF", color: "#FFFFFF", label: short || "Bus", shape: "pill" };
}

/** A spoken name for a line, for screen readers ("R train", "PATH train", "Q32 bus"). */
export function lineName(routeId: string | null | undefined, name: string | null | undefined, mode: string): string {
  const [feed] = (routeId ?? "").split(":");
  if (feed === "PATH") return "PATH train";
  if (mode === "BUS") return `${name ?? ""} bus`.trim();
  return `${(name ?? "").replace(/X$/, "")} train`.trim();
}

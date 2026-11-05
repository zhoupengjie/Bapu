// Appearance: follow the system, or always light / dark. Remembered in this browser profile.
import { useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";
export const THEMES: { id: Theme; label: string }[] = [
  { id: "system", label: "跟随系统" }, { id: "light", label: "浅色" }, { id: "dark", label: "深色" },
];
const KEY = "bapu.theme";
const EVENT = "bapu-theme";

export function getTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: Theme = getTheme()) {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

export function setTheme(t: Theme) {
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* storage unavailable: this session only */
  }
  applyTheme(t);
  window.dispatchEvent(new Event(EVENT));
}

/** Changes whenever the colours change (a choice here, or the system switching), for canvases to redraw. */
export function useThemeVersion(): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    const bump = () => setV((x) => x + 1);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    window.addEventListener(EVENT, bump);
    mq.addEventListener("change", bump);
    return () => {
      window.removeEventListener(EVENT, bump);
      mq.removeEventListener("change", bump);
    };
  }, []);
  return v;
}

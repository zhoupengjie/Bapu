// Interface language: 简体中文 (the source text), English or 日本語.
//
// Every visible string is written in Simplified Chinese and passed through t(); the other
// languages look it up in web/src/locales/*.ts (one file per page, merged here). Placeholders
// are {name}. Text that comes from the service or the desktop shell (step titles, device
// labels, error messages) goes through t() too: known strings are translated, others shown as is.
// The first choice follows the system language; the sidebar button changes it.
import { useEffect, useState } from "react";
import * as all from "./locales";

export type Lang = "zh-CN" | "en" | "ja";
export const LANGS: { id: Lang; label: string }[] = [
  { id: "zh-CN", label: "简体中文" }, { id: "en", label: "English" }, { id: "ja", label: "日本語" },
];
type Dict = Record<string, string>;
const KEY = "bapu.lang";
const EVENT = "bapu-lang";

const DICTS: Record<Exclude<Lang, "zh-CN">, Dict> = { en: {}, ja: {} };
for (const part of Object.values(all) as { en?: Dict; ja?: Dict }[]) {
  Object.assign(DICTS.en, part.en ?? {});
  Object.assign(DICTS.ja, part.ja ?? {});
}

/** The system's language: any Chinese → Simplified Chinese, Japanese → Japanese, anything else → English. */
export function systemLang(): Lang {
  for (const l of navigator.languages?.length ? navigator.languages : [navigator.language]) {
    const s = (l || "").toLowerCase();
    if (s.startsWith("zh")) return "zh-CN";
    if (s.startsWith("ja")) return "ja";
    if (s) return "en";
  }
  return "zh-CN";
}

function stored(): Lang | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "zh-CN" || v === "en" || v === "ja" ? v : null;
  } catch {
    return null;
  }
}

let current: Lang = stored() ?? systemLang();

export const getLang = () => current;

export function setLang(l: Lang) {
  current = l;
  try {
    localStorage.setItem(KEY, l);
  } catch {
    /* storage unavailable: this session only */
  }
  document.documentElement.lang = l;
  window.dispatchEvent(new Event(EVENT));
}

/** Translate a Simplified Chinese source string; {name} placeholders are filled from `vars`. */
export function t(text: string, vars?: Record<string, string | number>): string {
  const out = current === "zh-CN" ? text : lookup(DICTS[current], text);
  return vars ? out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : out;
}

/** Exact text; else line by line; else "<known message>：<detail>" with the detail kept as is
 *  (service errors often end in a path or the underlying error). */
function lookup(d: Dict, text: string): string {
  if (text in d) return d[text];
  if (text.includes("\n")) return text.split("\n").map((l) => lookup(d, l)).join("\n");
  const i = text.indexOf("：");
  if (i > 0 && text.slice(0, i) in d) return `${d[text.slice(0, i)]}${current === "en" ? ": " : "："}${text.slice(i + 1)}`;
  return text;
}

/** Re-render on a language change (call once near the root; components then just use t()). */
export function useLang(): Lang {
  const [l, setL] = useState(current);
  useEffect(() => {
    const on = () => setL(current);
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return l;
}

document.documentElement.lang = current;

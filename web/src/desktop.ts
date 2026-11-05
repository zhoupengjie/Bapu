// Inside the Bapu desktop window (Tauri) the page can use the system dialogs and
// open links in the default browser directly; in a plain browser these fall back
// to the local service (/api/dialog) and normal links.

import { t as tr } from "./i18n";

type Filter = { name: string; extensions: string[] };
type TauriGlobal = {
  dialog: {
    open(o: { title?: string; directory?: boolean; multiple?: boolean; defaultPath?: string; filters?: Filter[] }): Promise<string | string[] | null>;
  };
  opener: { openUrl(url: string): Promise<void>; revealItemInDir(path: string): Promise<void> };
  event: { listen<T>(name: string, cb: (e: { payload: T }) => void): Promise<() => void> };
  core: { invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> };
};

const tauri = (): TauriGlobal | null => (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__ ?? null;
export const inDesktop = () => tauri() !== null;

export type PickKind = "audio" | "lyrics" | "project" | "folder";
const KINDS: Record<PickKind, { title: string; filters?: Filter[] }> = {
  audio: { title: "选择原曲", filters: [
    { name: "音频或视频", extensions: ["wav", "flac", "mp3", "m4a", "aac", "ogg", "opus", "mp4", "mov", "mkv", "webm"] },
    { name: "所有文件", extensions: ["*"] }] },
  lyrics: { title: "选择参考歌词", filters: [{ name: "字幕或歌词", extensions: ["ass", "ssa", "lrc", "txt"] }, { name: "所有文件", extensions: ["*"] }] },
  project: { title: "打开 Bapu 工程", filters: [{ name: "Bapu 工程", extensions: ["bapu"] }] },
  folder: { title: "选择工程文件夹" },
};

/** The system dialog of the desktop window; undefined when not in the desktop app. */
export async function desktopPick(kind: PickKind, initial?: string): Promise<string | null | undefined> {
  const t = tauri();
  if (!t) return undefined;
  const k = KINDS[kind];
  const r = await t.dialog.open({ title: tr(k.title), directory: kind === "folder", multiple: false, defaultPath: initial,
    filters: k.filters?.map((f) => ({ ...f, name: tr(f.name) })) });
  return Array.isArray(r) ? r[0] ?? null : r;
}

/** Files dropped onto the desktop window (the window takes the drop, so the page gets real paths). */
export function onDesktopDrop(cb: (paths: string[]) => void): () => void {
  const t = tauri();
  if (!t?.event) return () => {};
  let stop: (() => void) | null = null;
  let gone = false;
  t.event.listen<{ paths: string[] }>("tauri://drag-drop", (e) => cb(e.payload.paths ?? []))
    .then((u) => (gone ? u() : (stop = u)))
    .catch(() => {});
  return () => {
    gone = true;
    stop?.();
  };
}

export function revealInFolder(path: string) {
  tauri()?.opener.revealItemInDir(path).catch(() => {});
}

/** In the desktop window, links to other sites open in the default browser. */
export function routeExternalLinks() {
  const t = tauri();
  if (!t) return;
  document.addEventListener("click", (e) => {
    const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a) return;
    const url = new URL(a.href, window.location.href);
    if (url.origin !== window.location.origin && /^https?:$/.test(url.protocol)) {
      e.preventDefault();
      t.opener.openUrl(url.href).catch(() => {});
    }
  });
}

/** The Python service the desktop window runs (or why it is not running yet). */
export type Service = {
  state: "no_runtime" | "needs_sync" | "installing" | "starting" | "ready" | "failed";
  error: string | null;
  port: number;
  portable: boolean;
  log: string;
  setup_running: boolean;
};

export const invoke = <T,>(cmd: string, args?: Record<string, unknown>) => {
  const t = tauri();
  return t ? t.core.invoke<T>(cmd, args) : Promise.reject(new Error("不在 Bapu 桌面窗口里"));
};

/** Subscribe to a desktop-shell event; returns an unsubscribe function. */
export function onDesktopEvent<T>(name: string, cb: (payload: T) => void): () => void {
  const t = tauri();
  if (!t?.event) return () => {};
  let stop: (() => void) | null = null;
  let gone = false;
  t.event.listen<T>(name, (e) => cb(e.payload)).then((u) => (gone ? u() : (stop = u))).catch(() => {});
  return () => {
    gone = true;
    stop?.();
  };
}

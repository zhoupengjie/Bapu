// Everything the 环境 step tracks: the Python runtime (installed by the desktop shell),
// the models (one download queue in the Python service) and Bapu's own updates.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { inDesktop, invoke, onDesktopEvent, type Service } from "./desktop";

export type DlItem = {
  id: string; stage: string; name: string; status: "ready" | "missing" | "corrupt" | "builtin";
  size: number; done: number; license: { name: string; url: string; commercial: string };
};
export type Downloads = {
  state: "idle" | "downloading" | "paused" | "failed"; current: string | null; queue: string[];
  error: string | null; speed: number; threads: number; items: DlItem[];
};
export type UpdateStatus = {
  current: string;
  state: "idle" | "checking" | "latest" | "available" | "downloading" | "ready" | "error";
  latest: { version: string; date?: string; notes?: string; zip: { size: number } } | null;
  error: string | null; done: number; total: number | null; can_install: boolean; staged: boolean;
};
type Variant = { id: string; label: string; detail: string; size_gb: number; experimental: boolean; available: boolean; note: string; recommended: boolean };
export type SetupInfo = {
  home: string; writable: boolean; nvidia: { name: string; driver: number | null }[]; adapters: string[]; amd: boolean; amd_supported?: boolean; apple?: string | null;
  variants: Variant[]; recommended: string;
  sources: Record<"official" | "china", { label: string; detail: string }>;
  settings: { source?: string; measured?: Probe } | null;
  previous: { variant: string; label: string; source?: string; device: { name: string; kind: string } } | null;
  models: { id: string; stage: string; name: string; size: number; builtin: boolean; license?: { name: string; url: string; commercial: string } }[];
  threads: number; partial?: boolean;
};
type Probe = { official: number | null; china: number | null; best: "official" | "china" };
export type Environment = { device: { kind: string; name: string }; portable: boolean };
export type SourceChoice = "auto" | "official" | "china";

/** What the runtime row shows. */
export type Runtime =
  | { state: "ready" }
  | { state: "missing"; partial: boolean }
  | { state: "installing"; index: number; total: number; title: string; sync: boolean }
  | { state: "starting" }
  | { state: "failed"; error: string };

const CONTINUE = "bapu.continueDownloads"; // after the runtime: go on with the models

function flag(on?: boolean): boolean {
  try {
    if (on === undefined) return sessionStorage.getItem(CONTINUE) === "1";
    if (on) sessionStorage.setItem(CONTINUE, "1");
    else sessionStorage.removeItem(CONTINUE);
  } catch {
    /* storage unavailable */
  }
  return !!on;
}

export function useEnvState(service: Service | null) {
  const apiUp = !service || service.state === "ready";
  const portable = !!service?.portable;
  const [setup, setSetup] = useState<SetupInfo | null>(null);
  const [step, setStep] = useState<{ index: number; total: number; title: string } | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [downloads, setDownloads] = useState<Downloads | null>(null);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  const [environment, setEnvironment] = useState<Environment | null>(null);
  const [source, setSourceState] = useState<SourceChoice>("auto");
  const [threads, setThreadsState] = useState(8);
  const [error, setError] = useState<string | null>(null);
  const synced = useRef(false);

  const loadSetup = useCallback(() => {
    if (!portable) return;
    invoke<SetupInfo>("setup_info").then((i) => {
      setSetup(i);
      if (!apiUp) {
        setThreadsState(i.threads ?? 8);
        const s = i.settings?.source;
        if (s === "auto" || s === "official" || s === "china") setSourceState(s);
      }
    }).catch(() => {});
  }, [portable, apiUp]);
  useEffect(loadSetup, [loadSetup]);

  useEffect(() => {
    if (!inDesktop()) return;
    const offs = [
      onDesktopEvent<{ index: number; total: number; title: string }>("setup-step", setStep),
      onDesktopEvent("setup-done", () => { setStep(null); loadSetup(); }),
      onDesktopEvent("setup-paused", () => { setStep(null); loadSetup(); }),
      onDesktopEvent<{ message: string }>("setup-error", (e) => { setStep(null); setSetupError(e.message); flag(false); loadSetup(); }),
    ];
    return () => offs.forEach((f) => f());
  }, [loadSetup]);

  const refresh = useCallback(async () => {
    if (!apiUp) return;
    const [d, u] = await Promise.all([
      api.get<Downloads>("/downloads").catch(() => null),
      api.get<UpdateStatus>("/update").catch(() => null),
    ]);
    if (d) {
      setDownloads(d);
      setThreadsState(d.threads);
    }
    if (u) setUpdate(u);
  }, [apiUp]);

  useEffect(() => {
    if (!apiUp) return;
    refresh();
    api.get<Environment>("/system/environment").then(setEnvironment).catch(() => {});
    api.get<{ choice: SourceChoice }>("/system/sources").then((s) => setSourceState(s.choice)).catch(() => {});
    // once a day, quietly: is there a newer Bapu?
    let last = 0;
    try {
      last = Number(localStorage.getItem("bapu.updateCheckedAt") || 0);
    } catch {
      /* storage unavailable */
    }
    if (Date.now() - last > 24 * 3600 * 1000) {
      api.post<UpdateStatus>("/update/check").then((u) => {
        setUpdate(u);
        try {
          localStorage.setItem("bapu.updateCheckedAt", String(Date.now()));
        } catch {
          /* ignore */
        }
      }).catch(() => {});
    }
  }, [apiUp, refresh]);

  // the runtime row
  let runtime: Runtime;
  if (!service || !portable) runtime = { state: "ready" };
  else if (service.state === "installing" || service.setup_running || service.state === "needs_sync") {
    runtime = { state: "installing", index: step?.index ?? 0, total: step?.total ?? 4, title: step?.title ?? "准备中", sync: service.state === "needs_sync" || !!setup?.previous };
  } else if (service.state === "starting") runtime = { state: "starting" };
  else if (service.state === "ready") runtime = { state: "ready" };
  else if (service.state === "failed") runtime = { state: "failed", error: service.error ?? "启动失败" };
  else runtime = setupError ? { state: "failed", error: setupError } : { state: "missing", partial: !!setup?.partial };

  // while uv installs the runtime there is no byte count: show the machine's download rate
  const [netSpeed, setNetSpeed] = useState(0);
  const installing = runtime.state === "installing";
  useEffect(() => {
    if (!installing || !inDesktop()) {
      setNetSpeed(0);
      return;
    }
    const t = setInterval(() => {
      invoke<{ net: { down: number } | null }>("sys_load").then((l) => setNetSpeed(l.net?.down ?? 0)).catch(() => {});
    }, 1000);
    return () => clearInterval(t);
  }, [installing]);

  const modelsBusy = downloads?.state === "downloading";
  const updateBusy = update?.state === "downloading";
  const busy = runtime.state === "installing" || runtime.state === "starting" || modelsBusy || updateBusy;
  const missingModels = downloads
    ? downloads.items.filter((i) => i.status === "missing" || i.status === "corrupt").length
    : setup?.models.filter((m) => !m.builtin).length ?? 0;
  const runtimeMissing = runtime.state !== "ready";
  const ready = !runtimeMissing && downloads != null && missingModels === 0;
  const updateAvailable = update?.state === "available" && update.can_install ? update.latest?.version ?? null : null;

  // keep it live while something is moving
  useEffect(() => {
    if (!apiUp || !(modelsBusy || updateBusy)) return;
    const t = setInterval(refresh, 1000);
    return () => clearInterval(t);
  }, [apiUp, modelsBusy, updateBusy, refresh]);

  async function installRuntime(variant?: string) {
    if (!setup) return;
    setSetupError(null);
    flag(true); // once the service is up, carry on with the models
    const v = variant ?? setup.previous?.variant ?? setup.recommended;
    let resolved: "official" | "china" = source === "auto" ? "official" : source;
    let measured: Probe | Record<string, never> = setup.settings?.measured ?? {};
    if (source === "auto") {
      const p = await invoke<Probe>("setup_probe").catch(() => null);
      if (p) {
        resolved = p.best;
        measured = p;
      }
    }
    await invoke("setup_install", { variant: v, source: resolved, choice: source, measured })
      .catch((e) => setSetupError(String(e)));
  }

  // updated, and the new version needs other packages: bring the runtime up to date right away
  useEffect(() => {
    if (service?.state === "needs_sync" && setup && !synced.current && !service.setup_running) {
      synced.current = true;
      installRuntime(setup.previous?.variant);
    }
  }, [service?.state, setup]); // eslint-disable-line react-hooks/exhaustive-deps

  // the runtime is up: carry on with the models if the user had pressed 下载
  useEffect(() => {
    if (apiUp && flag()) {
      flag(false);
      api.post<Downloads>("/downloads/start", {}).then(setDownloads).catch((e) => setError((e as Error).message));
    }
  }, [apiUp]);

  /** 下载 / 继续下载 / 更新: whatever is missing, in order. */
  async function start() {
    setError(null);
    try {
      if (runtimeMissing && portable) {
        await installRuntime();
        return;
      }
      if (missingModels) setDownloads(await api.post<Downloads>("/downloads/start", {}));
      if (updateAvailable) setUpdate(await api.post<UpdateStatus>("/update/download"));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function pause() {
    setError(null);
    try {
      if (runtime.state === "installing") {
        flag(false);
        await invoke("setup_pause");
      } else if (modelsBusy) setDownloads(await api.post<Downloads>("/downloads/pause"));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function setThreads(n: number) {
    const v = Math.max(1, Math.min(16, n));
    setThreadsState(v);
    if (apiUp) await api.put("/system/sources", { threads: v }).catch(() => {});
    else await invoke("setup_settings", { changes: { threads: v } }).catch(() => {});
  }

  async function setSource(c: SourceChoice) {
    setSourceState(c);
    if (apiUp) await api.put("/system/sources", { choice: c }).catch(() => {});
    else await invoke("setup_settings", { changes: { source: c } }).catch(() => {});
  }

  return {
    apiUp, portable, setup, runtime, netSpeed, downloads, update, environment, source, threads, error,
    busy, ready, missingModels, runtimeMissing, updateAvailable, paused: downloads?.state === "paused",
    refresh, start, pause, setThreads, setSource, installRuntime,
  };
}

export type EnvState = ReturnType<typeof useEnvState>;

/** The step names users see, by pipeline stage. */
export const STAGE_LABEL: Record<string, string> = {
  // pipeline steps, and the model entries of the registry (lead.* are the two models of the lead step)
  separate: "人声分离", lead: "提取主唱", splits: "合并音符", "lead.karaoke": "主唱提取", "lead.dereverb": "去混响", tempo: "测速度",
  transcribe: "转音符", dictate: "听写歌词", align: "对齐时间", lyrics: "整理歌词", assign: "填字到音符",
};

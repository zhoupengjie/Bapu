import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, type JobDetail, type JobSummary } from "./api";
import { inDesktop, invoke, onDesktopEvent, desktopPick, type Service } from "./desktop";
import { useEnvState, type EnvState } from "./env";
import { Icon } from "./icons";
import { THEMES, getTheme, setTheme, type Theme } from "./theme";
import { LANGS, getLang, setLang, t, type Lang } from "./i18n";
import LoadPanel from "./components/LoadPanel";
import EnvPage from "./pages/EnvPage";
import ImportPage from "./pages/ImportPage";
import RunPage from "./pages/RunPage";
import CheckPage from "./pages/CheckPage";
import ExportPage from "./pages/ExportPage";

/** One song at a time: the steps of the flow are the routes (#/env … #/export). */
export type Step = "env" | "import" | "run" | "check" | "export";
const STEP_ORDER: Step[] = ["env", "import", "run", "check", "export"];
const TITLE: Record<Step, string> = { env: "环境", import: "导入", run: "自动处理", check: "检查", export: "导出" }; // source text, shown through t()

export const go = (step: Step) => {
  window.location.hash = `/${step}`;
};

function useStep(): Step | null {
  const read = () => {
    const s = window.location.hash.replace(/^#\/?/, "").split("/")[0];
    return (STEP_ORDER as string[]).includes(s) ? (s as Step) : null;
  };
  const [step, setStep] = useState(read);
  useEffect(() => {
    const on = () => setStep(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return step;
}

type Ctx = {
  job: JobDetail | null; // the project on screen (null: none yet)
  refresh: () => Promise<JobDetail | null>;
  open: (id: string | null) => void; // show another project (null: start a new one)
  env: EnvState; // runtime, models, updates (see env.ts)
};
const JobContext = createContext<Ctx | null>(null);
export const useJob = () => useContext(JobContext)!;

const KEY = "bapu.job";
function remembered(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}
function remember(id: string | null) {
  try {
    localStorage.setItem(KEY, id ?? ""); // "": the user chose to start a new song
  } catch {
    /* storage unavailable: the latest job is used instead */
  }
}

/** Has the processing been started (or finished) at least once? */
export const started = (job: JobDetail) =>
  job.state === "running" || job.state === "queued" || job.stages.some((s) => s.state !== "missing");

type StepView = { step: Step; status: string; state: "done" | "now" | "warn" | "todo" | "locked" };

function steps(job: JobDetail | null, env: EnvState): StepView[] {
  const envView: StepView = env.busy
    ? { step: "env", status: env.runtime.state === "starting" ? t("启动中") : t("下载中"), state: "now" }
    : env.paused
      ? { step: "env", status: t("已暂停"), state: "warn" }
      : env.ready
        ? { step: "env", status: env.updateAvailable ? t("有更新") : "", state: "done" }
        : { step: "env", status: env.runtimeMissing || env.missingModels ? t("缺 {n} 项", { n: env.missingModels + (env.runtimeMissing ? 1 : 0) }) : "", state: "warn" };
  if (!job) {
    return [envView,
      { step: "import", status: "", state: env.ready ? "todo" : "locked" },
      { step: "run", status: "", state: "locked" },
      { step: "check", status: "", state: "locked" },
      { step: "export", status: "", state: "locked" }];
  }
  const total = job.stages.length || 9;
  const fresh = job.stages.filter((s) => s.state === "fresh").length;
  let run: StepView;
  if (job.state === "queued") run = { step: "run", status: t("排队中"), state: "now" };
  else if (job.state === "running") {
    const st = job.stages.find((s) => s.stage === job.stage);
    const pct = st ? Math.round(((st.index - 1 + (job.fraction ?? 0)) / total) * 100) : 0;
    run = { step: "run", status: `${pct}%`, state: "now" };
  } else if (job.state === "failed") run = { step: "run", status: t("出错了"), state: "warn" };
  else if (fresh === total && job.stages.length) run = { step: "run", status: "", state: "done" };
  else if (started(job)) run = { step: "run", status: `${fresh}/${total}`, state: "warn" };
  else run = { step: "run", status: "", state: "todo" };
  return [
    envView,
    { step: "import", status: "", state: started(job) ? "done" : "todo" },
    run,
    !job.has_score
      ? { step: "check", status: "", state: "locked" }
      : job.attention
        ? { step: "check", status: String(job.attention), state: "warn" }
        : { step: "check", status: "", state: "done" },
    !job.has_score
      ? { step: "export", status: "", state: "locked" }
      : job.exported_at && !job.export_stale
        ? { step: "export", status: "", state: "done" }
        : { step: "export", status: "", state: "todo" },
  ];
}

function ThemeButton() {
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const i = THEMES.findIndex((x) => x.id === theme);
  const label = t("外观：{name}", { name: t(THEMES[i].label) });
  return (
    <button type="button" className="icon-btn" title={label} aria-label={label}
      onClick={() => { const n = THEMES[(i + 1) % THEMES.length].id; setThemeState(n); setTheme(n); }}>
      <Icon name={theme} size={16} />
    </button>
  );
}

const SHORT: Record<Lang, string> = { "zh-CN": "中", en: "EN" };

function LangButton() {
  const l = getLang();
  const i = LANGS.findIndex((x) => x.id === l);
  const next = LANGS[(i + 1) % LANGS.length];
  const label = t("界面语言：{name}", { name: LANGS[i].label });
  return (
    <button type="button" className="icon-btn lang-btn" title={label} aria-label={label} onClick={() => setLang(next.id)}>
      {SHORT[l]}
    </button>
  );
}

function Sidebar({ job, env, current, onNew, onOpen, onSave, saved, open, onClose }: {
  job: JobDetail | null; env: EnvState; current: Step; onNew: () => void; onOpen: () => void; onSave: () => void;
  saved: boolean; open: boolean; onClose: () => void;
}) {
  const list = steps(job, env);
  return (
    <>
      <aside className="side" aria-label={t("导航")}>
        <div className="brand">
          <span className="logo" aria-hidden="true"><img src="/favicon.png" alt="" width="28" height="28" style={{ borderRadius: 8 }} /></span>
          Bapu
          <span style={{ marginLeft: "auto", display: "flex", gap: 2 }}><LangButton /><ThemeButton /></span>
        </div>
        <button type="button" className={`nav ${env.ready ? "" : "off"}`} onClick={onNew} aria-disabled={!env.ready}>
          <span className="ic"><Icon name="edit" /></span><span className="t">{t("新建工程")}</span>
        </button>
        <button type="button" className={`nav ${env.apiUp ? "" : "off"}`} onClick={onOpen} aria-disabled={!env.apiUp}>
          <span className="ic"><Icon name="folder" /></span><span className="t">{t("打开工程…")}</span>
        </button>
        <button type="button" className={`nav ${job ? "" : "off"} ${saved ? "saved" : ""}`} onClick={onSave} aria-disabled={!job}
          aria-label={saved ? t("保存工程（已保存）") : t("保存工程")}>
          <span className="ic"><Icon name="save" /></span><span className="t">{t("保存工程")}</span><small>Ctrl+S</small>
        </button>

        <nav className="steps" aria-label={t("步骤")} style={{ marginTop: 12 }}>
          {list.map((s, i) => {
            const locked = s.state === "locked";
            const on = s.step === current;
            return (
              <button key={s.step} type="button" className={`nav ${on ? "on" : ""} ${locked ? "off" : ""}`}
                aria-disabled={locked} aria-current={on ? "page" : undefined}
                onClick={() => { if (!locked) { go(s.step); onClose(); } }}>
                <span className={`dot ${s.state === "locked" ? "todo" : s.state}`} aria-hidden="true">
                  {s.state === "done" ? <Icon name="check" size={12} /> : s.state === "now" ? <span className="spin" style={{ width: 11, height: 11 }} /> : s.state === "warn" ? "!" : i + 1}
                </span>
                <span className="t">{t(TITLE[s.step])}</span>
                {s.status && <small>{s.status}</small>}
              </button>
            );
          })}
        </nav>

        <div className="side-foot">
          {env.updateAvailable && (
            <button type="button" className="nav upd" onClick={() => { go("env"); onClose(); }}>
              <span className="ic"><Icon name="upd" /></span><span className="t">{t("有新版本 {v}", { v: env.updateAvailable })}</span>
            </button>
          )}
          <div className="label">{t("负载")}</div>
          <LoadPanel apiUp={env.apiUp} />
        </div>
      </aside>
      {open && <div className="scrim" onClick={onClose} />}
    </>
  );
}

export default function App({ service = null }: { service?: Service | null }) {
  const routed = useStep();
  const env = useEnvState(service);
  const [jobId, setJobId] = useState<string | null | undefined>(undefined); // undefined: still looking
  const [job, setJob] = useState<JobDetail | null>(null);
  const [menu, setMenu] = useState(false);
  const [saved, setSaved] = useState(false);

  // the project to show: the one opened last, else the newest one
  useEffect(() => {
    if (!env.apiUp) return;
    api.get<JobSummary[]>("/jobs").then((list) => {
      const mine = remembered();
      setJobId(mine === "" ? null : list.find((j) => j.id === mine)?.id ?? list[0]?.id ?? null);
    }).catch(() => setJobId(null));
  }, [env.apiUp]);

  const refresh = useCallback(async () => {
    if (!jobId) {
      setJob(null);
      return null;
    }
    try {
      const d = await api.get<JobDetail>(`/jobs/${encodeURIComponent(jobId)}`);
      setJob(d);
      return d;
    } catch {
      return null;
    }
  }, [jobId]);

  useEffect(() => {
    refresh();
  }, [refresh, routed]);

  // keep the sidebar current while the job is busy
  useEffect(() => {
    if (job?.state !== "running" && job?.state !== "queued") return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [job?.state, refresh]);

  const open = useCallback((id: string | null) => {
    remember(id);
    setJob(null);
    setJobId(id);
  }, []);

  const openPath = useCallback((path: string) =>
    api.post<JobDetail>("/projects/open", { path }).then((d) => {
      open(d.id);
      window.location.hash = "/";
    }).catch((e) => window.alert(`${t("打不开这个工程")}\n\n${t((e as Error).message)}`)), [open]);

  // a .bapu Bapu was launched with, double-clicked or dropped onto the window
  useEffect(() => {
    if (!inDesktop() || !env.apiUp) return;
    invoke<string | null>("take_launch_project").then((p) => { if (p) openPath(p); }).catch(() => {});
    return onDesktopEvent<string>("open-project", openPath);
  }, [env.apiUp, openPath]);

  const onNew = () => {
    if (!env.ready) return;
    if (job?.state === "running" && !window.confirm(t("这个工程还在处理，离开后它会在后台继续跑完。确定吗？"))) return;
    open(null);
    go("import");
  };
  const onOpen = async () => {
    if (!env.apiUp) return;
    try {
      const local = await desktopPick("project");
      const p = local === undefined ? (await api.post<{ path: string | null }>("/dialog", { kind: "project" })).path : local;
      if (p) openPath(p);
    } catch (e) {
      window.alert(t((e as Error).message));
    }
  };
  const onSave = useCallback(async () => {
    if (!job) return;
    try {
      await api.post(`/jobs/${encodeURIComponent(job.id)}/save`);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      window.alert(t((e as Error).message));
    }
  }, [job]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        onSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onSave]);

  const ctx: Ctx = { job, refresh, open, env };
  const list = steps(job, env);
  const loading = env.apiUp && (jobId === undefined || (jobId && !job));

  // with no route, land where the song is: environment, import, processing or check
  const landing: Step = !env.ready ? "env" : !job ? "import" : job.has_score && job.state !== "running" ? "check" : started(job) ? "run" : "import";
  const wanted: Step = routed ?? landing;
  const shown: Step = list.find((x) => x.step === wanted)?.state === "locked" ? (env.ready ? (job ? "run" : "import") : "env") : wanted;

  let page;
  if (loading) page = null;
  else if (shown === "env") page = <EnvPage />;
  else if (shown === "import") page = <ImportPage key={job?.id ?? "new"} />;
  else if (shown === "run") page = <RunPage />;
  else if (shown === "check") page = <CheckPage key={job!.id} id={job!.id} />;
  else page = <ExportPage key={job!.id} id={job!.id} />;

  return (
    <JobContext.Provider value={ctx}>
      <div className={`shell ${menu ? "open" : ""}`}>
        <Sidebar job={job} env={env} current={shown} onNew={onNew} onOpen={onOpen} onSave={onSave} saved={saved}
          open={menu} onClose={() => setMenu(false)} />
        <div style={{ minWidth: 0 }}>
          <div className="mobilebar">
            <button type="button" className="icon-btn" aria-label={t("打开导航")} onClick={() => setMenu(true)}><Icon name="menu" /></button>
            <b>{t(TITLE[shown])}</b>
          </div>
          <main>{page}</main>
        </div>
      </div>
    </JobContext.Provider>
  );
}

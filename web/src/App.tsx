import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, fmtSize, type JobDetail, type JobSummary, type Model, type StageInfo, type SystemInfo } from "./api";
import { AudioStep, LangStep, LyricsStep } from "./pages/SetupSteps";
import { EnvStep } from "./pages/EnvStep";
import { isMissing, neededModels } from "./components/Models";
import RunPage from "./pages/RunPage";
import ReviewPage from "./pages/ReviewPage";
import ExportPage from "./pages/ExportPage";

/** One song at a time: the steps of the flow are the routes (#/audio … #/export). */
export type Step = "env" | "audio" | "lyrics" | "lang" | "run" | "review" | "export";
const STEP_ORDER: Step[] = ["env", "audio", "lyrics", "lang", "run", "review", "export"];

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
  job: JobDetail | null; refresh: () => Promise<JobDetail | null>; open: (id: string | null) => void;
  envReady: boolean | null; refreshEnv: () => void; // every model the pipeline needs is on disk
};
const JobContext = createContext<Ctx>({ job: null, refresh: async () => null, open: () => {}, envReady: null, refreshEnv: () => {} });
export const useJob = () => useContext(JobContext);

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

type StepView = { step: Step; title: string; status: string; state: "done" | "now" | "warn" | "todo" | "locked" };

function steps(job: JobDetail | null, envReady: boolean | null): StepView[] {
  const env: StepView = envReady === false
    ? { step: "env", title: "环境", status: "要下载模型", state: "warn" }
    : { step: "env", title: "环境", status: envReady ? "已就绪" : "", state: envReady ? "done" : "todo" };
  return [env, ...projectSteps(job, envReady !== false)];
}

function projectSteps(job: JobDetail | null, envOk: boolean): StepView[] {
  const LANG: Record<string, string> = { yue: "粤语", zh: "普通话", en: "英语" };
  if (!job) {
    return [
      { step: "audio", title: "原曲", status: "导入录音", state: envOk ? "todo" : "locked" },
      { step: "lyrics", title: "参考歌词", status: "可选", state: "locked" },
      { step: "lang", title: "语言", status: "", state: "locked" },
      { step: "run", title: "自动处理", status: "", state: "locked" },
      { step: "review", title: "审查", status: "", state: "locked" },
      { step: "export", title: "导出", status: "", state: "locked" },
    ];
  }
  const go_ = started(job);
  const total = job.stages.length || 9;
  const fresh = job.stages.filter((s) => s.state === "fresh").length;
  const langs = (job.params?.lyrics?.langs as string[] | undefined) ?? ["yue", "en"];
  let run: StepView;
  if (job.state === "queued") run = { step: "run", title: "自动处理", status: "排队中", state: "now" };
  else if (job.state === "running") {
    const st = job.stages.find((s) => s.stage === job.stage);
    run = { step: "run", title: "自动处理", status: st ? `第 ${st.index}/${total} 步` : "处理中", state: "now" };
  } else if (job.state === "failed") run = { step: "run", title: "自动处理", status: "出错了", state: "warn" };
  else if (fresh === total && job.stages.length) run = { step: "run", title: "自动处理", status: "已完成", state: "done" };
  else if (go_) run = { step: "run", title: "自动处理", status: `完成 ${fresh}/${total} 步`, state: "warn" };
  else run = { step: "run", title: "自动处理", status: "", state: "todo" };
  return [
    { step: "audio", title: "原曲", status: "已导入", state: "done" },
    { step: "lyrics", title: "参考歌词", status: job.has_lyrics ? "已导入" : go_ ? "没有" : "可选", state: job.has_lyrics || go_ ? "done" : "todo" },
    { step: "lang", title: "语言", status: go_ ? langs.map((l) => LANG[l] ?? l).join("、") : "", state: go_ ? "done" : "todo" },
    run,
    !job.has_score
      ? { step: "review", title: "审查", status: "", state: "locked" }
      : job.attention
        ? { step: "review", title: "审查", status: `${job.attention} 处待确认`, state: "warn" }
        : { step: "review", title: "审查", status: "都确认了", state: "done" },
    !job.has_score
      ? { step: "export", title: "导出", status: "", state: "locked" }
      : job.exported_at
        ? job.export_stale
          ? { step: "export", title: "导出", status: "改过，要重新导出", state: "warn" }
          : { step: "export", title: "导出", status: "已导出", state: "done" }
        : { step: "export", title: "导出", status: "", state: "todo" },
  ];
}

function Stepper({ job, envReady, current }: { job: JobDetail | null; envReady: boolean | null; current: Step }) {
  return (
    <nav className="flow" aria-label="步骤">
      {steps(job, envReady).map((s, i) => {
        const on = s.step === current;
        const inner = (
          <>
            <span className={`dot ${s.state}`} aria-hidden="true">
              {s.state === "now" ? <span className="spin" /> : s.state === "done" ? "✓" : s.state === "warn" ? "!" : i + 1}
            </span>
            <span className="txt"><b>{s.title}</b>{s.status && <span className="small">{s.status}</span>}</span>
          </>
        );
        return s.state === "locked"
          ? <span key={s.step} className="step locked" aria-disabled="true">{inner}</span>
          : <a key={s.step} href={`#/${s.step}`} className={`step ${on ? "on" : ""}`} aria-current={on ? "step" : undefined}>{inner}</a>;
      })}
    </nav>
  );
}

function Header({ job, onNew }: { job: JobDetail | null; onNew: () => void }) {
  const [sys, setSys] = useState<SystemInfo | null>(null);
  useEffect(() => {
    const load = () => api.get<SystemInfo>("/system").then(setSys).catch(() => setSys(null));
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  return (
    <header className="top">
      <span className="brand"><b>Bapu</b><span className="small" style={{ color: "var(--header-ink)" }}>扒谱</span></span>
      {job && <span className="song" title={job.name}>{job.name}</span>}
      <div className="sys">
        {sys?.gpu
          ? <span>{sys.gpu.name.replace("NVIDIA GeForce ", "")} · 显存 <span className="mono">{fmtSize(sys.gpu.total - sys.gpu.free)} / {fmtSize(sys.gpu.total)}</span></span>
          : <span>{sys ? "没有可用的显卡" : "连不上本机服务（bapu serve）"}</span>}
        {job && <button type="button" className="btn small ghost" onClick={onNew}>新建 / 打开</button>}
      </div>
    </header>
  );
}

export default function App() {
  const routed = useStep();
  const [jobId, setJobId] = useState<string | null | undefined>(undefined); // undefined: still looking
  const [job, setJob] = useState<JobDetail | null>(null);
  const [envReady, setEnvReady] = useState<boolean | null>(null);
  const refreshEnv = useCallback(() => {
    Promise.all([api.get<Model[]>("/models"), api.get<StageInfo[]>("/stages")])
      .then(([m, s]) => setEnvReady(!neededModels(s, m).some(isMissing)))
      .catch(() => setEnvReady(true)); // cannot tell: do not block the flow
  }, []);
  useEffect(refreshEnv, [refreshEnv]);

  // the job to show: #/p/<id> (from "bapu open"), else the one opened last in this browser, else the newest one
  useEffect(() => {
    const linked = window.location.hash.match(/^#\/p\/([^/]+)/)?.[1];
    if (linked) {
      const id = decodeURIComponent(linked);
      remember(id);
      window.history.replaceState(null, "", "#/");
      setJobId(id);
      return;
    }
    api.get<JobSummary[]>("/jobs").then((list) => {
      const mine = remembered();
      setJobId(mine === "" ? null : list.find((j) => j.id === mine)?.id ?? list[0]?.id ?? null);
    }).catch(() => setJobId(null));
  }, []);

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

  // keep the stepper current while the job is busy
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

  // #/p/<id> while the page is already open: the desktop window opening another .bapu
  useEffect(() => {
    const on = () => {
      const linked = window.location.hash.match(/^#\/p\/([^/]+)/)?.[1];
      if (!linked) return;
      window.history.replaceState(null, "", "#/");
      open(decodeURIComponent(linked));
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [open]);

  if (jobId === undefined || envReady === null) return <Header job={null} onNew={() => {}} />;
  if (jobId && !job) return <Header job={null} onNew={() => {}} />;

  // with no route, land where the song is: setup, processing or review
  const step: Step = routed ?? (envReady === false ? "env" : !job ? "audio" : job.has_score && job.state !== "running" ? "review" : started(job) ? "run" : "lyrics");
  const blocked = (s: Step) => steps(job, envReady).find((x) => x.step === s)?.state === "locked";
  const shown: Step = blocked(step) ? (job ? "run" : envReady === false ? "env" : "audio") : step;

  let page;
  if (shown === "env") page = <EnvStep />;
  else if (shown === "audio") page = <AudioStep />;
  else if (shown === "lyrics") page = <LyricsStep />;
  else if (shown === "lang") page = <LangStep />;
  else if (shown === "run") page = <RunPage />;
  else if (shown === "review") page = <ReviewPage key={job!.id} id={job!.id} />;
  else page = <ExportPage key={job!.id} id={job!.id} />;

  return (
    <JobContext.Provider value={{ job, refresh, open, envReady, refreshEnv }}>
      <Header job={job} onNew={() => {
        if (job?.state === "running" && !window.confirm("这个工程还在处理，离开后它会在后台继续跑完。确定吗？")) return;
        open(null);
        go("audio");
      }} />
      <main className="page">
        <Stepper job={job} envReady={envReady} current={shown} />
        {page}
      </main>
    </JobContext.Provider>
  );
}

import { useEffect, useState } from "react";
import { api, fmtSize, jobLabel, type JobSummary, type SystemInfo } from "./api";
import JobsPage from "./pages/JobsPage";
import RunPage from "./pages/RunPage";
import ReviewPage from "./pages/ReviewPage";
import ExportPage from "./pages/ExportPage";
import ModelsPage from "./pages/ModelsPage";

/** Hash routes: #/ · #/models · #/job/<id>/run|review|export */
function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export const go = (path: string) => {
  window.location.hash = path;
};

function Header({ section }: { section: "jobs" | "models" }) {
  const [sys, setSys] = useState<SystemInfo | null>(null);
  useEffect(() => {
    const load = () => api.get<SystemInfo>("/system").then(setSys).catch(() => setSys(null));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);
  return (
    <header className="top">
      <a className="brand" href="#/">
        <b>Bapu</b>
        <span className="small" style={{ color: "var(--header-ink)" }}>扒谱</span>
      </a>
      <nav aria-label="主导航">
        <a href="#/" className={section === "jobs" ? "on" : ""}>任务</a>
        <a href="#/models" className={section === "models" ? "on" : ""}>模型</a>
      </nav>
      <div className="sys">
        {sys?.gpu ? (
          <span>
            {sys.gpu.name.replace("NVIDIA GeForce ", "")} · 显存{" "}
            <span className="mono">{fmtSize(sys.gpu.total - sys.gpu.free)} / {fmtSize(sys.gpu.total)}</span>
          </span>
        ) : (
          <span>{sys ? "没有可用的显卡" : "连不上本机服务（bapu serve）"}</span>
        )}
        {sys && <span>队列：{sys.running.length ? `${sys.running.length} 个运行中` : "空闲"}{sys.queue ? `，${sys.queue} 个排队` : ""}</span>}
      </div>
    </header>
  );
}

export function JobHeader({ job, tab }: { job: JobSummary | null; tab: "run" | "review" | "export" }) {
  const id = job?.id ?? "";
  const [label, cls] = job ? jobLabel(job) : ["", ""];
  return (
    <div className="card" style={{ gap: 10, paddingBottom: 0 }}>
      <div className="row" style={{ gap: 12, alignItems: "center" }}>
        <a href="#/" className="small">任务</a>
        <span className="muted small">/</span>
        <h1 style={{ margin: 0, fontSize: 20 }}>{job?.name ?? "…"}</h1>
        {job && <span className={`badge ${cls}`}>{label}{job.state === "running" && job.stage ? ` · ${job.stage}` : ""}</span>}
        {job?.attention != null && <span className="badge warn">待确认 {job.attention}</span>}
      </div>
      <nav className="tabs" aria-label="任务页面">
        <a href={`#/job/${encodeURIComponent(id)}/run`} className={tab === "run" ? "on" : ""}>进度</a>
        <a href={`#/job/${encodeURIComponent(id)}/review`} className={tab === "review" ? "on" : ""}>审查</a>
        <a href={`#/job/${encodeURIComponent(id)}/export`} className={tab === "export" ? "on" : ""}>导出</a>
      </nav>
    </div>
  );
}

export default function App() {
  const route = useRoute();
  const section = route[0] === "models" ? "models" : "jobs";
  let page;
  if (route[0] === "models") page = <ModelsPage />;
  else if (route[0] === "job" && route[1]) {
    const tab = route[2] ?? "run";
    if (tab === "review") page = <ReviewPage id={route[1]} />;
    else if (tab === "export") page = <ExportPage id={route[1]} />;
    else page = <RunPage id={route[1]} />;
  } else page = <JobsPage />;
  return (
    <>
      <Header section={section} />
      {page}
    </>
  );
}

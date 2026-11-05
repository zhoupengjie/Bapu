import { useCallback, useEffect, useState } from "react";
import { ApiError, api, follow, type Model, type StageInfo } from "../api";
import { go, started, useJob } from "../App";
import { STEPS } from "../flow";
import { ModelDownload, isMissing, neededModels } from "../components/Models";
import LoadPanel from "../components/LoadPanel";

type Live = { stage: string; fraction: number | null; message: string };

/** Models this job will use that are not on disk yet. */
function missingModels(stages: StageInfo[], models: Model[], params: Record<string, Record<string, unknown>>): Model[] {
  return neededModels(stages, models, params).filter(isMissing);
}

export default function RunPage() {
  const { job, refresh } = useJob();
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [models, setModels] = useState<Model[] | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = job?.id ?? "";
  const enc = encodeURIComponent(id);

  const loadModels = useCallback(() => api.get<Model[]>("/models").then(setModels).catch(() => setModels([])), []);
  useEffect(() => {
    api.get<StageInfo[]>("/stages").then(setStages).catch(() => {});
    loadModels();
  }, [loadModels]);

  useEffect(() => {
    if (!id) return;
    return follow(`/jobs/${enc}/events`, (ev) => {
      if (ev.type === "progress") {
        setLive({ stage: ev.stage as string, fraction: (ev.fraction as number) ?? null, message: (ev.message as string) ?? "" });
        if (ev.kind === "done" || ev.kind === "skip") refresh();
      } else if (ev.type === "state") {
        if (ev.state !== "running") setLive(null);
        refresh();
      }
    });
  }, [id, enc, refresh]);

  const start = useCallback(async () => {
    setError(null);
    try {
      await api.post(`/jobs/${enc}/run`, {});
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && Array.isArray(e.detail)) loadModels();
      else setError((e as Error).message);
    }
  }, [enc, refresh, loadModels]);

  if (!job) return null;
  if (models === null) return <section className="card stepcard"><span className="muted"><span className="spin" style={{ display: "inline-block", verticalAlign: -2, marginRight: 8 }} />正在读取…</span></section>;
  const running = job.state === "running" || job.state === "queued";
  const missing = missingModels(stages, models, job.params);

  // once the last model is on disk, start without another click
  const onModelReady = async () => {
    const list = await api.get<Model[]>("/models");
    setModels(list);
    if (!running && missingModels(stages, list, job.params).length === 0) start();
  };
  if (missing.length && !running) {
    return (
      <section className="card stepcard" aria-labelledby="h-dl">
        <h1 id="h-dl">还缺模型</h1>
        <ModelDownload missing={missing} onReady={onModelReady} action="下载并开始处理 →" />
        <div className="nav-row"><button type="button" className="btn" onClick={() => go("lang")}>← 上一步</button><span /></div>
      </section>
    );
  }

  // before the first run the job has no stage rows yet: list the steps from the pipeline
  const rows = stages.map((info) => job.stages.find((r) => r.stage === info.name)
    ?? { stage: info.name, title: info.title, index: info.index, state: "missing" as const, metrics: {} });
  const total = stages.length || job.stages.length || 9;
  const fresh = job.stages.filter((s) => s.state === "fresh").length;
  const current = running ? job.stages.find((s) => s.stage === (live?.stage ?? job.stage)) : undefined;
  const frac = running
    ? current ? Math.min(1, (current.index - 1 + (live?.fraction ?? job.fraction ?? 0)) / total) : 0
    : fresh / total;
  const done = !running && fresh === total && job.stages.length > 0;
  const seconds = job.stages.reduce((n, s) => n + (s.seconds ?? 0), 0);

  let headline: string;
  if (job.state === "queued") headline = "排队中…";
  else if (running) headline = current ? `第 ${current.index}/${total} 步 · ${current.title}` : "准备中…";
  else if (done) headline = "处理完成";
  else if (job.state === "failed") headline = "处理出错了";
  else if (job.state === "cancelled") headline = "已停止";
  else if (started(job)) headline = `已完成 ${fresh}/${total} 步`;
  else headline = "还没开始";

  return (
    <section className="card stepcard" aria-labelledby="h-run">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 id="h-run">{headline}</h1>
        <span className="mono muted">{Math.round(frac * 100)}%</span>
      </div>
      <div className="bar big" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)}>
        <i style={{ width: `${frac * 100}%` }} />
      </div>
      {running && live?.message && <p className="muted small" style={{ margin: 0 }}>{live.message}</p>}
      {done && <p className="lead">用时约 {Math.round(seconds)} 秒。接下来检查一下标出来的地方。</p>}
      {job.state === "failed" && job.error && <div className="notice bad">{job.error}</div>}
      {error && <div className="notice bad">{error}</div>}

      <LoadPanel />

      <ol className="runlist">
        {rows.map((s) => {
          const isNow = running && current?.stage === s.stage;
          const state = isNow ? "now" : s.state === "fresh" ? "done" : "todo";
          return (
            <li key={s.stage} className={state}>
              <span className={`dot ${state}`} aria-hidden="true">{isNow ? <span className="spin" /> : state === "done" ? "✓" : ""}</span>
              <span className="name">{s.title}</span>
              <span className="muted small note">
                {isNow
                  ? (live?.fraction != null ? `${Math.round(live.fraction * 100)}%` : "进行中")
                  : state === "done" ? STEPS[s.stage]?.summary(s.metrics) : ""}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="nav-row">
        <button type="button" className="btn" onClick={() => go("lang")} disabled={running}>← 上一步</button>
        <span className="row" style={{ gap: 10 }}>
          {running && <button type="button" className="btn danger" onClick={() => api.post(`/jobs/${enc}/cancel`).then(refresh)}>停止</button>}
          {!running && !done && <button type="button" className="btn primary" onClick={start}>{started(job) ? "继续处理" : "开始处理"} →</button>}
          {done && <button type="button" className="btn primary" onClick={() => go("review")}>下一步：审查 →</button>}
        </span>
      </div>
    </section>
  );
}

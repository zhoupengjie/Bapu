import { useCallback, useEffect, useState } from "react";
import { ApiError, api, follow, type StageInfo } from "../api";
import { go, started, useJob } from "../App";
import { STAGE_LABEL } from "../env";
import { Icon } from "../icons";

type Live = { stage: string; fraction: number | null };
type Row = { stage: string; title: string; index: number; state: "fresh" | "stale" | "missing"; seconds: number | null };

// stages STAGE_LABEL does not name yet
const MORE_LABEL: Record<string, string> = { lead: "提取主唱", splits: "合并音符" };
const label = (stage: string, title: string) => STAGE_LABEL[stage] ?? MORE_LABEL[stage] ?? title;

function took(seconds: number): string {
  if (seconds < 60) return `用了 ${Math.max(1, Math.round(seconds))} 秒`;
  return `用了 ${Math.round(seconds / 60)} 分钟`;
}

export default function RunPage() {
  const { job, refresh } = useJob();
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsEnv, setNeedsEnv] = useState(false);
  const [busy, setBusy] = useState(false);
  const id = job?.id ?? "";
  const enc = encodeURIComponent(id);

  useEffect(() => {
    api.get<StageInfo[]>("/stages").then(setStages).catch(() => {});
  }, []);

  useEffect(() => {
    if (!id) return;
    return follow(`/jobs/${enc}/events`, (ev) => {
      if (ev.type === "progress") {
        setLive({ stage: ev.stage as string, fraction: (ev.fraction as number | undefined) ?? null });
        if (ev.kind === "done" || ev.kind === "skip") refresh();
      } else if (ev.type === "state") {
        if (ev.state !== "running") setLive(null);
        refresh();
      }
    });
  }, [id, enc, refresh]);

  const start = useCallback(async () => {
    setError(null);
    setNeedsEnv(false);
    setBusy(true);
    try {
      await api.post(`/jobs/${enc}/run`, {});
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNeedsEnv(true);
      else setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [enc, refresh]);

  const stop = useCallback(async () => {
    setError(null);
    try {
      await api.post(`/jobs/${enc}/cancel`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [enc, refresh]);

  if (!job) return null;

  const running = job.state === "running" || job.state === "queued";
  // before the first run the job has no stage rows yet: list the steps from the pipeline
  const rows: Row[] = stages.length
    ? stages.map((info) => job.stages.find((r) => r.stage === info.name)
      ?? { stage: info.name, title: info.title, index: info.index, state: "missing", seconds: null })
    : job.stages;
  const total = rows.length || 9;
  const fresh = rows.filter((s) => s.state === "fresh").length;
  const nowStage = job.state === "running" ? live?.stage ?? job.stage : null;
  const current = nowStage ? rows.find((s) => s.stage === nowStage) : undefined;
  const fraction = live && live.stage === current?.stage ? live.fraction : job.fraction;
  const frac = running
    ? current ? Math.min(1, (current.index - 1 + (fraction ?? 0)) / total) : 0
    : fresh / total;
  const pct = Math.round(frac * 100);
  const done = !running && rows.length > 0 && fresh === rows.length;
  const seconds = rows.reduce((n, s) => n + (s.seconds ?? 0), 0);
  const failed = job.state === "failed";

  let headline: string;
  if (job.state === "queued") headline = "排队中…";
  else if (running) headline = current ? `正在${label(current.stage, current.title)}…` : "准备中…";
  else if (done) headline = "处理完成";
  else if (failed) headline = "处理出错了";
  else if (job.state === "cancelled" || started(job)) headline = "已停止";
  else headline = "还没开始";

  return (
    <section className="card narrow" aria-labelledby="h-run">
      <div className="foot">
        <h1 id="h-run">{headline}</h1>
        <span className="mono" style={{ fontSize: 18 }}>{pct}%</span>
      </div>
      <div className="bar" role="progressbar" aria-label="总进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <i style={{ width: `${pct}%` }} />
      </div>

      {failed && job.error && <div className="notice bad">{job.error}</div>}
      {error && <div className="notice bad">{error}</div>}
      {needsEnv && (
        <div className="notice bad foot">
          <span>还缺模型</span>
          <button type="button" className="btn" onClick={() => go("env")}>去环境</button>
        </div>
      )}

      <div className="runlist" role="list">
        {rows.map((s) => {
          const state = running && current?.stage === s.stage ? "now" : s.state === "fresh" ? "done" : "todo";
          return (
            <div key={s.stage} className={state} role="listitem" aria-current={state === "now" ? "step" : undefined}>
              {state === "done"
                ? <span style={{ color: "var(--ok)", display: "inline-flex" }}><Icon name="check" size={16} /></span>
                : state === "now"
                  ? <span className="spin" aria-hidden="true" />
                  : <span aria-hidden="true" style={{ width: 16, height: 16, borderRadius: "50%", border: "2px solid var(--border)", flex: "none" }} />}
              <span>{label(s.stage, s.title)}</span>
            </div>
          );
        })}
      </div>

      <div className="foot">
        {running ? (
          <><span /><button type="button" className="btn" onClick={stop}>停止</button></>
        ) : done ? (
          <><span className="muted" style={{ fontSize: 14 }}>{took(seconds)}</span>
            <button type="button" className="btn primary" onClick={() => go("check")}>去检查</button></>
        ) : (
          <><span />
            <button type="button" className="btn primary" disabled={busy} onClick={start}>{started(job) ? "继续处理" : "开始处理"}</button></>
        )}
      </div>
    </section>
  );
}

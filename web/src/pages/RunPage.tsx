import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, follow, type JobDetail, type StageInfo } from "../api";
import { JobHeader } from "../App";

const STATE: Record<string, [string, string]> = { fresh: ["最新", "ok"], stale: ["需重跑", "warn"], missing: ["未运行", ""] };

function metricsLine(m: Record<string, unknown>): string {
  return Object.entries(m)
    .filter(([, v]) => typeof v !== "object" || v === null)
    .slice(0, 6)
    .map(([k, v]) => `${k}=${v}`)
    .join(" · ");
}

export default function RunPage({ id }: { id: string }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [live, setLive] = useState<{ stage: string; fraction: number | null; message: string } | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [from, setFrom] = useState<string>("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const load = useCallback(() => api.get<JobDetail>(`/jobs/${encodeURIComponent(id)}`).then(setJob).catch((e) => setError(e.message)), [id]);

  useEffect(() => {
    load();
    api.get<StageInfo[]>("/stages").then(setStages).catch(() => {});
    const stop = follow(`/jobs/${encodeURIComponent(id)}/events`, (ev) => {
      const time = new Date((ev.at as number) * 1000).toLocaleTimeString();
      if (ev.type === "progress") {
        setLive({ stage: ev.stage as string, fraction: (ev.fraction as number) ?? null, message: (ev.message as string) ?? "" });
        if (ev.kind !== "update" || (ev.message as string)?.startsWith("载入"))
          setLog((l) => [...l.slice(-300), `${time} ${ev.stage} ${ev.kind}${ev.message ? " " + ev.message : ""}`]);
        if (ev.kind === "done" || ev.kind === "skip") load();
      } else if (ev.type === "state") {
        setLog((l) => [...l.slice(-300), `${time} —— ${ev.state}${ev.error ? "：" + ev.error : ""}`]);
        if (ev.state !== "running") setLive(null);
        load();
      }
    });
    return stop;
  }, [id, load]);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [log]);

  const running = job?.state === "running" || job?.state === "queued";
  const fromStage = stages.find((s) => s.name === from);
  const current = (job?.params?.[from] ?? {}) as Record<string, unknown>;

  async function run() {
    setError(null);
    const set: Record<string, Record<string, unknown>> = {};
    for (const [k, raw] of Object.entries(edits)) {
      const n = Number(raw);
      (set[from] ??= {})[k] = raw.trim() === "" ? null : Number.isFinite(n) && /^-?[\d.]+$/.test(raw.trim()) ? n : raw;
    }
    try {
      await api.post(`/jobs/${encodeURIComponent(id)}/run`, { from: from || undefined, set });
      setEdits({});
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && Array.isArray(e.detail)) setError(`缺少模型：${(e.detail as string[]).join("、")}，先到模型页下载。`);
      else setError((e as Error).message);
    }
  }

  return (
    <main className="page">
      <JobHeader job={job} tab="run" />
      {error && <div className="notice bad">{error}</div>}
      {job && !job.has_lyrics && <div className="notice warn">没有参考歌词：歌词全部来自听写，审查时每句都要确认。可以在下面补传。</div>}
      <div className="row">
        <section className="card flush grow" aria-labelledby="steps">
          <div className="head">
            <h2 id="steps" style={{ margin: 0, fontSize: 17 }}>步骤</h2>
            {live && <span className="small mono">{live.stage} · {live.fraction != null ? `${Math.round(live.fraction * 100)}%` : ""} {live.message}</span>}
          </div>
          <div className="scroll-x">
            <table className="grid">
              <thead><tr><th>#</th><th>步骤</th><th>状态</th><th>耗时</th><th>结果</th><th /></tr></thead>
              <tbody>
                {job?.stages.map((s) => {
                  const isLive = live?.stage === s.stage && running;
                  const [label, cls] = isLive ? ["运行中", "info"] : STATE[s.state];
                  return (
                    <tr key={s.stage} className={from === s.stage ? "sel" : ""}>
                      <td className="mono muted">{String(s.index).padStart(2, "0")}</td>
                      <td>{s.title}<div className="muted small mono">{s.stage}</div></td>
                      <td>
                        <span className={`badge ${cls}`}>{label}</span>
                        {isLive && live?.fraction != null && <div className="bar" style={{ marginTop: 6, width: 90 }}><i style={{ width: `${live.fraction * 100}%` }} /></div>}
                      </td>
                      <td className="mono small">{s.seconds != null ? `${s.seconds.toFixed(1)} s` : "—"}</td>
                      <td className="small muted" style={{ maxWidth: 420 }}>{metricsLine(s.metrics)}</td>
                      <td><button type="button" className="link" disabled={running} onClick={() => { setFrom(s.stage); setEdits({}); }}>从这一步重跑</button></td>
                    </tr>
                  );
                })}
                {job && job.stages.length === 0 && <tr><td colSpan={6} className="muted">还没有运行过。</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="side" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <section className="card" aria-labelledby="rerun">
            <h2 id="rerun">{from ? `从「${fromStage?.title ?? from}」重跑` : "运行"}</h2>
            <p className="muted small" style={{ margin: 0 }}>
              {from ? "这一步和后面的步骤会重新计算，前面的用缓存。" : "没变的步骤直接用缓存。"}
            </p>
            {fromStage && Object.entries(fromStage.defaults).filter(([k]) => !(k in fromStage.model_params)).map(([k, v]) => (
              <label key={k} className="field"><span className="mono">{k}</span>
                <input type="text" className="mono" placeholder={String(current[k] ?? v)} value={edits[k] ?? ""}
                  onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} />
              </label>
            ))}
            <div className="row" style={{ gap: 10 }}>
              <button type="button" className="btn primary" disabled={running} onClick={run}>{from ? "从这一步重跑" : "开始 / 继续"}</button>
              {from && <button type="button" className="btn" onClick={() => setFrom("")}>取消选择</button>}
              {running && <button type="button" className="btn danger" onClick={() => api.post(`/jobs/${encodeURIComponent(id)}/cancel`).then(load)}>取消任务</button>}
            </div>
          </section>

          {job && !job.has_lyrics && (
            <section className="card">
              <h2>补传参考歌词</h2>
              <input type="file" accept=".ass,.ssa,.lrc,.txt" onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const form = new FormData();
                form.set("lyrics", f);
                await api.form(`/jobs/${encodeURIComponent(id)}/lyrics`, form).then(load).catch((er) => setError(er.message));
              }} />
            </section>
          )}

          <section className="card">
            <div style={{ display: "flex", justifyContent: "space-between" }}><h2>日志</h2><span className="muted small">不含歌词</span></div>
            <pre className="log mono" ref={logRef}>{log.length ? log.join("\n") : "等待事件…"}</pre>
            {job?.has_score && <a href={`#/job/${encodeURIComponent(id)}/review`}>去审查 →</a>}
          </section>
        </aside>
      </div>
    </main>
  );
}

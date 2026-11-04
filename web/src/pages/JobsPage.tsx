import { useEffect, useMemo, useState } from "react";
import { ApiError, api, fmtSize, jobLabel, type JobDetail, type JobSummary, type Model, type StageInfo } from "../api";
import { go } from "../App";

function useModelsAndStages() {
  const [models, setModels] = useState<Model[]>([]);
  const [stages, setStages] = useState<StageInfo[]>([]);
  useEffect(() => {
    api.get<Model[]>("/models").then(setModels).catch(() => {});
    api.get<StageInfo[]>("/stages").then(setStages).catch(() => {});
  }, []);
  return { models, stages };
}

function parseValue(raw: string): unknown {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) && /^-?[\d.]+(e-?\d+)?$/i.test(raw.trim()) ? n : raw;
}

function NewJob({ onCreated }: { onCreated: () => void }) {
  const { models, stages } = useModelsAndStages();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"upload" | "path">("upload");
  const [audio, setAudio] = useState<File | null>(null);
  const [lyrics, setLyrics] = useState<File | null>(null);
  const [audioPath, setAudioPath] = useState("");
  const [lyricsPath, setLyricsPath] = useState("");
  const [choice, setChoice] = useState<Record<string, string>>({}); // "stage.param" -> model id
  const [params, setParams] = useState<Record<string, string>>({}); // "stage.param" -> raw text
  const [adv, setAdv] = useState(false);
  const [langs, setLangs] = useState<string[]>(["yue", "en"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; missing?: string[] } | null>(null);

  const modelRows = useMemo(
    () => stages.flatMap((s) => Object.entries(s.model_params).map(([param, tag]) => ({ s, param, tag }))),
    [stages],
  );
  const paramRows = useMemo(
    () => stages.flatMap((s) => Object.entries(s.defaults)
      .filter(([k, v]) => !(k in s.model_params) && (typeof v === "number" || typeof v === "string"))
      .map(([k, v]) => ({ s, k, v }))),
    [stages],
  );

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.set("name", name);
      if (mode === "upload") {
        if (!audio) throw new Error("请选择录音文件");
        form.set("audio", audio);
        if (lyrics) form.set("lyrics", lyrics);
      } else {
        if (!audioPath.trim()) throw new Error("请填写录音文件的路径");
        form.set("audio_path", audioPath.trim());
        if (lyricsPath.trim()) form.set("lyrics_path", lyricsPath.trim());
      }
      const job = await api.form<JobDetail>("/jobs", form);
      const set: Record<string, Record<string, unknown>> = { lyrics: { langs }, dictate: { langs } };
      for (const [key, raw] of Object.entries(params)) {
        const [st, p] = key.split(".");
        (set[st] ??= {})[p] = parseValue(raw);
      }
      const chosen: Record<string, Record<string, string>> = {};
      for (const [key, id] of Object.entries(choice)) {
        const [st, p] = key.split(".");
        (chosen[st] ??= {})[p] = id;
      }
      try {
        await api.post(`/jobs/${encodeURIComponent(job.id)}/run`, { set, models: chosen });
        go(`/job/${encodeURIComponent(job.id)}/run`);
      } catch (e) {
        onCreated();
        if (e instanceof ApiError && e.status === 409 && Array.isArray(e.detail)) {
          setError({ message: "任务已建好，但还缺模型，先下载再到进度页开始：", missing: e.detail as string[] });
        } else throw e;
      }
    } catch (e) {
      setError({ message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card grow" aria-labelledby="newjob">
      <div>
        <h1 id="newjob" style={{ margin: 0, fontSize: 22 }}>新建任务</h1>
        <p className="muted small" style={{ margin: "4px 0 0" }}>
          歌词由模型从录音里听写；给了参考歌词时，用它来校正用字。
        </p>
      </div>

      <label className="field">任务名
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="默认用录音文件名" />
      </label>

      <div className="seg" role="group" aria-label="文件来源">
        <button type="button" className={mode === "upload" ? "on" : ""} onClick={() => setMode("upload")}>上传文件</button>
        <button type="button" className={mode === "path" ? "on" : ""} onClick={() => setMode("path")}>本机路径（大文件更快）</button>
      </div>

      {mode === "upload" ? (
        <div className="row" style={{ gap: 14 }}>
          <label className="field" style={{ flex: "1 1 260px" }}>1 · 录音
            <input type="file" accept="audio/*,.wav,.flac,.mp3" onChange={(e) => setAudio(e.target.files?.[0] ?? null)} />
          </label>
          <label className="field" style={{ flex: "1 1 260px" }}>2 · 参考歌词（可选：.ass / .lrc / .txt）
            <input type="file" accept=".ass,.ssa,.lrc,.txt" onChange={(e) => setLyrics(e.target.files?.[0] ?? null)} />
          </label>
        </div>
      ) : (
        <div className="row" style={{ gap: 14 }}>
          <label className="field" style={{ flex: "1 1 260px" }}>1 · 录音路径
            <input type="text" className="mono" value={audioPath} onChange={(e) => setAudioPath(e.target.value)} placeholder="D:\歌曲\xxx.wav" />
          </label>
          <label className="field" style={{ flex: "1 1 260px" }}>2 · 参考歌词路径（可选）
            <input type="text" className="mono" value={lyricsPath} onChange={(e) => setLyricsPath(e.target.value)} />
          </label>
        </div>
      )}
      <p className="muted small" style={{ margin: "-6px 0 0" }}>歌词只在本机处理；日志和报告里只出现句号、音符编号和时间。</p>

      <div className="field">3 · 歌里有哪些语言（可多选）
        <div className="seg" role="group" aria-label="语言">
          {([["yue", "粤语"], ["zh", "普通话"], ["en", "英语"]] as const).map(([id, label]) => {
            const on = langs.includes(id);
            return (
              <button key={id} type="button" className={on ? "on" : ""} aria-pressed={on}
                onClick={() => setLangs(on ? (langs.length > 1 ? langs.filter((x) => x !== id) : langs) : [...langs, id])}>
                {on ? "✓ " : ""}{label}
              </button>
            );
          })}
        </div>
        <span className="muted small" style={{ fontWeight: 400 }}>
          {langs.includes("yue") && langs.includes("zh") ? "粤语和普通话都选时，按听写模型对每个乐句的判断分别处理。" : "英文单词总是按英语处理。"}
        </span>
      </div>

      <div className="field">
        <span style={{ display: "flex", justifyContent: "space-between" }}>
          4 · 每一步用的模型 <a href="#/models">管理和下载模型 →</a>
        </span>
        <div className="card flush">
          <table className="grid">
            <tbody>
              {modelRows.map(({ s, param, tag }) => {
                const key = `${s.name}.${param}`;
                const options = models.filter((m) => m.stage === tag);
                const value = choice[key] ?? String(s.defaults[param] ?? "");
                const sel = options.find((m) => m.id === value);
                return (
                  <tr key={key}>
                    <td style={{ width: 120 }}>{s.title}{param !== "model" && <span className="muted small"> · {param.replace("_model", "")}</span>}</td>
                    <td>
                      <select value={value} onChange={(e) => setChoice({ ...choice, [key]: e.target.value })} style={{ width: "100%" }}>
                        {options.map((m) => (
                          <option key={m.id} value={m.id}>{m.name}{m.verified ? "" : " · 实验"}</option>
                        ))}
                        {param === "dereverb_model" && <option value="none">不去混响</option>}
                      </select>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {sel && (
                        <>
                          <span className={`badge ${sel.status === "ready" || sel.status === "builtin" ? "ok" : "warn"}`}>
                            {sel.status === "builtin" ? "无需下载" : sel.status === "ready" ? `已下载 · ${fmtSize(sel.size)}` : `未下载 · ${fmtSize(sel.size)}`}
                          </span>{" "}
                          {sel.license.commercial === "no" && <span className="badge warn">仅限非商业</span>}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <button type="button" className="link" onClick={() => setAdv(!adv)} aria-expanded={adv}>
          {adv ? "▾" : "▸"} 5 · 高级参数（不改就用默认值）
        </button>
        {adv && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, padding: 14, background: "var(--surface-2)", borderRadius: 8, marginTop: 6 }}>
            {paramRows.map(({ s, k, v }) => (
              <label key={`${s.name}.${k}`} className="field">
                {s.title} · <span className="mono">{k}</span>
                <input type="text" className="mono" placeholder={String(v)} value={params[`${s.name}.${k}`] ?? ""}
                  onChange={(e) => {
                    const next = { ...params, [`${s.name}.${k}`]: e.target.value };
                    if (!e.target.value) delete next[`${s.name}.${k}`];
                    setParams(next);
                  }} />
              </label>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="notice bad">
          {error.message}
          {error.missing && <> <span className="mono">{error.missing.join("、")}</span> · <a href="#/models">去模型页</a></>}
        </div>
      )}
      <div className="row" style={{ gap: 12, alignItems: "center" }}>
        <button type="button" className="btn primary" disabled={busy} onClick={submit}>{busy ? "正在创建…" : "开始扒谱"}</button>
        <span className="muted small">同一时间只跑一个任务，其余排队。</span>
      </div>
    </section>
  );
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const load = () => api.get<JobSummary[]>("/jobs").then(setJobs).catch(() => setJobs([]));
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);
  return (
    <main className="page">
      <div className="row">
        <NewJob onCreated={load} />
        <aside className="side card" aria-labelledby="recent">
          <h2 id="recent">任务</h2>
          {jobs === null && <span className="muted small">加载中…</span>}
          {jobs?.length === 0 && <span className="muted small">还没有任务。</span>}
          {jobs?.map((j) => {
            const [label, cls] = jobLabel(j);
            return (
              <a key={j.id} href={`#/job/${encodeURIComponent(j.id)}/${j.has_score ? "review" : "run"}`}
                style={{ display: "flex", flexDirection: "column", gap: 6, padding: 12, border: "1px solid var(--border)", borderRadius: 8, color: "var(--ink)", textDecoration: "none" }}>
                <span style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <b style={{ fontWeight: 500 }}>{j.name}</b>
                  <span className={`badge ${cls}`}>{label}</span>
                </span>
                <span className="muted small">
                  {j.state === "running" && j.stage ? `${j.stage}${j.fraction != null ? ` · ${Math.round(j.fraction * 100)}%` : ""}` : j.has_score ? `已有乐谱${j.attention != null ? ` · 待确认 ${j.attention}` : ""}` : "还没有乐谱"}
                  {!j.has_lyrics && " · 无参考歌词"}
                </span>
                {j.state === "running" && <span className="bar"><i style={{ width: `${Math.round((j.fraction ?? 0) * 100)}%` }} /></span>}
                {j.error && <span className="small" style={{ color: "var(--bad)" }}>{j.error}</span>}
              </a>
            );
          })}
        </aside>
      </div>
    </main>
  );
}

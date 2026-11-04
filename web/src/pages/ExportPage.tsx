import { useEffect, useState } from "react";
import { api, fmtSize, type JobDetail } from "../api";
import { JobHeader } from "../App";

const FORMATS = [
  { id: "svp", name: "SynthV 工程（.svp）", points: ["每个音符带歌词和语言；歌手留空，在 SV 里自己选", "时间按秒换算成 blick，不量化"] },
  { id: "mid", name: "MIDI", points: ["单轨，带拍速、拍号和每个音符的歌词事件（含 - 和 +），UTF-8", "在 SV 里导入后要把工程拍速设成一样的"] },
  { id: "paste", name: "粘贴文件（后备）", points: ["一句一行，在 SV 里全选音符后粘贴", "只有歌词，没有音高和时间"] },
];

type Stats = { stats: Record<string, number>; lines: number[]; applied: boolean; entries: number };

export default function ExportPage({ id }: { id: string }) {
  const enc = encodeURIComponent(id);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [formats, setFormats] = useState<Record<string, boolean>>({ svp: true, mid: true, paste: false });
  const [tracks, setTracks] = useState<Record<string, boolean>>({ accompaniment: true, lead_dry: true });
  const [files, setFiles] = useState<{ format: string; name: string; size: number }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [svp, setSvp] = useState<File | null>(null);
  const [preview, setPreview] = useState<Stats | null>(null);

  useEffect(() => {
    api.get<JobDetail>(`/jobs/${enc}`).then(setJob).catch((e) => setError(e.message));
  }, [enc]);

  async function exportNow() {
    setBusy(true);
    setError(null);
    try {
      const audio = Object.entries(tracks).filter(([, on]) => on).map(([k]) => k).join(",") || "none";
      const r = await api.post<{ files: typeof files }>(`/jobs/${enc}/export`, {
        formats: Object.entries(formats).filter(([, on]) => on).map(([k]) => k), audio,
      });
      setFiles(r.files);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function readSvp(apply: boolean) {
    if (!svp) return;
    setError(null);
    const form = new FormData();
    form.set("project", svp);
    form.set("apply", String(apply));
    try {
      setPreview(await api.form<Stats>(`/jobs/${enc}/corrections/from-svp`, form));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <main className="page">
      <JobHeader job={job} tab="export" />
      {error && <div className="notice bad">{error}</div>}
      {job && !job.has_score && <div className="notice warn">还没有乐谱：先在进度页跑完整个流程。</div>}
      <div className="row">
        <section className="card grow" aria-labelledby="exp">
          <h2 id="exp">导出</h2>
          {FORMATS.map((f) => (
            <div key={f.id} style={{ border: `1px solid ${formats[f.id] ? "var(--accent)" : "var(--border)"}`, borderRadius: 8, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
              <label style={{ display: "flex", gap: 10, alignItems: "center", fontWeight: 500 }}>
                <input type="checkbox" checked={!!formats[f.id]} onChange={() => setFormats({ ...formats, [f.id]: !formats[f.id] })} />
                {f.name}
              </label>
              <ul className="small muted" style={{ margin: 0, paddingLeft: 20 }}>{f.points.map((p) => <li key={p}>{p}</li>)}</ul>
              {f.id === "svp" && formats.svp && (
                <div className="row small" style={{ gap: 16 }}>
                  <span className="muted">音频轨（只引用路径，不复制音频）：</span>
                  <label><input type="checkbox" checked={tracks.accompaniment} onChange={() => setTracks({ ...tracks, accompaniment: !tracks.accompaniment })} /> 伴奏</label>
                  <label><input type="checkbox" checked={tracks.lead_dry} onChange={() => setTracks({ ...tracks, lead_dry: !tracks.lead_dry })} /> 主唱干声（对比用）</label>
                </div>
              )}
            </div>
          ))}
          <div className="row" style={{ gap: 12, alignItems: "center" }}>
            <button type="button" className="btn primary" disabled={busy || !job?.has_score || !Object.values(formats).some(Boolean)} onClick={exportNow}>
              {busy ? "生成中…" : "生成"}
            </button>
            <span className="muted small">文件也会存到工作目录的 export/ 里。</span>
          </div>
          {files.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {files.map((f) => (
                <li key={f.name}><a href={`/api/jobs/${enc}/export/${encodeURIComponent(f.name)}`} download>{f.name}</a> <span className="muted small">{fmtSize(f.size)}</span></li>
              ))}
            </ul>
          )}
        </section>

        <aside className="side card" aria-labelledby="fromsvp">
          <h2 id="fromsvp">从 SV 读回修正</h2>
          <p className="muted small" style={{ margin: 0 }}>在 SV 里改好并保存后选这个 .svp：和计划逐个对比，生成修正。合并的改动先写，重跑后再读一次才能比较歌词。</p>
          <input type="file" accept=".svp" onChange={(e) => { setSvp(e.target.files?.[0] ?? null); setPreview(null); }} />
          <div className="row" style={{ gap: 10 }}>
            <button type="button" className="btn" disabled={!svp} onClick={() => readSvp(false)}>预览</button>
            <button type="button" className="btn primary" disabled={!svp || !preview || preview.entries === 0 || preview.applied} onClick={() => readSvp(true)}>写入修正文件</button>
          </div>
          {preview && (
            <div className="small" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span>确认合并 {preview.stats.merge_confirm ?? 0} · 拒绝合并 {preview.stats.merge_reject ?? 0} · 字幕修正 {preview.stats.line_text ?? 0} · 人工版本 {preview.stats.line_version ?? 0} 句 · 音高 {preview.stats.pitch ?? 0}</span>
              {preview.lines.length > 0 && <span className="muted">人工版本：{preview.lines.map((l) => `L${String(l).padStart(2, "0")}`).join(" ")}</span>}
              {preview.stats.needs_rerun ? <span className="muted">这次只有合并修正：写入后重跑，再读一次。</span> : null}
              {preview.applied && <span style={{ color: "var(--ok)" }}>已写入。到进度页重跑生效。</span>}
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

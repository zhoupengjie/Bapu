import { useState } from "react";
import { api, follow, fmtSize, type Model, type StageInfo } from "../api";
import DownloadInfo from "./DownloadInfo";

/** Models the pipeline will use (stage defaults, or a job's own params). */
export function neededModels(stages: StageInfo[], models: Model[], params: Record<string, Record<string, unknown>> = {}): Model[] {
  const ids = stages.flatMap((s) => Object.keys(s.model_params).map((p) => params[s.name]?.[p] ?? s.defaults[p]));
  return models.filter((m) => ids.includes(m.id));
}

export const isMissing = (m: Model) => m.status === "missing" || m.status === "corrupt";

/** The models still to download: licenses accepted once, then all downloaded in a row. */
export function ModelDownload({ missing, onReady, action }: { missing: Model[]; onReady: () => void; action: string }) {
  const [agree, setAgree] = useState(false);
  const [progress, setProgress] = useState<Record<string, { done: number; total: number | null }>>({});
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState<string | null>(null);
  const busy = Object.keys(progress).length > 0 || missing.some((m) => m.downloading);
  const total = missing.reduce((n, m) => n + (m.size ?? 0), 0);

  async function pullAll() {
    setError(null);
    for (const m of missing) {
      try {
        await api.post(`/models/${encodeURIComponent(m.id)}/pull`, { accept_license: true });
      } catch (e) {
        setError(`${m.name}：${(e as Error).message}`);
        continue;
      }
      setProgress((p) => ({ ...p, [m.id]: { done: 0, total: m.size } }));
      const stop = follow(`/models/${encodeURIComponent(m.id)}/events`, (ev) => {
        if (ev.type === "progress") setProgress((p) => ({ ...p, [m.id]: { done: ev.done as number, total: (ev.total as number) ?? m.size } }));
        if (ev.type === "state" && ev.state !== "downloading") {
          stop();
          if (ev.state === "failed") setError(`${m.name} 下载失败：${ev.error}`);
          setProgress((p) => { const n = { ...p }; delete n[m.id]; return n; });
          onReady();
        }
      });
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="muted small" style={{ margin: 0 }}>共 {missing.length} 个，约 {fmtSize(total)}。只下载一次，之后都在本机运行。</p>
      <ul className="dl-list">
        {missing.map((m) => {
          const p = progress[m.id];
          return (
            <li key={m.id}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <span><b style={{ fontWeight: 500 }}>{m.name}</b> <span className="muted small">{fmtSize(m.size)}</span></span>
                <span className="small" style={{ color: m.license.commercial === "yes" ? "var(--muted)" : "var(--warn)" }}>
                  许可证：{m.license.url ? <a href={m.license.url} target="_blank" rel="noreferrer">{m.license.name}</a> : m.license.name}
                  {m.license.commercial === "no" && "（不可商用）"}
                </span>
              </div>
              {p && (
                <>
                  <span className="bar"><i style={{ width: `${p.total ? Math.max(2, (p.done / p.total) * 100) : 3}%` }} /></span>
                  <span className="muted small mono">{fmtSize(p.done)} / {fmtSize(p.total)}</span>
                </>
              )}
              <button type="button" className="link" style={{ alignSelf: "flex-start", padding: 0 }} onClick={() => setManual(manual === m.id ? null : m.id)}>
                {manual === m.id ? "▾" : "▸"} 自己下载（地址、目录和文件名）
              </button>
              {manual === m.id && <DownloadInfo model={m} onRecheck={onReady} />}
            </li>
          );
        })}
      </ul>
      <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <input type="checkbox" checked={agree} onChange={() => setAgree(!agree)} /> 我已阅读并接受上面这些许可证
      </label>
      {error && <div className="notice bad">{error}</div>}
      <div><button type="button" className="btn primary" disabled={!agree || busy} onClick={pullAll}>{busy ? "下载中…" : action}</button></div>
    </div>
  );
}

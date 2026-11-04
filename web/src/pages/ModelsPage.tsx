import { useCallback, useEffect, useState } from "react";
import { api, follow, fmtSize, type Model, type StageInfo } from "../api";

const COMMERCIAL = { yes: "允许", no: "不允许", unknown: "未知，请自行确认" };

export default function ModelsPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [dialog, setDialog] = useState<Model | null>(null);
  const [agree, setAgree] = useState(false);
  const [progress, setProgress] = useState<Record<string, { done: number; total: number | null; file: string }>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api.get<Model[]>("/models").then(setModels).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
    api.get<StageInfo[]>("/stages").then(setStages).catch(() => {});
  }, [load]);

  function watch(id: string) {
    const stop = follow(`/models/${encodeURIComponent(id)}/events`, (ev) => {
      if (ev.type === "progress") setProgress((p) => ({ ...p, [id]: { done: ev.done as number, total: (ev.total as number) ?? null, file: ev.file as string } }));
      if (ev.type === "state" && ev.state !== "downloading") {
        stop();
        if (ev.state === "failed") setError(`${id} 下载失败：${ev.error}`);
        setProgress((p) => { const n = { ...p }; delete n[id]; return n; });
        load();
      }
    });
  }

  async function pull(m: Model, accept: boolean) {
    setError(null);
    try {
      await api.post(`/models/${encodeURIComponent(m.id)}/pull`, { accept_license: accept });
      setDialog(null);
      watch(m.id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(m: Model) {
    if (!window.confirm(`删除 ${m.name} 的权重文件？`)) return;
    await api.del(`/models/${encodeURIComponent(m.id)}`).catch((e) => setError(e.message));
    load();
  }

  // group by the stage a model serves ("lead.karaoke" belongs to the lead stage)
  const stageOf = (tag: string) => stages.find((s) => Object.values(s.model_params).includes(tag));
  const groups = new Map<string, Model[]>();
  for (const m of models) {
    const key = stageOf(m.stage)?.title ?? m.stage;
    groups.set(key, [...(groups.get(key) ?? []), m]);
  }
  const used = models.filter((m) => m.status === "ready").reduce((a, m) => a + (m.size ?? 0), 0);

  return (
    <main className="page">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>模型</h1>
          <p className="muted small" style={{ margin: "4px 0 0" }}>只列出注册表里的模型：固定的下载地址和 SHA-256，下载前先看许可证。权重不进代码仓库。</p>
        </div>
        <span className="small">已下载 {models.filter((m) => m.status === "ready").length} 个 · 约 <span className="mono">{fmtSize(used)}</span></span>
      </div>
      {error && <div className="notice bad">{error}</div>}
      {[...groups.entries()].map(([title, list]) => (
        <section key={title} className="card flush" aria-label={title}>
          <div className="head"><h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2></div>
          <table className="grid">
            <tbody>
              {list.map((m) => {
                const p = progress[m.id];
                return (
                  <tr key={m.id}>
                    <td style={{ minWidth: 240 }}>
                      <b style={{ fontWeight: 500 }}>{m.name}</b>{" "}
                      <span className={`badge ${m.verified ? "ok" : "warn"}`}>{m.verified ? "已验证" : "实验"}</span>
                      <div className="muted small mono">{m.id}</div>
                    </td>
                    <td className="small" style={{ color: m.license.commercial === "yes" ? "var(--ink)" : "var(--warn)" }}>{m.license.name}</td>
                    <td className="mono small">{fmtSize(m.size)}</td>
                    <td style={{ minWidth: 200 }}>
                      {m.status === "builtin" && <span className="muted small">随程序安装</span>}
                      {m.status === "ready" && (
                        <span className="row" style={{ gap: 8, alignItems: "center" }}>
                          <span className="small" style={{ color: "var(--ok)" }}>✓ 已下载 · 校验通过</span>
                          <button type="button" className="btn small" onClick={() => remove(m)}>删除</button>
                        </span>
                      )}
                      {(m.status === "missing" || m.status === "corrupt") && (p || m.downloading ? (
                        <div className="small">
                          <div className="muted">下载中 {p?.total ? `${Math.round((p.done / p.total) * 100)}%` : fmtSize(p?.done)}</div>
                          <div className="bar"><i style={{ width: `${p?.total ? (p.done / p.total) * 100 : 5}%` }} /></div>
                        </div>
                      ) : (
                        <button type="button" className="btn small primary" onClick={() => { setAgree(m.license_accepted); setDialog(m); }}>
                          {m.status === "corrupt" ? "校验失败，重新下载" : "看许可证并下载"}
                        </button>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ))}

      {dialog && (
        <div className="dialog-bg" onClick={() => setDialog(null)}>
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dlgt" onClick={(e) => e.stopPropagation()}>
            <h2 id="dlgt" style={{ margin: 0, fontSize: 18 }}>下载 {dialog.name}</h2>
            <div className="small" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 14px" }}>
              <span className="muted">大小</span><span>{fmtSize(dialog.size)}</span>
              <span className="muted">来源</span><span>注册表里记录的固定地址</span>
              <span className="muted">校验</span><span>下载后核对 SHA-256，不一致就不启用</span>
            </div>
            <div className="notice warn small">
              <b>许可证：{dialog.license.name}</b>
              <div>商用：{COMMERCIAL[dialog.license.commercial]}</div>
              {dialog.license.note && <div>{dialog.license.note}</div>}
              {dialog.license.url && <a href={dialog.license.url} target="_blank" rel="noreferrer">{dialog.license.url}</a>}
            </div>
            <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <input type="checkbox" checked={agree} onChange={() => setAgree(!agree)} /> 我已阅读并接受这个许可证
            </label>
            <div className="row" style={{ gap: 10, justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => setDialog(null)}>取消</button>
              <button type="button" className="btn primary" disabled={!agree} onClick={() => pull(dialog, true)}>接受并下载</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

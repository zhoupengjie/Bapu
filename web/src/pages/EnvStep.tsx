import { useCallback, useEffect, useState } from "react";
import { api, fmtSize, type Model, type StageInfo } from "../api";
import { go, useJob } from "../App";
import { ModelDownload, isMissing, neededModels } from "../components/Models";

export type Environment = {
  device: { kind: string; name: string; torch: string | null; build?: string; experimental?: boolean };
  python: string;
  portable: boolean;
  home: string | null;
  runtime: { variant: string; label: string; python: string; installed_at: number } | null;
  models_dir: string;
  disk_free: number;
  association: { supported: boolean; on: boolean; other: boolean };
};

const KIND: Record<string, string> = { cuda: "GPU（CUDA）", xpu: "GPU（Intel XPU）", mps: "GPU（Apple MPS）", cpu: "CPU" };

/** Step 0: what Bapu runs on and the models it needs. Shown first until every model is ready. */
export function EnvStep() {
  const { job, refreshEnv } = useJob();
  const [env, setEnv] = useState<Environment | null>(null);
  const [models, setModels] = useState<Model[] | null>(null);
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [e, m, s] = await Promise.all([
      api.get<Environment>("/system/environment"), api.get<Model[]>("/models"), api.get<StageInfo[]>("/stages"),
    ]);
    setEnv(e);
    setModels(m);
    setStages(s);
    refreshEnv();
  }, [refreshEnv]);
  useEffect(() => {
    load().catch((e) => setError((e as Error).message));
  }, [load]);

  if (!env || !models) {
    return <section className="card stepcard">{error ? <div className="notice bad">{error}</div> : <span className="muted"><span className="spin inline" />正在检查环境…</span>}</section>;
  }
  const needed = neededModels(stages, models);
  const missing = needed.filter(isMissing);
  const dev = env.device;

  async function associate(on: boolean) {
    setError(null);
    try {
      setEnv(await api.post<Environment>("/system/association", { on }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="card stepcard" aria-labelledby="h-env">
      <h1 id="h-env">环境</h1>
      <p className="lead">{missing.length ? "第一次使用要先下载模型。下载完就可以开始扒谱了。" : "运行环境和模型都准备好了。"}</p>

      <div className="env-grid">
        <div className="env-item">
          <span className="muted small">推理设备</span>
          <b>{dev.name}</b>
          <span className="small">
            {KIND[dev.kind] ?? dev.kind}
            {dev.experimental && <span className="badge warn" style={{ marginLeft: 6 }}>实验</span>}
          </span>
          {dev.kind === "cpu" && <span className="small" style={{ color: "var(--warn)" }}>没有用上显卡，处理一首歌会慢很多</span>}
        </div>
        <div className="env-item">
          <span className="muted small">运行环境</span>
          <b>Python {env.python} · PyTorch {dev.torch ?? "—"}</b>
          <span className="small muted">{env.runtime ? `${env.runtime.label}（程序自己装的）` : env.portable ? "—" : "开发环境（.venv）"}</span>
        </div>
        <div className="env-item">
          <span className="muted small">模型位置</span>
          <span className="mono small" style={{ overflowWrap: "anywhere" }}>{env.models_dir}</span>
          <span className="small muted">剩余空间 {fmtSize(env.disk_free)}</span>
        </div>
      </div>

      <div className="field">模型（{needed.length - missing.length}/{needed.length} 个已就绪）
        {missing.length > 0
          ? <ModelDownload missing={missing} onReady={() => load().catch(() => {})} action="接受许可证并下载" />
          : (
            <ul className="dl-list">
              {needed.map((m) => (
                <li key={m.id} style={{ flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap" }}>
                  <span><b style={{ fontWeight: 500 }}>{m.name}</b> <span className="muted small">{m.status === "builtin" ? "随程序安装" : fmtSize(m.size)}</span></span>
                  <span className="small" style={{ color: "var(--ok)" }}>✓ {m.status === "builtin" ? "无需下载" : "已下载，校验通过"}</span>
                </li>
              ))}
            </ul>
          )}
      </div>

      {env.association.supported && (
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
          <input type="checkbox" checked={env.association.on} onChange={() => associate(!env.association.on)} style={{ marginTop: 3 }} />
          <span>双击 .bapu 文件时用这个 Bapu 打开
            <span className="muted small" style={{ display: "block" }}>
              可选。只改当前用户的文件关联，随时可以关掉；不打开的话，也可以在 Windows 的“打开方式”里选 Bapu.exe，或者把 .bapu 拖到窗口上。
              {env.association.other && !env.association.on && " 现在 .bapu 关联到了别的位置的 Bapu。"}
            </span>
          </span>
        </label>
      )}
      {error && <div className="notice bad">{error}</div>}
      <div className="nav-row">
        <span />
        <button type="button" className="btn primary" disabled={missing.length > 0} onClick={() => (job ? (window.location.hash = "/") : go("audio"))}>
          {job ? "回到工程 →" : "下一步 →"}
        </button>
      </div>
    </section>
  );
}

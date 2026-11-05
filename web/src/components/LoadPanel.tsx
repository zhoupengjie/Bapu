import { useEffect, useState } from "react";
import { api } from "../api";

export type Load = {
  at: number;
  cpu: number;
  cpu_count: number;
  ram: { used: number; total: number };
  gpu: { name: string; util: number; mem_used: number; mem_total: number; temp?: number } | null;
};

const HISTORY = 60; // seconds shown in the sparklines
const gb = (n: number) => `${(n / 2 ** 30).toFixed(1)}`;

function Spark({ values, color }: { values: number[]; color: string }) {
  const w = 120, h = 28;
  const pts = values.map((v, i) => `${(i / (HISTORY - 1)) * w},${h - (Math.min(100, Math.max(0, v)) / 100) * (h - 2) - 1}`);
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      {pts.length > 1 && <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
    </svg>
  );
}

function Tile({ label, value, sub, pct, history, color }: {
  label: string; value: string; sub: string; pct: number; history: number[]; color: string;
}) {
  return (
    <div className="load-tile">
      <div className="load-head"><span className="muted small">{label}</span><b className="mono">{value}</b></div>
      <Spark values={history} color={color} />
      <span className="bar"><i style={{ width: `${Math.min(100, pct)}%`, background: color }} /></span>
      <span className="muted small mono">{sub}</span>
    </div>
  );
}

/** CPU / RAM / GPU / VRAM, refreshed every second: shows the machine is working, not stuck. */
export default function LoadPanel() {
  const [hist, setHist] = useState<Load[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    const tick = () => api.get<Load>("/system/load")
      .then((l) => { if (alive) { setHist((h) => [...h.slice(-(HISTORY - 1)), l]); setError(false); } })
      .catch(() => alive && setError(true));
    tick();
    const t = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const last = hist.at(-1);
  if (!last) return <div className="muted small">{error ? "读不到资源占用" : "正在读取资源占用…"}</div>;
  const ramPct = (last.ram.used / last.ram.total) * 100;
  const g = last.gpu;
  return (
    <section className="load" aria-label="资源占用">
      <Tile label="CPU" value={`${Math.round(last.cpu)}%`} sub={`${last.cpu_count} 个逻辑核心`} pct={last.cpu}
        history={hist.map((l) => l.cpu)} color="var(--accent)" />
      <Tile label="内存" value={`${Math.round(ramPct)}%`} sub={`${gb(last.ram.used)} / ${gb(last.ram.total)} GB`} pct={ramPct}
        history={hist.map((l) => (l.ram.used / l.ram.total) * 100)} color="var(--accent)" />
      {g ? (
        <>
          <Tile label="GPU" value={`${g.util}%`} sub={`${g.name.replace("NVIDIA GeForce ", "")}${g.temp != null ? ` · ${g.temp}°C` : ""}`}
            pct={g.util} history={hist.map((l) => l.gpu?.util ?? 0)} color="var(--ok)" />
          <Tile label="显存" value={`${Math.round((g.mem_used / g.mem_total) * 100)}%`} sub={`${gb(g.mem_used)} / ${gb(g.mem_total)} GB`}
            pct={(g.mem_used / g.mem_total) * 100} history={hist.map((l) => (l.gpu ? (l.gpu.mem_used / l.gpu.mem_total) * 100 : 0))} color="var(--ok)" />
        </>
      ) : (
        <div className="load-tile"><span className="muted small">GPU</span><span className="muted small">没有检测到 NVIDIA 显卡</span></div>
      )}
    </section>
  );
}

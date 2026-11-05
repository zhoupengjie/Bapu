import { useEffect, useState } from "react";
import { api } from "../api";
import { inDesktop, invoke } from "../desktop";

type Load = {
  cpu: number | null;
  ram: { used: number; total: number } | null;
  gpu: { util: number; mem_used: number; mem_total: number } | null;
  net: { up: number; down: number } | null;
};

const NET_FULL = 50e6; // the network bars are full at 50 MB/s
const rate = (b: number) => (b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB/s` : `${Math.round(b / 1e3)} KB/s`);

function Row({ k, v, pct, sep }: { k: string; v: string; pct: number; sep?: boolean }) {
  return (
    <div className={`ld ${sep ? "sep" : ""}`}>
      <span>{k}</span><b className="mono">{v}</b>
      <div className="bar"><i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} /></div>
    </div>
  );
}

/** CPU, memory, GPU, VRAM, download and upload, every second: shows the machine is working.
 *  From the Python service when it runs (it sees the GPU), else from the desktop shell. */
export default function LoadPanel({ apiUp }: { apiUp: boolean }) {
  const [l, setL] = useState<Load | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => {
      const p = apiUp ? api.get<Load>("/system/load") : inDesktop() ? invoke<Load>("sys_load") : Promise.reject();
      p.then((x) => alive && setL(x)).catch(() => {});
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(t); };
  }, [apiUp]);

  const ram = l?.ram ? (l.ram.used / l.ram.total) * 100 : null;
  const g = l?.gpu;
  const vram = g ? (g.mem_used / g.mem_total) * 100 : null;
  const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x)}%`);
  return (
    <div className="load" aria-label="负载">
      <Row k="CPU" v={pct(l?.cpu)} pct={l?.cpu ?? 0} />
      <Row k="内存" v={pct(ram)} pct={ram ?? 0} />
      <Row k="GPU" v={pct(g?.util)} pct={g?.util ?? 0} />
      <Row k="显存" v={pct(vram)} pct={vram ?? 0} />
      <Row k="下载" v={l?.net ? rate(l.net.down) : "—"} pct={l?.net ? (l.net.down / NET_FULL) * 100 : 0} sep />
      <Row k="上传" v={l?.net ? rate(l.net.up) : "—"} pct={l?.net ? (l.net.up / NET_FULL) * 100 : 0} />
    </div>
  );
}

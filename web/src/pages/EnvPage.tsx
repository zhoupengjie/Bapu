import { useRef, useState, type ReactNode } from "react";
import { go, useJob } from "../App";
import { STAGE_LABEL, type SourceChoice } from "../env";
import { Icon } from "../icons";

/** The checklist rows: the runtime, the models in pipeline order, Bapu itself. */
type Row = {
  key: string; name: string; size: number; got: number;
  done: boolean; now: boolean; paused: boolean; st: ReactNode;
};

const STAGE_ORDER = Object.keys(STAGE_LABEL);
const VARIANT_ORDER = ["cuda", "xpu", "cpu"];
const SOURCES: [SourceChoice, string][] = [["auto", "自动"], ["official", "官方源"], ["china", "国内镜像"]];

/** Drop the technical parts of a label: "(CUDA 12.8)", trailing version numbers. */
const plain = (s: string) => s.replace(/\s*[（(].*$/, "").replace(/\s+v?\d+(\.\d+)+\s*$/, "").trim();
const gb = (b: number, digits = 2) => (b / 1e9).toFixed(digits);
const mbs = (bps: number) => `${(bps / 1e6).toFixed(1)} MB/s`;
const part = (got: number, size: number) => `${gb(got)} / ${gb(size)} GB`;
const warn = (t: string) => <span style={{ color: "var(--warn)" }}>{t}</span>;
const sum = (rows: Row[], f: (r: Row) => number) => rows.reduce((a, r) => a + f(r), 0);

/** Step 1 (环境): one card for every case — ready, downloading, paused, missing, update. */
export default function EnvPage() {
  const { env } = useJob();
  const { runtime, downloads, update, setup } = env;
  const [open, setOpen] = useState<"device" | "source" | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  const [showLicenses, setShowLicenses] = useState(false);
  const [details, setDetails] = useState(false);
  const [pending, setPending] = useState(false);
  const batch = useRef(new Set<string>()); // the rows of the current download run, for a steady overall percent

  const act = (f: () => Promise<unknown>) => async () => {
    setPending(true);
    try {
      await f();
    } finally {
      setPending(false);
    }
  };

  const rtInstalling = runtime.state === "installing";
  const rtStarting = runtime.state === "starting";
  const modelsBusy = downloads?.state === "downloading";
  const updBusy = update?.state === "downloading";
  const downloading = rtInstalling || rtStarting || modelsBusy || updBusy;
  const paused = !downloading && env.paused;
  const speed = modelsBusy ? downloads?.speed ?? 0 : rtInstalling ? env.netSpeed : 0;

  // ---- the runtime row
  const installVariant = setup?.variants.find((v) => v.id === (setup.previous?.variant ?? setup.recommended));
  const rtSize = env.portable && installVariant ? installVariant.size_gb * 1e9 : 0;
  let rtStep = "";
  const rt: Row = { key: "runtime", name: "Python 环境", size: rtSize, got: 0, done: false, now: false, paused: false, st: "" };
  if (!env.portable || runtime.state === "ready") Object.assign(rt, { done: true, got: rtSize });
  else if (runtime.state === "installing") {
    rtStep = runtime.index ? `第 ${runtime.index}/${runtime.total} 步 · ${plain(runtime.title)}` : "准备中";
    Object.assign(rt, { now: true, got: (rtSize * Math.max(0, runtime.index - 1)) / Math.max(1, runtime.total),
      st: <>{rtStep}{speed > 0 && <> · <b>{mbs(speed)}</b></>}</> });
  } else if (runtime.state === "starting") Object.assign(rt, { now: true, got: rtSize, st: "启动中" });
  else if (runtime.state === "failed") rt.st = warn("没装好");
  else if (runtime.partial) rt.st = rtSize ? `可续传 · ${gb(rtSize)} GB` : "可续传";
  else rt.st = <>{warn("缺少")}{rtSize ? ` · ${gb(rtSize)} GB` : ""}</>;

  // ---- the model rows (before the Python service runs: what the desktop shell knows)
  const pausedId = downloads?.state === "paused" ? downloads.current ?? downloads.queue[0] ?? null : null;
  const source = downloads
    ? downloads.items.map((i) => ({ id: i.id, stage: i.stage, name: i.name, size: i.size, got: i.done, ok: i.status === "ready" || i.status === "builtin" }))
    : (setup?.models ?? []).map((m) => ({ id: m.id, stage: m.stage, name: m.name, size: m.size, got: m.builtin ? m.size : 0, ok: m.builtin }));
  const rank = (stage: string) => (STAGE_ORDER.includes(stage) ? STAGE_ORDER.indexOf(stage) : STAGE_ORDER.length);
  const models: Row[] = [...source].sort((a, b) => rank(a.stage) - rank(b.stage)).map((m) => {
    const r: Row = { key: `m:${m.id}`, name: STAGE_LABEL[m.stage] ?? m.name, size: m.size, got: m.got, done: m.ok, now: false, paused: false, st: "" };
    if (m.ok) r.got = m.size;
    else if (modelsBusy && downloads?.current === m.id) {
      r.now = true;
      r.st = <>{part(m.got, m.size)}{speed > 0 && <> · <b>{mbs(speed)}</b></>}</>;
    } else if (m.id === pausedId) {
      r.paused = true;
      r.st = `${part(m.got, m.size)} · 已暂停`;
    } else if (((modelsBusy || paused) && downloads?.queue.includes(m.id)) || rtInstalling || rtStarting) r.st = "等待";
    else if (m.got > 0) r.st = `${part(m.got, m.size)} · 可续传`;
    else r.st = <>{warn("缺少")} · {gb(m.size)} GB</>;
    return r;
  });

  // ---- Bapu itself (only the running service knows)
  let bapu: Row | null = null;
  if (env.apiUp && update) {
    const latest = update.latest?.version ?? update.current;
    const zip = update.latest?.zip.size ?? 0;
    bapu = { key: "bapu", name: `Bapu ${update.current}`, size: zip, got: 0, done: true, now: false, paused: false, st: "最新版" };
    if (updBusy) {
      const size = update.total ?? zip;
      Object.assign(bapu, { name: `Bapu ${latest}`, size, got: update.done, done: false, now: true, st: size ? part(update.done, size) : "" });
    } else if (update.state === "ready") Object.assign(bapu, { name: `Bapu ${latest}`, got: zip, st: "关闭后换上" });
    else if (env.updateAvailable) {
      Object.assign(bapu, { name: `Bapu ${env.updateAvailable}`, done: false, st: <>{warn("有新版本")}{zip ? ` · ${gb(zip)} GB` : ""}</> });
    } else if (update.state === "available") bapu.st = "有新版本"; // development checkout: updated with git
  }

  const rows = [rt, ...models, ...(bapu ? [bapu] : [])];
  const todo = [rt, ...models].filter((r) => !r.done);

  // ---- overall progress of this run
  const active = downloading || paused;
  if (!active) batch.current.clear();
  else for (const r of rows) if (!r.done && (r.key !== "bapu" || updBusy)) batch.current.add(r.key);
  const inRun = rows.filter((r) => batch.current.has(r.key));
  const total = sum(inRun, (r) => r.size);
  const got = sum(inRun, (r) => (r.done ? r.size : Math.min(r.got, r.size)));
  const pct = total ? Math.min(100, Math.floor((got / total) * 100)) : 0;

  // ---- the device
  const variants = [...(setup?.variants ?? [])].sort((a, b) => VARIANT_ORDER.indexOf(a.id) - VARIANT_ORDER.indexOf(b.id));
  const chosenId = pick ?? setup?.previous?.variant ?? setup?.recommended ?? null;
  const chosen = variants.find((v) => v.id === chosenId);
  const device = env.environment?.device.name ?? setup?.previous?.device.name ?? (installVariant ? plain(installVariant.label) : null);
  const canChangeDevice = env.portable && !!setup && variants.length > 0 && !rtInstalling && !rtStarting;

  // ---- the hero
  const checking = !active && runtime.state === "ready" && !downloads;
  const pctEl = <span className="mono" style={{ fontSize: 18 }}>{pct}%</span>;
  let hero: ReactNode;
  if (checking) {
    hero = <>
      <span className="badge-big acc"><span className="spin" /></span>
      <div style={{ flex: 1, minWidth: 0 }}><h1 id="h-env">正在检查</h1></div>
    </>;
  } else if (downloading) {
    const left = total - got;
    const lead = speed > 0 && left > 0
      ? `还要约 ${Math.max(1, Math.round(left / speed / 60))} 分钟 · ${mbs(speed)}`
      : rtInstalling ? rtStep : rtStarting && !modelsBusy && !updBusy ? "马上就好" : total ? `已下 ${gb(got, 1)} / ${gb(total, 1)} GB` : "";
    hero = <>
      <span className="badge-big acc"><span className="spin" /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 id="h-env">{rtStarting && !modelsBusy && !updBusy ? "正在启动" : "正在下载"}</h1>
        {lead && <p className="lead">{lead}</p>}
      </div>
      {total > 0 && pctEl}
      {(rtInstalling || modelsBusy) && <button type="button" className="btn" disabled={pending} onClick={act(env.pause)}>暂停</button>}
    </>;
  } else if (paused) {
    hero = <>
      <span className="badge-big acc"><Icon name="pause" size={22} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 id="h-env">已暂停</h1>
        <p className="lead">已下 {gb(got, 1)} / {gb(total, 1)} GB，继续时接着下</p>
      </div>
      {pctEl}
      <button type="button" className="btn primary" disabled={pending} onClick={act(env.start)}>继续</button>
    </>;
  } else if (env.ready && env.updateAvailable) {
    hero = <>
      <span className="badge-big acc"><Icon name="upd" size={22} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 id="h-env">Bapu 有新版本</h1>
        <p className="lead">关闭 Bapu 时自动换上</p>
      </div>
      <button type="button" className="btn primary" disabled={pending} onClick={act(env.start)}>更新</button>
    </>;
  } else if (env.ready) {
    hero = <>
      <span className="badge-big ok"><Icon name="check" size={22} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 id="h-env">环境就绪</h1>
        {device && <p className="lead">用 {device} 推理</p>}
      </div>
      <button type="button" className="btn primary" onClick={() => go("import")}>下一步</button>
    </>;
  } else {
    const left = sum(todo, (r) => Math.max(0, r.size - r.got));
    const resumable = todo.some((r) => r.got > 0) || (runtime.state === "missing" && runtime.partial);
    const label = runtime.state === "failed" ? "重试" : resumable ? "继续下载" : "下载";
    hero = <>
      <span className="badge-big warn"><Icon name="down" size={22} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 id="h-env">还缺 {todo.length} 项</h1>
        {left > 0 && <p className="lead">还要下 {gb(left, 1)} GB</p>}
      </div>
      <button type="button" className="btn primary" disabled={pending} onClick={act(env.start)}>{label}</button>
    </>;
  }

  // ---- licenses of what is still to download
  const licenses = downloads
    ? downloads.items.filter((i) => i.status === "missing" || i.status === "corrupt")
    : (setup?.models ?? []).filter((m) => !m.builtin && m.license).map((m) => ({ id: m.id, stage: m.stage, name: m.name, license: m.license! }));
  const modelsMissing = models.some((r) => !r.done);

  // ---- errors
  const errors = [
    env.error,
    runtime.state === "failed" ? runtime.error : null,
    downloads?.state === "failed" ? downloads.error : null,
    update?.state === "error" && update.error?.startsWith("下载更新失败") ? update.error : null,
  ].filter((e): e is string => !!e);
  const [firstError, ...moreError] = errors.join("\n").split("\n");

  const row = (k: string, v: ReactNode, extra: ReactNode, first = false, body: ReactNode = null) => (
    <div style={first ? undefined : { borderTop: "1px solid var(--border)" }}>
      <div className="row"><span className="k">{k}</span><span className="v">{v}</span>{extra}</div>
      {body}
    </div>
  );

  return (
    <section className="card narrow" aria-labelledby="h-env">
      <div className="hero">{hero}</div>
      {active && total > 0 && <div className="bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${pct}%` }} /></div>}

      {errors.length > 0 && (
        <div className="notice bad">
          {firstError}
          {moreError.length > 0 && <> <button type="button" className="link" onClick={() => setDetails(!details)}>{details ? "收起" : "查看详情"}</button></>}
          {details && moreError.length > 0 && <pre className="log mono" style={{ marginTop: 8 }}>{moreError.join("\n")}</pre>}
        </div>
      )}

      <div className="dls">
        {rows.map((r) => {
          const fill = r.size ? Math.min(100, (r.got / r.size) * 100) : r.done ? 100 : 0;
          return (
            <div key={r.key} className={`dl ${r.now || r.paused ? "now" : r.done ? "" : "todo"}`}>
              {r.done
                ? <span style={{ color: "var(--ok)", display: "grid" }}><Icon name="check" size={16} /></span>
                : r.now ? <span className="spin" />
                  : r.paused ? <span style={{ color: "var(--accent-text)", display: "grid" }}><Icon name="pause" size={16} /></span>
                    : <span className="wait" />}
              <span className="name">{r.name}</span>
              {r.done || r.now || r.paused || r.got > 0
                ? <div className="bar"><i style={{ width: `${fill}%`, ...(r.done ? { background: "var(--ok)" } : {}) }} /></div>
                : <span />}
              <span className="st mono">{r.st}</span>
            </div>
          );
        })}
      </div>

      <div className="rows">
        {row("推理设备", device ?? "—",
          canChangeDevice && <button type="button" className="link" onClick={() => setOpen(open === "device" ? null : "device")}>{open === "device" ? "收起" : "更改"}</button>,
          true,
          open === "device" && canChangeDevice && (
            <div className="opts">
              {variants.map((v) => (
                <button key={v.id} type="button" className={`opt ${chosenId === v.id ? "on" : ""}`} disabled={!v.available}
                  aria-pressed={chosenId === v.id} onClick={() => setPick(v.id)}>
                  <span>{plain(v.label)}{v.recommended && <span className="pill acc" style={{ marginLeft: 8 }}>推荐</span>}</span>
                  <span className="muted mono" style={{ fontSize: 12.5 }}>约 {v.size_gb} GB</span>
                </button>
              ))}
              {chosen?.available && chosen.note && <span className="small" style={{ color: "var(--warn)" }}>{chosen.note}</span>}
              <button type="button" className="btn primary" style={{ alignSelf: "flex-start", marginTop: 4 }}
                disabled={pending || env.busy || !chosen?.available}
                onClick={act(async () => {
                  if (!chosenId) return;
                  await env.installRuntime(chosenId);
                  setOpen(null);
                })}>
                {runtime.state === "ready" ? "用这个重新安装" : "用这个安装"}
              </button>
            </div>
          ))}
        {row("下载源", SOURCES.find(([id]) => id === env.source)?.[1] ?? "自动",
          <button type="button" className="link" onClick={() => setOpen(open === "source" ? null : "source")}>{open === "source" ? "收起" : "更改"}</button>,
          false,
          open === "source" && (
            <div className="opts">
              {SOURCES.map(([id, label]) => (
                <button key={id} type="button" className={`opt ${env.source === id ? "on" : ""}`} aria-pressed={env.source === id}
                  onClick={() => { env.setSource(id); setOpen(null); }}>
                  <span>{label}</span>
                </button>
              ))}
            </div>
          ))}
        {row("下载线程", null,
          <span className="stepper" role="group" aria-label="下载线程数">
            <button type="button" aria-label="减少" disabled={env.threads <= 1} onClick={() => env.setThreads(env.threads - 1)}><Icon name="minus" size={14} /></button>
            <b className="mono" aria-live="polite">{env.threads}</b>
            <button type="button" aria-label="增加" disabled={env.threads >= 16} onClick={() => env.setThreads(env.threads + 1)}><Icon name="plus" size={14} /></button>
          </span>)}
      </div>

      {modelsMissing && (
        <div className="muted small">
          下载即表示同意各模型的许可证
          {licenses.length > 0 && <> <button type="button" className="link" onClick={() => setShowLicenses(!showLicenses)}>{showLicenses ? "收起" : "查看"}</button></>}
          {showLicenses && licenses.length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {licenses.map((i) => (
                <li key={i.id}>
                  {STAGE_LABEL[i.stage] ?? i.name}：
                  {i.license.url ? <a href={i.license.url} target="_blank" rel="noreferrer">{i.license.name}</a> : i.license.name}
                  {i.license.commercial === "no" && <span style={{ color: "var(--warn)" }}> · 不可商用</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

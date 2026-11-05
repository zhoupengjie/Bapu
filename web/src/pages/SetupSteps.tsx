import { useEffect, useRef, useState } from "react";
import { ApiError, api, type JobDetail, type JobSummary } from "../api";
import { go, started, useJob } from "../App";
import { desktopPick, onDesktopDrop } from "../desktop";

function Nav({ back, next, nextLabel = "下一步", disabled, busy, extra }: {
  back?: () => void; next: () => void; nextLabel?: string; disabled?: boolean; busy?: boolean; extra?: React.ReactNode;
}) {
  return (
    <div className="nav-row">
      {back ? <button type="button" className="btn" onClick={back}>← 上一步</button> : <span />}
      <span className="row" style={{ gap: 10, alignItems: "center" }}>
        {extra}
        <button type="button" className="btn primary" disabled={disabled || busy} onClick={next}>{busy ? "请稍候…" : `${nextLabel} →`}</button>
      </span>
    </div>
  );
}

type Dialog = { path: string | null; folder?: string };
/** The system file / folder dialog, shown by the local Bapu service (it can see real paths). */
export async function askPath(kind: "audio" | "lyrics" | "project" | "folder", initial?: string): Promise<Dialog> {
  const local = await desktopPick(kind, initial); // the desktop window has its own dialogs
  if (local === undefined) return api.post<Dialog>("/dialog", { kind, initial });
  if (local && kind === "audio") return { path: local, folder: suggestFolder(local) };
  return { path: local };
}

/** Default project folder: next to the song, named after it. */
const suggestFolder = (p: string) => {
  const sep = p.includes("\\") ? "\\" : "/";
  return `${dirName(p)}${sep}${stem(baseName(p))} - Bapu`;
};

/** A picked input: a real path from the system dialog, or a file dropped into the page. */
type Picked = { path?: string; file?: File; name: string };
const baseName = (p: string) => p.split(/[\\/]/).pop() ?? p;
const dirName = (p: string) => p.replace(/[\\/][^\\/]*$/, "");
const stem = (name: string) => name.replace(/\.[^.]+$/, "");

function FilePick({ kind, accept, picked, onPick, hint }: {
  kind: "audio" | "lyrics"; accept: string; picked: Picked | null; hint: string;
  onPick: (p: Picked, dialog?: Dialog) => void;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // desktop window: a dropped file arrives with its real path
  const pickRef = useRef(onPick);
  pickRef.current = onPick;
  useEffect(() => onDesktopDrop((paths) => {
    const p = paths.find((x) => !x.toLowerCase().endsWith(".bapu"));
    if (p) pickRef.current({ path: p, name: baseName(p) }, kind === "audio" ? { path: p, folder: suggestFolder(p) } : undefined);
  }), [kind]);
  async function browse() {
    try {
      const r = await askPath(kind, picked?.path ? dirName(picked.path) : undefined);
      if (r.path) onPick({ path: r.path, name: baseName(r.path) }, r);
    } catch {
      input.current?.click(); // no system dialog here: the browser's picker (the file is uploaded)
    }
  }
  return (
    <div className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0} onClick={browse}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); browse(); } }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onPick({ file: f, name: f.name });
      }}>
      <input ref={input} type="file" accept={accept} hidden onChange={(e) => {
        const f = e.target.files?.[0];
        if (f) onPick({ file: f, name: f.name });
      }} />
      {picked
        ? <><b style={{ fontWeight: 500 }}>{picked.name}</b>{picked.path && <span className="muted small mono">{dirName(picked.path)}</span>}</>
        : <span><u>点这里选择文件</u>，或者把文件拖进来</span>}
      <span className="muted small">{hint}</span>
    </div>
  );
}

/** Step 1: a new project (the song and where to keep the project), or open one. */
export function AudioStep() {
  const { job, open } = useJob();
  const [audio, setAudio] = useState<Picked | null>(null);
  const [folder, setFolder] = useState("");
  const [taken, setTaken] = useState<string | null>(null); // the folder already holds a project
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<JobSummary[]>([]);
  useEffect(() => {
    if (!job) api.get<JobSummary[]>("/jobs").then(setRecent).catch(() => {});
  }, [job]);

  if (job) {
    const sep = job.folder.includes("\\") ? "\\" : "/";
    return (
      <section className="card stepcard" aria-labelledby="h-audio">
        <h1 id="h-audio">工程</h1>
        <div className="kv">
          <span className="muted">原曲</span><b style={{ fontWeight: 500 }}>{job.sources.source}</b>
          <span className="muted">工程文件</span><span className="mono small" style={{ overflowWrap: "anywhere" }}>{job.folder}{sep}{job.project_file}</span>
        </div>
        <p className="muted small" style={{ margin: 0 }}>要处理别的歌，点右上角“新建 / 打开”。</p>
        <Nav next={() => go("lyrics")} />
      </section>
    );
  }

  async function openProject(path?: string) {
    setError(null);
    try {
      const p = path ?? (await askPath("project")).path;
      if (!p) return;
      const d = await api.post<JobDetail>("/projects/open", { path: p });
      open(d.id);
      window.location.hash = "/";
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function create() {
    setBusy(true);
    setError(null);
    setTaken(null);
    try {
      const form = new FormData();
      if (audio!.path) form.set("audio_path", audio!.path);
      else form.set("audio", audio!.file!);
      form.set("folder", folder.trim());
      const created = await api.form<JobDetail>("/jobs", form);
      open(created.id);
      go("lyrics");
    } catch (e) {
      setError((e as Error).message);
      const d = e instanceof ApiError && e.status === 409 ? (e.detail as { folder?: string } | undefined) : undefined;
      setTaken(d?.folder ?? null);
    } finally {
      setBusy(false);
    }
  }

  const alternative = audio?.path ? suggestFolder(audio.path) : null;
  const sep = folder.includes("/") && !folder.includes("\\") ? "/" : "\\";
  return (
    <section className="card stepcard" aria-labelledby="h-audio">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <h1 id="h-audio">新建工程</h1>
        <button type="button" className="btn small" onClick={() => openProject()}>打开工程…</button>
      </div>
      <p className="lead">选一首歌的录音或视频（带伴奏的原曲就行）。人声、伴奏会自动分开。</p>
      <FilePick kind="audio" accept="audio/*,video/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.mp4,.mov,.mkv,.webm"
        picked={audio} hint="WAV、FLAC、MP3、M4A，或者 MP4 等视频文件（只取声音）"
        onPick={(p, d) => { setAudio(p); if (d?.folder) setFolder(d.folder); }} />

      <label className="field">工程文件夹
        <span className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
          <input type="text" className="mono" style={{ flex: 1 }} value={folder} onChange={(e) => setFolder(e.target.value)}
            placeholder={audio?.file ? "拖进来的文件不知道原来的位置，请选一个文件夹" : "选好原曲后，默认放在原曲旁边"} />
          <button type="button" className="btn" onClick={async () => {
            try {
              const r = await askPath("folder", folder || (audio?.path ? dirName(audio.path) : undefined));
              if (r.path) setFolder(r.path);
            } catch (e) {
              setError((e as Error).message);
            }
          }}>选择…</button>
        </span>
      </label>
      {folder.trim() && audio && (
        <p className="muted small" style={{ margin: "-8px 0 0", overflowWrap: "anywhere" }}>
          会建 <span className="mono">{folder.trim()}{sep}{stem(audio.name)}.bapu</span>，原曲复制一份进去，处理结果也都放在这个文件夹里。以后双击 .bapu 就能打开。
        </p>
      )}
      {error && (
        <div className="notice bad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span>{error}</span>
          {taken && (
            <span className="row" style={{ gap: 8 }}>
              <button type="button" className="btn small" onClick={() => openProject(taken)}>打开这个工程</button>
              {alternative && alternative.toLowerCase() !== taken.toLowerCase() && (
                <button type="button" className="btn small" onClick={() => { setFolder(alternative); setError(null); setTaken(null); }}>
                  改用 {baseName(alternative)}
                </button>
              )}
            </span>
          )}
        </div>
      )}
      <Nav next={create} disabled={!audio || !folder.trim()} busy={busy} nextLabel="建好工程，下一步" />

      {recent.length > 0 && (
        <div className="recent">
          <span className="muted small">最近的工程</span>
          {recent.slice(0, 6).map((j) => (
            <button key={j.id} type="button" className="recent-item" title={j.folder} onClick={() => { open(j.id); window.location.hash = "/"; }}>
              <b>{j.name}</b><span className="muted small mono">{j.folder}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

/** Step 2: optional reference lyrics. */
export function LyricsStep() {
  const { job, refresh } = useJob();
  const [picked, setPicked] = useState<Picked | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!job) return null;

  async function next() {
    setError(null);
    if (picked) {
      setBusy(true);
      try {
        const form = new FormData();
        if (picked.path) form.set("lyrics_path", picked.path);
        else form.set("lyrics", picked.file!);
        await api.form(`/jobs/${encodeURIComponent(job!.id)}/lyrics`, form);
        await refresh();
      } catch (e) {
        setError((e as Error).message);
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    go("lang");
  }

  return (
    <section className="card stepcard" aria-labelledby="h-lyrics">
      <h1 id="h-lyrics">参考歌词</h1>
      <p className="lead">
        歌词由模型从录音里听写出来。手上有歌词文件的话也导入，用来校正听写的用字；没有就直接下一步。
      </p>
      {job.has_lyrics && !picked && <div className="notice">已导入参考歌词。要换的话在下面重新选一个文件。</div>}
      <FilePick kind="lyrics" accept=".ass,.ssa,.lrc,.txt" picked={picked} onPick={setPicked} hint=".ass、.lrc、.txt 字幕或歌词文件" />
      <p className="muted small" style={{ margin: 0 }}>歌词只在本机处理，会复制一份到工程文件夹。</p>
      {error && <div className="notice bad">{error}</div>}
      <Nav back={() => go("audio")} next={next} busy={busy}
        nextLabel={picked || job.has_lyrics ? "下一步" : "没有歌词，下一步"} />
    </section>
  );
}

const LANGS = [["yue", "粤语"], ["zh", "普通话"], ["en", "英语"]] as const;

/** Step 3: languages sung in the song; "start" runs the pipeline. */
export function LangStep() {
  const { job, refresh } = useJob();
  const [langs, setLangs] = useState<string[]>(((job?.params?.lyrics?.langs as string[] | undefined) ?? ["yue", "en"]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!job) return null;
  const running = job.state === "running" || job.state === "queued";

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/jobs/${encodeURIComponent(job!.id)}/run`, { set: { lyrics: { langs }, dictate: { langs } } });
    } catch (e) {
      // missing models are downloaded on the next page; anything else is shown here
      if (!(e instanceof ApiError && e.status === 409)) {
        setError((e as Error).message);
        setBusy(false);
        return;
      }
    }
    await refresh();
    setBusy(false);
    go("run");
  }

  return (
    <section className="card stepcard" aria-labelledby="h-lang">
      <h1 id="h-lang">歌里唱了哪些语言</h1>
      <p className="lead">可以多选。听写和读音会按这里的选择来。</p>
      <div className="choices" role="group" aria-label="语言">
        {LANGS.map(([id, label]) => {
          const on = langs.includes(id);
          return (
            <button key={id} type="button" className={`choice ${on ? "on" : ""}`} aria-pressed={on}
              onClick={() => setLangs(on ? (langs.length > 1 ? langs.filter((x) => x !== id) : langs) : [...langs, id])}>
              <span className="box" aria-hidden="true">{on ? "✓" : ""}</span>{label}
            </button>
          );
        })}
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        {langs.includes("yue") && langs.includes("zh")
          ? "粤语和普通话都选时，每句先按粤语处理，审查时可以逐句改成普通话。"
          : "英文单词总是按英语处理。"}
      </p>
      {error && <div className="notice bad">{error}</div>}
      <Nav back={() => go("lyrics")} next={start} busy={busy} disabled={running}
        nextLabel={started(job) ? "按这个设置重新处理" : "开始处理"} />
    </section>
  );
}

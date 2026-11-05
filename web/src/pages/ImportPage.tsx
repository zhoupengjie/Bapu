import { useEffect, useRef, useState, type DragEvent } from "react";
import { ApiError, api, type JobDetail } from "../api";
import { go, started, useJob } from "../App";
import { desktopPick, onDesktopDrop } from "../desktop";
import { t } from "../i18n";
import { Icon } from "../icons";

/** A picked input: a real path (system dialog, desktop drop) or a file dropped into the page. */
type Picked = { path?: string; file?: File; name: string };
type Kind = "audio" | "lyrics";

const LANGS = [["yue", "粤语"], ["zh", "普通话"], ["en", "英语"]] as const;
const DEFAULT_LANGS = ["yue", "en"];
const ACCEPT: Record<Kind, string> = {
  audio: "audio/*,video/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.mp4,.mov,.mkv,.webm",
  lyrics: ".ass,.ssa,.lrc,.txt",
};
const LYRICS_FILE = /\.(ass|ssa|lrc|txt)$/i;

const baseName = (p: string) => p.split(/[\\/]/).pop() ?? p;
const dirName = (p: string) => p.replace(/[\\/][^\\/]*$/, "");
const stem = (name: string) => name.replace(/\.[^.]+$/, "");

/** The project folder: next to the song, named after it (`(2)`, `(3)` … when taken). */
function folderFor(path: string, n: number) {
  const sep = path.includes("\\") ? "\\" : "/";
  return `${dirName(path)}${sep}${stem(baseName(path))} - Bapu${n > 1 ? ` (${n})` : ""}`;
}

/** The folder of an existing project when creating one ran into it (409). */
function takenFolder(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  const f = (e.detail as { folder?: unknown } | undefined)?.folder;
  return typeof f === "string" ? f : null;
}

// An error from starting a project that was just created: the page remounts on the new project.
let carried: string | null = null;

export default function ImportPage() {
  const { job, refresh, open } = useJob();
  const locked = !!job && started(job); // already processed: shown, not edited
  const [audio, setAudio] = useState<Picked | null>(null);
  const [lyrics, setLyrics] = useState<Picked | null>(null);
  const [langs, setLangs] = useState<string[]>(() => {
    const l = job?.params?.lyrics?.langs;
    return Array.isArray(l) && l.length ? (l as string[]) : DEFAULT_LANGS;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(() => carried);
  useEffect(() => {
    carried = null;
  }, []);
  const [taken, setTaken] = useState<{ folder: string; n: number } | null>(null);
  const [over, setOver] = useState<Kind | null>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const lyricsInput = useRef<HTMLInputElement>(null);

  const take = (kind: Kind, p: Picked) => {
    setError(null);
    setTaken(null);
    (kind === "audio" ? setAudio : setLyrics)(p);
  };
  const takeRef = useRef(take);
  takeRef.current = take;

  // desktop window: dropped files arrive with their real paths; sort them by type
  const audioFixed = !!job;
  useEffect(() => {
    if (locked) return;
    return onDesktopDrop((paths) => {
      const files = paths.filter((p) => !p.toLowerCase().endsWith(".bapu"));
      const lyr = files.find((p) => LYRICS_FILE.test(p));
      const aud = files.find((p) => !LYRICS_FILE.test(p));
      if (aud && !audioFixed) takeRef.current("audio", { path: aud, name: baseName(aud) });
      if (lyr) takeRef.current("lyrics", { path: lyr, name: baseName(lyr) });
    });
  }, [locked, audioFixed]);

  async function browse(kind: Kind) {
    const input = kind === "audio" ? audioInput : lyricsInput;
    let path: string | null;
    try {
      const local = await desktopPick(kind);
      if (local !== undefined) path = local;
      else {
        try {
          path = (await api.post<{ path: string | null }>("/dialog", { kind })).path;
        } catch {
          input.current?.click(); // no system dialog here: the browser's picker (the file is uploaded)
          return;
        }
      }
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    if (path) take(kind, { path, name: baseName(path) });
  }

  const dropProps = (kind: Kind) => ({
    onDragOver: (e: DragEvent) => {
      e.preventDefault();
      setOver(kind);
    },
    onDragLeave: () => setOver(null),
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      setOver(null);
      const f = e.dataTransfer.files?.[0];
      if (f) take(kind, { file: f, name: f.name });
    },
    style: over === kind ? { borderColor: "var(--accent)" } : undefined,
  });

  const runJob = async (id: string) => {
    try {
      await api.post(`/jobs/${encodeURIComponent(id)}/run`, { set: { lyrics: { langs }, dictate: { langs } } });
    } catch (e) {
      // missing models are fetched from the processing page; anything else is an error
      if (!(e instanceof ApiError && e.status === 409)) throw e;
    }
  };

  /** Create the project next to the song (folder `n`; `again`: skip taken folders), then start it. */
  async function create(n: number, again: boolean) {
    const a = audio!;
    let created: JobDetail | null = null;
    while (!created) {
      const form = new FormData();
      if (a.path) {
        form.set("audio_path", a.path);
        form.set("folder", folderFor(a.path, n));
      } else form.set("audio", a.file!);
      if (lyrics?.path) form.set("lyrics_path", lyrics.path);
      else if (lyrics?.file) form.set("lyrics", lyrics.file);
      try {
        created = await api.form<JobDetail>("/jobs", form);
      } catch (e) {
        const folder = takenFolder(e);
        if (!folder) throw e;
        if (!again || n >= 99) {
          setTaken({ folder, n });
          return;
        }
        n += 1;
      }
    }
    try {
      await runJob(created.id);
    } catch (e) {
      carried = (e as Error).message;
    }
    open(created.id); // the flow page takes over on the new project
    go("flow");
  }

  async function act(f: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await f();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const start = () => act(async () => {
    setTaken(null);
    if (!job) return create(1, false);
    if (lyrics) {
      const form = new FormData();
      if (lyrics.path) form.set("lyrics_path", lyrics.path);
      else form.set("lyrics", lyrics.file!);
      await api.form(`/jobs/${encodeURIComponent(job.id)}/lyrics`, form);
    }
    await runJob(job.id);
    await refresh();
    go("flow");
  });

  const openTaken = () => act(async () => {
    const d = await api.post<JobDetail>("/projects/open", { path: taken!.folder });
    open(d.id);
    window.location.hash = "/";
  });

  const another = () => act(async () => {
    const n = taken!.n + 1;
    setTaken(null);
    await create(n, true);
  });

  const newProject = () => {
    if (job?.state === "running" && !window.confirm(t("这个工程还在处理，离开后它会在后台继续跑完。确定吗？"))) return;
    open(null);
    go("flow");
  };

  const toggle = (id: string) => {
    const on = langs.includes(id);
    if (on && langs.length === 1) return; // at least one stays
    setLangs(on ? langs.filter((x) => x !== id) : [...langs, id]);
  };

  const hasLyrics = !!lyrics || !!job?.has_lyrics;
  const audioName = job ? baseName(job.sources?.source ?? job.name) : audio?.name;

  return (
    <section className="card narrow" aria-labelledby="h-import">
      <div>
        <h1 id="h-import">{t("导入")}</h1>
        <p className="lead">{t("原曲、歌词、语言，选好就能开始")}</p>
      </div>

      <div className="sec">
        <h2>{t("原曲")}</h2>
        {audioFixed ? (
          <div className="drop filled">
            <span style={{ color: "var(--accent)" }}><Icon name="music" size={22} /></span>
            <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}><b style={{ fontWeight: 500 }}>{audioName}</b></span>
          </div>
        ) : audio ? (
          <button type="button" className="drop filled" disabled={busy} onClick={() => browse("audio")} {...dropProps("audio")}>
            <span style={{ color: "var(--accent)" }}><Icon name="music" size={22} /></span>
            <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}><b style={{ fontWeight: 500 }}>{audio.name}</b></span>
            <span className="link">{t("换一个")}</span>
          </button>
        ) : (
          <button type="button" className="drop" disabled={busy} onClick={() => browse("audio")} {...dropProps("audio")}>
            <Icon name="down" size={26} />
            <b style={{ fontWeight: 500 }}>{t("选择文件，或拖进来")}</b>
            <span className="muted" style={{ fontSize: 14 }}>{t("WAV、MP3、MP4…")}</span>
          </button>
        )}
        <input ref={audioInput} type="file" accept={ACCEPT.audio} hidden onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) take("audio", { file: f, name: f.name });
          e.target.value = "";
        }} />
      </div>

      <div className="sec">
        <h2>{t("参考歌词")} <span className="muted" style={{ fontWeight: 400, fontSize: 14 }}>· {t("可选")}</span></h2>
        {locked ? (
          <div className={`drop slim ${hasLyrics ? "filled" : ""}`}>
            {hasLyrics
              ? <><span style={{ color: "var(--accent)" }}><Icon name="text" size={20} /></span><span style={{ flex: 1 }}>{t("已导入歌词")}</span></>
              : <><span className="muted"><Icon name="text" size={20} /></span><span style={{ flex: 1 }} className="muted">{t("没有")}</span></>}
          </div>
        ) : (
          <button type="button" className={`drop slim ${hasLyrics ? "filled" : ""}`} disabled={busy}
            onClick={() => browse("lyrics")} {...dropProps("lyrics")}>
            {hasLyrics
              ? <><span style={{ color: "var(--accent)" }}><Icon name="text" size={20} /></span><span style={{ flex: 1 }}>{t("已导入歌词")}</span><span className="link">{t("换一个")}</span></>
              : <><span className="muted"><Icon name="text" size={20} /></span><span style={{ flex: 1 }} className="muted">{t("有歌词文件的话，用来校正听写的字")}</span><span className="link">{t("选择")}</span></>}
          </button>
        )}
        <input ref={lyricsInput} type="file" accept={ACCEPT.lyrics} hidden onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) take("lyrics", { file: f, name: f.name });
          e.target.value = "";
        }} />
      </div>

      <div className="sec">
        <h2 id="h-langs">{t("唱了哪些语言")}</h2>
        <div className="choices" role="group" aria-labelledby="h-langs">
          {LANGS.map(([id, label]) => {
            const on = langs.includes(id);
            return (
              <button key={id} type="button" className={`choice ${on ? "on" : ""}`} aria-pressed={on}
                disabled={locked || busy} onClick={() => toggle(id)}>
                <span className="box" aria-hidden="true">{on ? "✓" : ""}</span>{t(label)}
              </button>
            );
          })}
        </div>
      </div>

      {taken && (
        <div className="notice" style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", justifyContent: "space-between" }}>
          <span>{t("这首歌已经有工程了")}</span>
          <span style={{ display: "flex", gap: 8 }}>
            <button type="button" className="btn" disabled={busy} onClick={openTaken}>{t("打开它")}</button>
            <button type="button" className="btn" disabled={busy} onClick={another}>{t("另建一个")}</button>
          </span>
        </div>
      )}
      {error && <div className="notice bad" role="alert">{t(error)}</div>}

      <div className="foot">
        <span />
        {locked ? (
          <button type="button" className="btn primary" onClick={newProject}>{t("新建工程")}</button>
        ) : (
          <button type="button" className="btn primary" disabled={busy || (!job && !audio)} onClick={start}>
            {busy && <span className="spin inline" aria-hidden="true" />}{t("开始处理")}
          </button>
        )}
      </div>
    </section>
  );
}

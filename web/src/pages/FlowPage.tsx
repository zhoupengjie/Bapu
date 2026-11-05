import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, apiUrl, follow, type JobDetail, type StageInfo, type SvTrack } from "../api";
import FlowGraph, { type Anchor, type NodeId, type NodeState, type NodeView, type Port, type Side, type Socket, type Wire } from "../components/FlowGraph";
import { go, started, useJob } from "../App";
import { desktopPick, inDesktop, onDesktopDrop, revealInFolder } from "../desktop";
import { STAGE_LABEL } from "../env";
import { t } from "../i18n";
import { Icon } from "../icons";

// 流程: the graph (输入 column → 分离 → 音符 → 歌词) and the selected node's panel. With no
// project yet the graph is empty and choosing the song creates one (next to the song);
// every node runs the pipeline up to its last step (earlier steps come from the cache),
// can take a file in place of an input, and can save what it made next to the song.

const NODE_STAGES: Record<NodeId, string[]> = {
  separate: ["separate", "lead", "tempo"], notes: ["transcribe", "splits"], lyrics: ["dictate", "lyrics", "align", "assign"],
};
const NODE_LAST: Record<NodeId, string> = { separate: "tempo", notes: "splits", lyrics: "assign" };
const LANGS = [["yue", "粤语"], ["zh", "普通话"], ["ja", "日语"], ["en", "英语"]] as const;
const CHINESE = ["yue", "zh"]; // kanji look the same in Chinese and Japanese: Japanese goes with English only
const DEFAULT_LANGS = ["yue", "en"];
const AUDIO = "accompaniment,lead_dry";
const GRID_KEY = "bapu.exportGrid"; // the grid chosen on the 导出 page applies to the .svp saved here too
const MORE_LABEL: Record<string, string> = { lead: "提取主唱", splits: "合并音符" };
const label = (stage: string, title: string) => t(STAGE_LABEL[stage] ?? MORE_LABEL[stage] ?? title);
const LYRICS_FILE = /\.(ass|ssa|lrc|txt)$/i;
const NOTES_FILE = /\.(svp|mid|midi)$/i;
const ACCEPT = {
  audio: "audio/*,video/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.mp4,.mov,.mkv,.webm",
  lyrics: ".ass,.ssa,.lrc,.txt",
  notes: ".svp,.mid,.midi",
};

/** The graph before a project exists: nothing given, nothing made. */
const NO_JOB: JobDetail = {
  id: "", name: "", folder: "", project_file: null, state: "idle", stage: null, fraction: null, error: null,
  has_score: false, attention: null, has_lyrics: false, has_notes: false, exported_at: null, export_stale: false,
  stages: [], params: {}, sources: {}, notes: null, exports: [],
};

// languages chosen before the project existed, handed to the page that opens on it
let carriedLangs: string[] | null = null;

function savedGrid(): number {
  try {
    const v = localStorage.getItem(GRID_KEY);
    return v === null ? 16 : Number(v) || 0;
  } catch {
    return 16;
  }
}

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

type Kind = "audio" | "lyrics" | "notes";
/** A chosen file: a real path (system dialog, desktop drop) or a file picked in the browser (uploaded). */
type Picked = { path?: string; file?: File; name: string };
type Live = { stage: string; fraction: number | null };
type Written = { format: string; name: string; size: number };
/** The menu under a clicked socket: give a file to an input, save or run for an output. */
type Menu = { node: Port; side: Side; socket: string; anchor: Anchor };
type Item = { label: string; primary?: boolean; disabled?: boolean; keep?: boolean; run: () => void };

export default function FlowPage() {
  const { job, refresh, open } = useJob();
  const j = job ?? NO_JOB;
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [selected, setSelected] = useState<NodeId | null>(null);
  const [langs, setLangs] = useState<string[]>(() => {
    const l = job?.params?.lyrics?.langs;
    if (Array.isArray(l) && l.length) return l as string[];
    const carried = carriedLangs;
    carriedLangs = null;
    return carried ?? DEFAULT_LANGS;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsEnv, setNeedsEnv] = useState(false);
  const [files, setFiles] = useState<Written[]>([]);
  // before the project exists: the lyrics chosen so far, the song whose folder was taken
  const [preLyrics, setPreLyrics] = useState<Picked | null>(null);
  const [taken, setTaken] = useState<{ audio: Picked; folder: string; n: number } | null>(null);
  // a notes file waiting for a track choice, and the tracks to choose from
  const [pending, setPending] = useState<Picked | null>(null);
  const [tracks, setTracks] = useState<SvTrack[] | null>(null);
  const [track, setTrack] = useState(1);
  const inputRefs = {
    audio: useRef<HTMLInputElement>(null), lyrics: useRef<HTMLInputElement>(null), notes: useRef<HTMLInputElement>(null),
  };
  const [menu, setMenu] = useState<Menu | null>(null);
  const menuEl = useRef<HTMLDivElement>(null);
  const id = job?.id ?? "";
  const enc = encodeURIComponent(id);

  useEffect(() => {
    api.get<StageInfo[]>("/stages").then(setStages).catch(() => {});
  }, []);

  useEffect(() => {
    if (!id) return;
    return follow(`/jobs/${enc}/events`, (ev) => {
      if (ev.type === "progress") {
        setLive({ stage: ev.stage as string, fraction: (ev.fraction as number | undefined) ?? null });
        if (ev.kind === "done" || ev.kind === "skip") refresh();
      } else if (ev.type === "state") {
        if (ev.state !== "running") setLive(null);
        refresh();
      }
    });
  }, [id, enc, refresh]);

  // the socket menu closes on Escape or a click anywhere else
  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      const el = e.target as Element | null;
      if (!menuEl.current?.contains(el) && !el?.closest?.(".flow-socket")) setMenu(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const act = useCallback(async (f: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await f();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const run = useCallback((until?: string) => act(async () => {
    if (!job) throw new Error("先选原曲");
    setNeedsEnv(false);
    try {
      await api.post(`/jobs/${enc}/run`, { until, set: { lyrics: { langs }, dictate: { langs } } });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNeedsEnv(true);
        return;
      }
      throw e;
    }
    await refresh();
  }), [act, enc, job, langs, refresh]);

  /** The 只… buttons: one node on its own, so its inputs must already be there. */
  const runOnly = useCallback((n: NodeId) => {
    const have = (stage: string) => j.stages.find((s) => s.stage === stage)?.state === "fresh";
    if (n === "notes" && !have("lead")) return setError("先运行人声分离");
    if (n === "lyrics" && !(have("lead") && have("tempo") && have("splits"))) return setError(have("lead") ? "先推理音符音高" : "先运行人声分离");
    return run(n === "lyrics" ? undefined : NODE_LAST[n]);
  }, [j.stages, run]);

  const stop = useCallback(() => act(async () => {
    await api.post(`/jobs/${enc}/cancel`);
    await refresh();
  }), [act, enc, refresh]);

  const exportNow = useCallback((formats: string[]) => act(async () => {
    const r = await api.post<{ files: Written[] }>(`/jobs/${enc}/export`, { formats, audio: AUDIO, grid: savedGrid() });
    setFiles(r.files);
    await refresh();
  }), [act, enc, refresh]);

  /** Create the project next to the song (folder `n`; `again`: skip taken folders), then open it. */
  const createProject = useCallback((a: Picked, n: number, again: boolean) => act(async () => {
    setTaken(null);
    let created: JobDetail | null = null;
    while (!created) {
      const form = new FormData();
      if (a.path) {
        form.set("audio_path", a.path);
        form.set("folder", folderFor(a.path, n));
      } else if (a.file) form.set("audio", a.file);
      if (preLyrics?.path) form.set("lyrics_path", preLyrics.path);
      else if (preLyrics?.file) form.set("lyrics", preLyrics.file);
      try {
        created = await api.form<JobDetail>("/jobs", form);
      } catch (e) {
        const folder = takenFolder(e);
        if (!folder) throw e;
        if (!again || n >= 99) {
          setTaken({ audio: a, folder, n });
          return;
        }
        n += 1;
      }
    }
    carriedLangs = langs;
    open(created.id); // the page remounts on the new project
  }), [act, langs, open, preLyrics]);

  /** Send a lyrics / notes file to the project; a 409 means several SV tracks have notes and one must be chosen. */
  const submit = useCallback((kind: "lyrics" | "notes", p: Picked, chosen?: number) => act(async () => {
    const form = new FormData();
    if (p.path) form.set(`${kind}_path`, p.path);
    else if (p.file) form.set(kind, p.file);
    if (chosen != null) form.set("track", String(chosen));
    try {
      await api.form(`/jobs/${enc}/${kind}`, form);
    } catch (e) {
      const list = e instanceof ApiError && e.status === 409 ? (e.detail as { tracks?: SvTrack[] } | undefined)?.tracks : undefined;
      if (!list?.length) throw e;
      setPending(p);
      setTracks(list);
      setTrack(list.find((x) => x.notes > 0)?.index ?? 1);
      return;
    }
    setPending(null);
    setTracks(null);
    await refresh();
  }), [act, enc, refresh]);

  /** A chosen file goes where it belongs: the song makes the project, the rest attach to it. */
  const take = useCallback((kind: Kind, p: Picked) => {
    setError(null);
    if (kind === "audio") {
      if (!job) createProject(p, 1, false);
    } else if (kind === "lyrics" && !job) setPreLyrics(p);
    else if (job) submit(kind, p);
    else setError("先选原曲");
  }, [createProject, job, submit]);
  const takeRef = useRef(take);
  takeRef.current = take;

  const pick = useCallback(async (kind: Kind) => {
    let path: string | null;
    try {
      const local = await desktopPick(kind);
      if (local !== undefined) path = local;
      else {
        try {
          path = (await api.post<{ path: string | null }>("/dialog", { kind })).path;
        } catch {
          inputRefs[kind].current?.click(); // no system dialog here: the browser's picker (the file is uploaded)
          return;
        }
      }
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    if (path) take(kind, { path, name: baseName(path) });
  }, [take]); // eslint-disable-line react-hooks/exhaustive-deps

  // desktop window: dropped files arrive with their real paths; sort them by type
  useEffect(() => onDesktopDrop((paths) => {
    const files = paths.filter((p) => !p.toLowerCase().endsWith(".bapu"));
    const lyr = files.find((p) => LYRICS_FILE.test(p));
    const notes = files.find((p) => NOTES_FILE.test(p));
    const aud = files.find((p) => !LYRICS_FILE.test(p) && !NOTES_FILE.test(p));
    if (aud) takeRef.current("audio", { path: aud, name: baseName(aud) });
    if (lyr) takeRef.current("lyrics", { path: lyr, name: baseName(lyr) });
    if (notes) takeRef.current("notes", { path: notes, name: baseName(notes) });
  }), []);

  const clear = useCallback((kind: "lyrics" | "notes") => act(async () => {
    if (!job) {
      setPreLyrics(null);
      return;
    }
    await api.del(`/jobs/${enc}/${kind}`);
    setPending(null);
    setTracks(null);
    await refresh();
  }), [act, enc, job, refresh]);

  const openTaken = () => act(async () => {
    const d = await api.post<JobDetail>("/projects/open", { path: taken!.folder });
    setTaken(null);
    open(d.id);
  });
  const another = () => {
    const tk = taken!;
    setTaken(null);
    createProject(tk.audio, tk.n + 1, true);
  };

  // -- what the graph shows ----------------------------------------------------------------
  const running = j.state === "running" || j.state === "queued";
  const idle = !!job && !running && !busy;
  const rowOf = (stage: string) => j.stages.find((s) => s.stage === stage);
  const fresh = (stage: string) => rowOf(stage)?.state === "fresh";
  const metric = (stage: string, key: string): number | string | null => {
    const v = rowOf(stage)?.metrics?.[key];
    return typeof v === "number" || typeof v === "string" ? v : null;
  };
  const nowStage = j.state === "running" ? live?.stage ?? j.stage : null;
  const info = (name: string) => stages.find((s) => s.name === name);
  const stepName = (name: string | null) => (name ? label(name, info(name)?.title ?? name) : "");

  const nodeState = (n: NodeId): NodeState => {
    if (nowStage && NODE_STAGES[n].includes(nowStage)) return "running";
    if (j.state === "failed" && j.stage && NODE_STAGES[n].includes(j.stage)) return "failed";
    const states = NODE_STAGES[n].map((s) => rowOf(s)?.state ?? "missing").filter((s) => s !== "skipped");
    if (states.length && states.every((s) => s === "fresh")) return "done";
    if (states.every((s) => s === "missing")) return "empty";
    return "stale";
  };
  const status = (n: NodeId) => (nodeState(n) === "running" ? stepName(nowStage) : undefined);

  const bpm = metric("tempo", "bpm");
  const noteCount = metric("splits", "notes");
  const notesFile = j.notes ? j.sources?.notes ?? j.notes.from : undefined;
  const lyricsName = job ? (j.has_lyrics ? j.sources?.lyrics : undefined) : preLyrics?.name;
  const langName = (code: string) => t(LANGS.find(([c]) => c === code)?.[1] ?? code);
  const langNames = langs.map(langName).join("、");

  // the 输入 column: what the user gives, wired to the node inputs it feeds
  const inputs: Socket[] = [
    { id: "source", label: t("原曲"), on: !!j.sources?.source, kind: "data", file: j.sources?.source, hint: t("选一首歌就新建工程") },
    { id: "notes", label: t("音符文件"), on: !!j.notes, kind: "notes", optional: true, file: notesFile, hint: job ? t("SV 工程或 MIDI，代替转谱") : t("先选原曲") },
    { id: "lyrics", label: t("参考歌词"), on: !!lyricsName, kind: "data", optional: true, file: lyricsName, hint: t("字幕或歌词文件，校正听写") },
    { id: "lang", label: t("歌曲语言"), on: true, kind: "data", file: langNames },
  ];
  const nodes: NodeView[] = [
    {
      id: "separate", title: t("人声分离"), steps: t("步骤 1–3"), state: nodeState("separate"), status: status("separate"),
      inputs: [{ id: "source", label: t("原曲"), on: !!j.sources?.source, kind: "data" }],
      outputs: [
        { id: "lead_dry", label: t("主唱干声"), on: fresh("lead"), kind: "audio" },
        { id: "accompaniment", label: t("伴奏"), on: fresh("separate"), kind: "audio" },
        { id: "tempo", label: bpm != null && fresh("tempo") ? t("拍速 · {bpm}", { bpm }) : t("拍速"), on: fresh("tempo"), kind: "data" },
      ],
    },
    {
      id: "notes", title: t("音符音高推理"), steps: t("步骤 4–5"), state: nodeState("notes"), status: status("notes"),
      inputs: [
        { id: "lead_dry", label: t("主唱干声"), on: fresh("lead"), kind: "audio" },
        { id: "file", label: t("音符文件"), on: !!j.notes, kind: "notes", optional: true, file: notesFile },
      ],
      outputs: [{ id: "notes", label: noteCount != null && fresh("splits") ? t("音符 · {n}", { n: noteCount }) : t("音符"), on: fresh("splits"), kind: "notes" }],
    },
    {
      id: "lyrics", title: t("歌词填词推理"), steps: t("步骤 6–9"), state: nodeState("lyrics"), status: status("lyrics"),
      inputs: [
        { id: "notes", label: t("音符"), on: fresh("splits"), kind: "notes" },
        { id: "lead_dry", label: t("主唱干声"), on: fresh("lead"), kind: "audio" },
        { id: "tempo", label: t("拍速"), on: fresh("tempo"), kind: "data" },
        { id: "ref", label: t("参考歌词"), on: !!lyricsName, kind: "data", optional: true, file: lyricsName },
        { id: "lang", label: t("歌曲语言"), on: true, kind: "data", file: langNames },
      ],
      outputs: [{ id: "score", label: t("乐谱"), on: fresh("assign") && j.has_score, kind: "notes" }],
    },
  ];
  const wires: Wire[] = [
    { from: ["inputs", "source"], to: ["separate", "source"], kind: "data", on: !!j.sources?.source },
    { from: ["inputs", "notes"], to: ["notes", "file"], kind: "notes", on: !!j.notes },
    { from: ["inputs", "lyrics"], to: ["lyrics", "ref"], kind: "data", on: !!lyricsName },
    { from: ["inputs", "lang"], to: ["lyrics", "lang"], kind: "data", on: true },
    { from: ["separate", "lead_dry"], to: ["notes", "lead_dry"], kind: "audio", on: fresh("lead") },
    { from: ["separate", "lead_dry"], to: ["lyrics", "lead_dry"], kind: "audio", on: fresh("lead") },
    { from: ["separate", "tempo"], to: ["lyrics", "tempo"], kind: "data", on: fresh("tempo") },
    { from: ["notes", "notes"], to: ["lyrics", "notes"], kind: "notes", on: fresh("splits") },
  ];
  // the node to open first: the one running, else the first that is not done, else the last
  const current: NodeId = selected
    ?? nodes.find((n) => n.state === "running")?.id
    ?? nodes.find((n) => n.state !== "done")?.id
    ?? "lyrics";
  const node = nodes.find((n) => n.id === current)!;

  // -- headline and progress -----------------------------------------------------------------
  const active = j.stages.filter((s) => s.state !== "skipped");
  const total = active.length || 9;
  const freshCount = active.filter((s) => s.state === "fresh").length;
  const at = nowStage ? active.findIndex((s) => s.stage === nowStage) : -1;
  const fraction = live && live.stage === nowStage ? live.fraction : j.fraction;
  const pct = Math.round((running ? (at >= 0 ? Math.min(1, (at + (fraction ?? 0)) / total) : 0) : freshCount / total) * 100);
  const allDone = !running && active.length > 0 && freshCount === active.length;
  let headline: string;
  if (!job) headline = t("还没有工程");
  else if (j.state === "queued") headline = t("排队中…");
  else if (running) headline = nowStage ? t("正在{step}…", { step: stepName(nowStage) }) : t("准备中…");
  else if (allDone) headline = t("处理完成");
  else if (j.state === "failed") headline = t("处理出错了");
  else if (j.state === "cancelled" || started(j)) headline = t("已停止");
  else headline = t("还没开始");

  const desktop = inDesktop();
  const reveal = (name: string) => {
    const sep = j.folder.includes("\\") ? "\\" : "/";
    revealInFolder(`${j.folder.replace(/[\\/]+$/, "")}${sep}${name}`);
  };
  const toggleLang = (code: string) => {
    const on = langs.includes(code);
    if (on) {
      if (langs.length > 1) setLangs(langs.filter((x) => x !== code)); // at least one stays
      return;
    }
    const others = code === "ja" ? langs.filter((x) => !CHINESE.includes(x)) : CHINESE.includes(code) ? langs.filter((x) => x !== "ja") : langs;
    setLangs([...others, code]);
  };
  const yes = <span style={{ color: "var(--ok)", display: "inline-flex" }}><Icon name="check" size={16} /></span>;
  const no = <span className="muted">{t("还没有")}</span>;

  // -- what a clicked socket offers ---------------------------------------------------------------
  const menuFor = (m: Menu): { title: string; items: Item[] } => {
    const runTo = (n: NodeId, text = t("运行到这里")): Item => ({ label: text, primary: true, disabled: !idle, run: () => run(n === "lyrics" ? undefined : NODE_LAST[n]) });
    const open_: Item[] = desktop && job ? [{ label: t("打开文件夹"), run: () => revealInFolder(j.folder) }] : [];
    const notYet = (what: string) => `${what} · ${t("还没有")}`;
    const fromNode = (what: string, n: NodeId, ok: boolean) => ({
      title: `${what} · ${t(n === "separate" ? "来自人声分离节点" : "来自音符音高推理节点")}`,
      items: ok
        ? [{ label: t(n === "separate" ? "看人声分离节点" : "看音符音高推理节点"), run: () => setSelected(n) }]
        : [runTo(n, t(n === "separate" ? "运行人声分离" : "运行音符音高推理"))],
    });
    // an item of the 输入 column offers the same as the node socket it feeds
    const COLUMN: Record<string, string> = { source: "separate/in/source", notes: "notes/in/file", lyrics: "lyrics/in/ref", lang: "lyrics/in/lang" };
    const key = m.node === "inputs" ? COLUMN[m.socket] ?? m.socket : `${m.node}/${m.side}/${m.socket}`;
    switch (key) {
      case "separate/in/source":
        if (!job) return { title: notYet(t("原曲")), items: [{ label: t("选择文件…"), primary: true, disabled: busy, run: () => pick("audio") }] };
        return { title: `${t("原曲")} · ${j.sources?.source ?? "—"}`, items: open_ };
      case "separate/out/lead_dry":
      case "separate/out/accompaniment": {
        const what = t(m.socket === "lead_dry" ? "主唱干声" : "伴奏");
        if (!fresh("lead")) return { title: notYet(what), items: [runTo("separate")] };
        return { title: what, items: [
          { label: t("存 wav"), disabled: !idle, run: () => exportNow(["wav"]) },
          { label: t("存 svp（只有音轨）"), disabled: !idle || !fresh("tempo"), run: () => exportNow(["svp-audio"]) },
          ...open_] };
      }
      case "separate/out/tempo":
        if (!fresh("tempo")) return { title: notYet(t("拍速")), items: [runTo("separate")] };
        return { title: `${t("拍速")} · ${bpm} BPM`, items: [
          { label: t("存 svp（只有音轨）"), disabled: !idle || !fresh("lead"), run: () => exportNow(["svp-audio"]) }] };
      case "notes/in/lead_dry":
      case "lyrics/in/lead_dry":
        return fromNode(t("主唱干声"), "separate", fresh("lead"));
      case "lyrics/in/tempo":
        return fromNode(t("拍速"), "separate", fresh("tempo"));
      case "lyrics/in/notes":
        return fromNode(t("音符"), "notes", fresh("splits"));
      case "notes/in/file":
        if (!job) return { title: t("先选原曲"), items: [] };
        if (j.notes) {
          const bits = [notesFile ?? ""];
          if (j.notes.notes != null) bits.push(t("{n} 个音符", { n: j.notes.notes }));
          if (j.notes.track != null) bits.push(t("第 {n} 轨", { n: j.notes.track }));
          return { title: bits.join(" · "), items: [
            { label: t("换一个"), disabled: !idle, run: () => pick("notes") },
            { label: t("改回自动转谱"), disabled: !idle, run: () => clear("notes") }] };
        }
        return { title: t("自动转谱。也可以用 SV 工程里的音符，比如 SV2 从主唱干声提取的"),
          items: [{ label: t("选择 SV 工程或 MIDI…"), primary: true, disabled: !idle, run: () => pick("notes") }] };
      case "lyrics/in/ref":
        if (lyricsName) return { title: lyricsName, items: [
          { label: t("换一个"), disabled: running || busy, run: () => pick("lyrics") },
          { label: t("去掉"), disabled: running || busy, run: () => clear("lyrics") }] };
        return { title: t("有歌词文件的话，用来校正听写的字"),
          items: [{ label: t("选择歌词文件…"), primary: true, disabled: running || busy, run: () => pick("lyrics") }] };
      case "lyrics/in/lang":
        return { title: t("歌曲语言"), items: LANGS.map(([code, name]) => ({
          label: `${langs.includes(code) ? "✓" : "　"} ${t(name)}`, keep: true, disabled: running, run: () => toggleLang(code),
        })) };
      case "notes/out/notes":
        if (!fresh("splits")) return { title: notYet(t("音符")), items: [runTo("notes")] };
        return { title: noteCount != null ? t("{n} 个音符", { n: noteCount }) : t("音符"), items: [
          { label: t("存 svp（音符）"), disabled: !idle || !fresh("tempo"), run: () => exportNow(["svp-notes"]) },
          ...open_] };
      case "lyrics/out/score": {
        if (!(fresh("assign") && j.has_score)) return { title: notYet(t("乐谱")), items: [runTo("lyrics", t("运行"))] };
        const n = metric("assign", "notes");
        const bits = [t("乐谱")];
        if (n != null) bits.push(t("{n} 个音符", { n }));
        if (j.attention != null) bits.push(t("待确认 {n} 处", { n: j.attention }));
        return { title: bits.join(" · "), items: [
          { label: t("检查"), primary: true, run: () => go("check") },
          { label: t("存 svp"), disabled: !idle, run: () => exportNow(["svp"]) },
          { label: t("存 MIDI"), disabled: !idle, run: () => exportNow(["mid"]) },
          ...open_] };
      }
      default:
        return { title: key, items: [] };
    }
  };
  const openMenu = (node_: Port, side: Side, socket: string, anchor: Anchor) => {
    setMenu((cur) => (cur && cur.node === node_ && cur.side === side && cur.socket === socket ? null : { node: node_, side, socket, anchor }));
  };
  const shown = menu ? menuFor(menu) : null;

  // -- the selected node's panel -----------------------------------------------------------------
  let rows;
  let actions;
  let hint: string | null = null;
  if (node.id === "separate") {
    const conf = metric("tempo", "confidence");
    rows = (
      <div className="rows">
        <div className="row"><span className="k">{t("原曲")}</span>
          <span className="v">{j.sources?.source ?? <button type="button" className="link" disabled={busy} onClick={() => pick("audio")}>{t("选择文件…")}</button>}</span></div>
        <div className="row"><span className="k">{t("主唱干声")}</span><span className="v">{fresh("lead") ? yes : no}</span></div>
        <div className="row"><span className="k">{t("伴奏")}</span><span className="v">{fresh("separate") ? yes : no}</span></div>
        <div className="row"><span className="k">{t("拍速")}</span>
          <span className="v">{fresh("tempo") && bpm != null ? <>{bpm} BPM{conf === "low" && <span className="muted"> · {t("置信度低")}</span>}</> : no}</span></div>
      </div>
    );
    actions = (
      <>
        <button type="button" className={`btn ${node.state === "done" ? "" : "primary"}`} disabled={!idle} onClick={() => run(NODE_LAST.separate)}>{t("运行到这里")}</button>
        <button type="button" className="btn" disabled={!idle || !fresh("lead")} onClick={() => exportNow(["wav"])}>{t("存 wav")}</button>
        <button type="button" className="btn" disabled={!idle || !fresh("lead") || !fresh("tempo")} onClick={() => exportNow(["svp-audio"])}>{t("存 svp（只有音轨）")}</button>
        {desktop && job && <button type="button" className="btn" onClick={() => revealInFolder(j.folder)}>{t("打开文件夹")}</button>}
      </>
    );
    hint = t("存出的 .svp 只有伴奏和主唱干声两条音轨。在 SV2 里对主唱干声提取音高、保存，再到“音符音高推理”节点把那个工程选进来。");
  } else if (node.id === "notes") {
    const merged = metric("splits", "auto_merged");
    rows = (
      <>
        <div className="rows">
          <div className="row"><span className="k">{t("主唱干声")}</span><span className="v">{fresh("lead") ? yes : no}</span></div>
          <div className="row"><span className="k">{t("音符")}</span>
            <span className="v">{fresh("splits") && noteCount != null
              ? <>{t("{n} 个音符", { n: noteCount })}{typeof merged === "number" && merged > 0 && <span className="muted"> · {t("自动合并 {n} 处", { n: merged })}</span>}</>
              : no}</span></div>
        </div>
        {j.notes ? (
          <div className="drop slim filled">
            <span style={{ color: "var(--accent)" }}><Icon name="music" size={20} /></span>
            <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>
              <b style={{ fontWeight: 500 }}>{notesFile}</b>
              <span className="muted" style={{ fontSize: 13, marginLeft: 8 }}>
                {j.notes.notes != null && t("{n} 个音符", { n: j.notes.notes })}
                {j.notes.track != null && ` · ${t("第 {n} 轨", { n: j.notes.track })}`}
                {" · "}{t("转谱已跳过，用的是这个文件")}
              </span>
            </span>
            <button type="button" className="link" disabled={!idle} onClick={() => pick("notes")}>{t("换一个")}</button>
            <button type="button" className="link" disabled={!idle} onClick={() => clear("notes")}>{t("改回自动转谱")}</button>
          </div>
        ) : (
          <button type="button" className="drop slim" disabled={!idle} onClick={() => pick("notes")}>
            <span className="muted"><Icon name="music" size={20} /></span>
            <span style={{ flex: 1 }} className="muted">{t("自动转谱。也可以用 SV 工程里的音符，比如 SV2 从主唱干声提取的")}</span>
            <span className="link">{t("选择")}</span>
          </button>
        )}
        {tracks && pending && (
          <div className="notice" style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            <span style={{ flex: 1 }}>{t("这个工程里有几条音轨都有音符，选一条")}</span>
            <select value={track} onChange={(e) => setTrack(Number(e.target.value))} aria-label={t("音轨")}>
              {tracks.map((x) => (
                <option key={x.index} value={x.index} disabled={!x.notes}>
                  {x.index}. {x.name || t("（没有名字）")} · {t("{n} 个音符", { n: x.notes })}
                </option>
              ))}
            </select>
            <button type="button" className="btn" disabled={busy} onClick={() => submit("notes", pending, track)}>{t("用这条")}</button>
          </div>
        )}
      </>
    );
    actions = (
      <>
        <button type="button" className={`btn ${node.state === "done" ? "" : "primary"}`} disabled={!idle} onClick={() => run(NODE_LAST.notes)}>{t("运行到这里")}</button>
        <button type="button" className="btn" disabled={!idle || !fresh("splits") || !fresh("tempo")} onClick={() => exportNow(["svp-notes"])}>{t("存 svp（音符）")}</button>
      </>
    );
    hint = t("存出的 .svp 里的音符还没有歌词（先填 la），带两条音轨，可以先在 SV 里听一遍。");
  } else {
    const scoreNotes = metric("assign", "notes");
    rows = (
      <>
        {lyricsName ? (
          <div className="drop slim filled">
            <span style={{ color: "var(--accent)" }}><Icon name="text" size={20} /></span>
            <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}><b style={{ fontWeight: 500 }}>{lyricsName}</b></span>
            <button type="button" className="link" disabled={running || busy} onClick={() => pick("lyrics")}>{t("换一个")}</button>
            <button type="button" className="link" disabled={running || busy} onClick={() => clear("lyrics")}>{t("去掉")}</button>
          </div>
        ) : (
          <button type="button" className="drop slim" disabled={running || busy} onClick={() => pick("lyrics")}>
            <span className="muted"><Icon name="text" size={20} /></span>
            <span style={{ flex: 1 }} className="muted">{t("有歌词文件的话，用来校正听写的字")}</span>
            <span className="link">{t("选择")}</span>
          </button>
        )}
        <div className="sec" style={{ gap: 6 }}>
          <h2>{t("歌曲语言")}</h2>
          <div className="choices" role="group" aria-label={t("歌曲语言")}>
            {LANGS.map(([code, name]) => {
              const on = langs.includes(code);
              return (
                <button key={code} type="button" className={`choice ${on ? "on" : ""}`} aria-pressed={on} disabled={running || busy} onClick={() => toggleLang(code)}>
                  <span className="box" aria-hidden="true">{on ? "✓" : ""}</span>{t(name)}
                </button>
              );
            })}
          </div>
        </div>
        <div className="rows">
          <div className="row"><span className="k">{t("乐谱")}</span>
            <span className="v">{fresh("assign") && j.has_score
              ? <>{scoreNotes != null && t("{n} 个音符", { n: scoreNotes })}{j.attention != null && <span className="muted"> · {t("待确认 {n} 处", { n: j.attention })}</span>}</>
              : no}</span></div>
        </div>
      </>
    );
    actions = (
      <>
        <button type="button" className={`btn ${node.state === "done" ? "" : "primary"}`} disabled={!idle} onClick={() => run()}>{t("运行")}</button>
        <button type="button" className="btn" disabled={!j.has_score} onClick={() => go("check")}>{t("检查")}</button>
        <button type="button" className="btn" disabled={!j.has_score} onClick={() => go("export")}>{t("导出")}</button>
      </>
    );
  }

  return (
    <div className="flow">
      <section className="card" aria-labelledby="h-flow">
        <div className="foot">
          <h1 id="h-flow">{headline}</h1>
          <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className="mono" style={{ fontSize: 18 }}>{pct}%</span>
            {!job
              ? <button type="button" className="btn primary" disabled={busy} onClick={() => pick("audio")}>{busy && <span className="spin inline" aria-hidden="true" />}{t("选择原曲…")}</button>
              : running && <button type="button" className="btn" onClick={stop}>{t("停止")}</button>}
          </span>
        </div>
        <div className="bar" role="progressbar" aria-label={t("总进度")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <i style={{ width: `${pct}%` }} />
        </div>
        <div className="actions" role="group" aria-label={t("运行")}>
          <button type="button" className={`btn ${allDone ? "" : "primary"}`} disabled={!idle} onClick={() => run()}>{t("标准流程")}</button>
          <button type="button" className="btn" disabled={!idle} onClick={() => runOnly("separate")}>{t("只分离人声")}</button>
          <button type="button" className="btn" disabled={!idle} onClick={() => runOnly("notes")}>{t("只推理音符音高")}</button>
          <button type="button" className="btn" disabled={!idle} onClick={() => runOnly("lyrics")}>{t("只推理歌词填词")}</button>
        </div>
        {j.state === "failed" && j.error && <div className="notice bad">{t(j.error)}</div>}
        {error && <div className="notice bad" role="alert">{t(error)}</div>}
        {needsEnv && (
          <div className="notice bad foot">
            <span>{t("还缺模型")}</span>
            <button type="button" className="btn" onClick={() => go("env")}>{t("去环境")}</button>
          </div>
        )}
        {taken && (
          <div className="notice foot">
            <span>{t("这首歌已经有工程了")}</span>
            <span style={{ display: "flex", gap: 8 }}>
              <button type="button" className="btn" disabled={busy} onClick={openTaken}>{t("打开它")}</button>
              <button type="button" className="btn" disabled={busy} onClick={another}>{t("另建一个")}</button>
            </span>
          </div>
        )}
        <FlowGraph inputs={inputs} nodes={nodes} wires={wires} selected={current} onSelect={(n) => { if (n !== current) setFiles([]); setSelected(n); }} onSocket={openMenu}>
          {menu && shown && (
            <div ref={menuEl} className="pop-menu" role="menu" aria-label={shown.title}
              style={{ left: Math.min(Math.max(menu.anchor.left, 110), Math.max(110, menu.anchor.width - 110)), top: menu.anchor.top }}>
              <div className="title">{shown.title}</div>
              {shown.items.map((it) => (
                <button key={it.label} type="button" role="menuitem" className={it.primary ? "primary" : ""} disabled={it.disabled}
                  onClick={() => { if (!it.keep) setMenu(null); it.run(); }}>
                  {it.label}
                </button>
              ))}
            </div>
          )}
        </FlowGraph>
      </section>

      <section className="card sec" aria-label={node.title} style={{ gap: 14 }}>
        <div>
          <h1 style={{ fontSize: 18 }}>{node.title}</h1>
          <p className="lead" style={{ fontSize: 14 }}>
            {node.id === "separate" ? t("分离原曲，得到主唱干声、伴奏和拍速。") : node.id === "notes" ? t("从主唱干声转出音符，再把切开的音符合并。") : t("听写、对齐，把每个字放到音符上。")}
          </p>
        </div>
        {rows}
        <div className="actions">{actions}</div>
        {hint && <p className="muted" style={{ fontSize: 13, margin: 0 }}>{hint}</p>}
        {files.length > 0 && (
          <div className="files">
            {files.map((f) => (
              <div key={f.name}>
                <a href={apiUrl(`/jobs/${enc}/export/${encodeURIComponent(f.name)}`)} download style={{ minWidth: 0, overflowWrap: "anywhere" }}>{f.name}</a>
                {desktop
                  ? <button type="button" className="link" onClick={() => reveal(f.name)}><Icon name="folder" size={16} /> {t("打开文件夹")}</button>
                  : <span className="muted" style={{ fontSize: 13 }}>{t("已存到原曲旁边")}</span>}
              </div>
            ))}
          </div>
        )}
        {(["audio", "lyrics", "notes"] as Kind[]).map((kind) => (
          <input key={kind} ref={inputRefs[kind]} type="file" accept={ACCEPT[kind]} hidden onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) take(kind, { file: f, name: f.name });
            e.target.value = "";
          }} />
        ))}
      </section>
    </div>
  );
}

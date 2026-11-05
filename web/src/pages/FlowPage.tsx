import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, apiUrl, follow, type JobDetail, type Mode, type StageInfo, type SvTrack } from "../api";
import FlowGraph, { type Anchor, type NodeId, type NodeState, type NodeView, type Port, type Side, type Socket, type Wire } from "../components/FlowGraph";
import { go, started, useJob } from "../App";
import { desktopPick, inDesktop, onDesktopDrop, revealInFolder } from "../desktop";
import { STAGE_LABEL } from "../env";
import { t } from "../i18n";
import { Icon } from "../icons";
import CheckPage from "./CheckPage";

// 流程: a mode (标准流程 / 只分离人声 / 只推理音符音高 / 只推理歌词填词) decides what the
// project starts from and which nodes the graph shows; the 输入 column holds what the
// user gives in that mode. With no project yet the graph is empty and giving the mode's
// main file (the song, or the dry lead) creates the project next to it. Every node runs
// the pipeline up to its last step (earlier steps come from the cache), can take a file
// in place of an input, and can save what it made next to the song.

type Kind = "audio" | "lead_dry" | "instrumental" | "lyrics" | "notes";
const MODES: { id: Mode; label: string; until?: string; primary: "audio" | "lead_dry"; nodes: NodeId[] }[] = [
  { id: "standard", label: "标准流程", primary: "audio", nodes: ["separate", "notes", "lyrics"] },
  { id: "separate", label: "只分离人声", until: "tempo", primary: "audio", nodes: ["separate"] },
  { id: "notes", label: "只推理音符音高", until: "splits", primary: "lead_dry", nodes: ["notes"] },
  { id: "lyrics", label: "只推理歌词填词", primary: "lead_dry", nodes: ["lyrics"] },
];
const NODE_STAGES: Record<Mode, Partial<Record<NodeId, string[]>>> = {
  standard: { separate: ["separate", "lead", "tempo"], notes: ["transcribe", "splits"], lyrics: ["dictate", "lyrics", "align", "assign"] },
  separate: { separate: ["separate", "lead", "tempo"] },
  notes: { notes: ["tempo", "transcribe", "splits"] },
  lyrics: { lyrics: ["splits", "dictate", "lyrics", "align", "assign"] },
};
const NODE_LAST: Record<NodeId, string> = { separate: "tempo", notes: "splits", lyrics: "assign" };
const NODE_TITLE: Record<NodeId, string> = { separate: "人声分离", notes: "音符音高推理", lyrics: "歌词填词推理" };
const NODE_STEPS: Record<NodeId, string> = { separate: "步骤 1–3", notes: "步骤 4–5", lyrics: "步骤 6–9" };
const LANGS = [["yue", "粤语"], ["zh", "普通话"], ["ja", "日语"], ["en", "英语"]] as const;
const CHINESE = ["yue", "zh"]; // kanji look the same in Chinese and Japanese: Japanese goes with English only
const DEFAULT_LANGS = ["yue", "en"];
const AUDIO = "accompaniment,lead_dry";
const GRID_KEY = "bapu.exportGrid"; // the SV grid the saved .svp files line up with (remembered per browser)
const GRIDS = [[0, "不对齐"], [8, "1/8"], [16, "1/16"], [32, "1/32"]] as const;
const MORE_LABEL: Record<string, string> = { lead: "提取主唱", splits: "合并音符" };
const label = (stage: string, title: string) => t(STAGE_LABEL[stage] ?? MORE_LABEL[stage] ?? title);
const LYRICS_FILE = /\.(ass|ssa|lrc|txt)$/i;
const NOTES_FILE = /\.(svp|mid|midi)$/i;
const AUDIO_ACCEPT = "audio/*,video/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.mp4,.mov,.mkv,.webm";
const ACCEPT: Record<Kind, string> = { audio: AUDIO_ACCEPT, lead_dry: AUDIO_ACCEPT, instrumental: AUDIO_ACCEPT, lyrics: ".ass,.ssa,.lrc,.txt", notes: ".svp,.mid,.midi" };
const KIND_LABEL: Record<Kind, string> = { audio: "原曲", lead_dry: "主唱干声", instrumental: "伴奏", lyrics: "参考歌词", notes: "音符文件" };

/** The graph before a project exists: nothing given, nothing made. */
const NO_JOB: JobDetail = {
  id: "", name: "", folder: "", mode: "standard", project_file: null, state: "idle", stage: null, fraction: null, error: null,
  has_score: false, attention: null, has_lyrics: false, has_notes: false, exported_at: null, export_stale: false,
  stages: [], params: {}, sources: {}, notes: null, tempo: null, exports: [],
};

// what was chosen before the project existed, handed to the page that opens on it
let carried: { langs: string[]; after: { kind: Kind; p: Picked }[]; tempo: number | null } | null = null;

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

/** The project folder: next to the file, named after it (`(2)`, `(3)` … when taken). */
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

/** A chosen file: a real path (system dialog, desktop drop) or a file picked in the browser (uploaded). */
type Picked = { path?: string; file?: File; name: string };
type Live = { stage: string; fraction: number | null };
type Written = { format: string; name: string; size: number };
/** The menu under a clicked socket: give a file to an input, save or run for an output. */
type Menu = { node: Port; side: Side; socket: string; anchor: Anchor };
type Item = {
  label: string; primary?: boolean; disabled?: boolean; keep?: boolean; run?: () => void;
  input?: { placeholder: string; submit: (value: string) => void }; // a value to type instead of a click
};

export default function FlowPage() {
  const { job, refresh, open } = useJob();
  const j = job ?? NO_JOB;
  const [modeChoice, setModeChoice] = useState<Mode>("standard");
  const mode: Mode = job ? j.mode : modeChoice;
  const MODE = MODES.find((m) => m.id === mode) ?? MODES[0];
  const [stages, setStages] = useState<StageInfo[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [selected, setSelected] = useState<NodeId | null>(null);
  const [langs, setLangs] = useState<string[]>(() => {
    const l = job?.params?.lyrics?.langs;
    if (Array.isArray(l) && l.length) return l as string[];
    return carried?.langs ?? DEFAULT_LANGS;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsEnv, setNeedsEnv] = useState(false);
  const [files, setFiles] = useState<Written[]>([]);
  const [grid, setGridState] = useState(savedGrid);
  const [checking, setChecking] = useState(false); // the piano roll and the attention list, under the panel
  // before the project exists: the files and tempo chosen so far, the file whose folder was taken
  const [pre, setPre] = useState<Partial<Record<Kind, Picked>>>({});
  const [preTempo, setPreTempo] = useState<number | null>(null);
  const [taken, setTaken] = useState<{ primary: Picked; folder: string; n: number } | null>(null);
  // a notes file waiting for a track choice, and the tracks to choose from
  const [pending, setPending] = useState<Picked | null>(null);
  const [tracks, setTracks] = useState<SvTrack[] | null>(null);
  const [track, setTrack] = useState(1);
  const inputRefs: Record<Kind, React.RefObject<HTMLInputElement | null>> = {
    audio: useRef<HTMLInputElement>(null), lead_dry: useRef<HTMLInputElement>(null), instrumental: useRef<HTMLInputElement>(null),
    lyrics: useRef<HTMLInputElement>(null), notes: useRef<HTMLInputElement>(null),
  };
  const [menu, setMenu] = useState<Menu | null>(null);
  const menuEl = useRef<HTMLDivElement>(null);
  const [typed, setTyped] = useState(""); // the value in the menu's input row
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
    if (!job) throw new Error(mode === "standard" || mode === "separate" ? "先选原曲" : "先选主唱干声");
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
  }), [act, enc, job, langs, mode, refresh]);

  const stop = useCallback(() => act(async () => {
    await api.post(`/jobs/${enc}/cancel`);
    await refresh();
  }), [act, enc, refresh]);

  const exportNow = useCallback((formats: string[]) => act(async () => {
    const r = await api.post<{ files: Written[] }>(`/jobs/${enc}/export`, { formats, audio: AUDIO, grid });
    setFiles(r.files);
    await refresh();
  }), [act, enc, grid, refresh]);

  const setGrid = (g: number) => {
    setGridState(g);
    try {
      localStorage.setItem(GRID_KEY, String(g));
    } catch {
      /* storage unavailable */
    }
  };

  /** Give the project a file; a 409 means several SV tracks have notes and one must be chosen. */
  const submit = useCallback((kind: Kind, p: Picked, chosen?: number) => act(async () => {
    const form = new FormData();
    if (p.path) form.set("path", p.path);
    else if (p.file) form.set("file", p.file);
    if (chosen != null) form.set("track", String(chosen));
    try {
      await api.form(`/jobs/${enc}/inputs/${kind}`, form);
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

  const setTempo = useCallback((bpm: number) => act(async () => {
    if (!job) {
      setPreTempo(bpm);
      return;
    }
    await api.put(`/jobs/${enc}/tempo`, { bpm });
    await refresh();
  }), [act, enc, job, refresh]);

  const clear = useCallback((kind: Kind | "tempo") => act(async () => {
    if (!job) {
      if (kind === "tempo") setPreTempo(null);
      else setPre((p) => { const q = { ...p }; delete q[kind]; return q; });
      return;
    }
    await api.del(kind === "tempo" ? `/jobs/${enc}/tempo` : `/jobs/${enc}/inputs/${kind}`);
    setPending(null);
    setTracks(null);
    await refresh();
  }), [act, enc, job, refresh]);

  /** Create the project next to its main file (folder `n`; `again`: skip taken folders), then open it. */
  const createProject = useCallback((a: Picked, n: number, again: boolean) => act(async () => {
    setTaken(null);
    let created: JobDetail | null = null;
    while (!created) {
      const form = new FormData();
      form.set("mode", mode);
      const field = MODE.primary === "audio" ? "audio" : "lead_dry";
      if (a.path) {
        form.set(`${field}_path`, a.path);
        form.set("folder", folderFor(a.path, n));
      } else if (a.file) form.set(field, a.file);
      const lyr = pre.lyrics;
      if (lyr?.path) form.set("lyrics_path", lyr.path);
      else if (lyr?.file) form.set("lyrics", lyr.file);
      try {
        created = await api.form<JobDetail>("/jobs", form);
      } catch (e) {
        const folder = takenFolder(e);
        if (!folder) throw e;
        if (!again || n >= 99) {
          setTaken({ primary: a, folder, n });
          return;
        }
        n += 1;
      }
    }
    const after: { kind: Kind; p: Picked }[] = [];
    for (const kind of ["notes", "instrumental"] as Kind[]) if (pre[kind]) after.push({ kind, p: pre[kind]! });
    carried = { langs, after, tempo: preTempo };
    open(created.id); // the page remounts on the new project
  }), [act, langs, mode, MODE.primary, open, pre, preTempo]);

  // the project just opened: hand over what was chosen before it existed
  useEffect(() => {
    if (!job || !carried) return;
    const c = carried;
    carried = null;
    (async () => {
      for (const { kind, p } of c.after) await submit(kind, p);
      if (c.tempo != null) await setTempo(c.tempo);
    })();
  }, [job, submit, setTempo]);

  /** A chosen file goes where it belongs: the mode's main file makes the project, the rest attach to it. */
  const take = useCallback((kind: Kind, p: Picked) => {
    setError(null);
    if (kind === MODE.primary && !job) createProject(p, 1, false);
    else if (kind === "audio" && job) setError("原曲不能换：新建一个工程");
    else if (!job) setPre((q) => ({ ...q, [kind]: p }));
    else submit(kind, p);
  }, [MODE.primary, createProject, job, submit]);
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
  const modeRef = useRef(MODE);
  modeRef.current = MODE;
  useEffect(() => onDesktopDrop((paths) => {
    const files = paths.filter((p) => !p.toLowerCase().endsWith(".bapu"));
    const lyr = files.find((p) => LYRICS_FILE.test(p));
    const notes = files.find((p) => NOTES_FILE.test(p));
    const aud = files.find((p) => !LYRICS_FILE.test(p) && !NOTES_FILE.test(p));
    if (aud) takeRef.current(modeRef.current.primary, { path: aud, name: baseName(aud) });
    if (lyr) takeRef.current("lyrics", { path: lyr, name: baseName(lyr) });
    if (notes) takeRef.current("notes", { path: notes, name: baseName(notes) });
  }), []);

  const openTaken = () => act(async () => {
    const d = await api.post<JobDetail>("/projects/open", { path: taken!.folder });
    setTaken(null);
    open(d.id);
  });
  const another = () => {
    const tk = taken!;
    setTaken(null);
    createProject(tk.primary, tk.n + 1, true);
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
  const groups = NODE_STAGES[mode];

  const nodeState = (n: NodeId): NodeState => {
    const group = groups[n] ?? [];
    if (nowStage && group.includes(nowStage)) return "running";
    if (j.state === "failed" && j.stage && group.includes(j.stage)) return "failed";
    const states = group.map((s) => rowOf(s)?.state ?? "missing").filter((s) => s !== "skipped" && s !== "blocked");
    if (states.length && states.every((s) => s === "fresh")) return "done";
    if (states.every((s) => s === "missing")) return "empty";
    return "stale";
  };
  const status = (n: NodeId) => (nodeState(n) === "running" ? stepName(nowStage) : undefined);

  // what is there, from a stage or from a file the user gave
  const given = (name: string) => (job ? !!j.sources?.[name] : !!pre[name as Kind]);
  const nameOf = (kind: Kind) => (job ? j.sources?.[kind === "audio" ? "source" : kind] : pre[kind]?.name);
  const hasLead = fresh("lead") || given("lead_dry");
  const hasTempo = fresh("tempo") || !!j.tempo || preTempo != null;
  const bpm = j.tempo?.bpm ?? preTempo ?? metric("tempo", "bpm");
  const tempoOrigin = j.tempo ? (j.tempo.origin === "notes" ? t("音符文件") : t("手填")) : preTempo != null ? t("手填") : fresh("tempo") ? t("自动测得") : null;
  const noteCount = metric("splits", "notes");
  const notesFile = job ? (j.notes ? j.sources?.notes ?? j.notes.from : undefined) : pre.notes?.name;
  const lyricsName = nameOf("lyrics");
  const langName = (code: string) => t(LANGS.find(([c]) => c === code)?.[1] ?? code);
  const langNames = langs.map(langName).join("、");
  const tempoText = hasTempo && bpm != null ? `${bpm} BPM${tempoOrigin ? ` · ${tempoOrigin}` : ""}` : undefined;

  // the 输入 column: what the user gives in this mode, wired to the node inputs it feeds
  const col = {
    source: { id: "source", label: t("原曲"), on: given("source") || !!pre.audio, kind: "data", file: nameOf("audio"), hint: t("选一首歌就新建工程") } as Socket,
    lead_dry: { id: "lead_dry", label: t("主唱干声"), on: given("lead_dry"), kind: "audio", file: nameOf("lead_dry"), hint: t("选一个干声文件就新建工程") } as Socket,
    instrumental: { id: "instrumental", label: t("伴奏"), on: given("instrumental"), kind: "audio", optional: true, file: nameOf("instrumental"), hint: t("用来测拍速") } as Socket,
    tempo: { id: "tempo", label: t("拍速"), on: hasTempo, kind: "data", optional: true, file: tempoText, hint: t("手填 BPM，或从伴奏测") } as Socket,
    notes: { id: "notes", label: t("音符文件"), on: !!notesFile, kind: "notes", optional: mode === "standard", file: notesFile,
      hint: mode === "standard" ? t("SV 工程或 MIDI，代替转谱") : t("SV 工程或 MIDI，要填词的音符") } as Socket,
    lyrics: { id: "lyrics", label: t("参考歌词"), on: !!lyricsName, kind: "data", optional: true, file: lyricsName, hint: t("字幕或歌词文件，校正听写") } as Socket,
    lang: { id: "lang", label: t("歌曲语言"), on: true, kind: "data", file: langNames } as Socket,
  };
  const inputs: Socket[] = {
    standard: [col.source, col.notes, col.lyrics, col.lang],
    separate: [col.source],
    notes: [col.lead_dry, col.instrumental, col.tempo],
    lyrics: [col.lead_dry, col.notes, col.tempo, col.lyrics, col.lang],
  }[mode];

  const node = (n: NodeId, ins: Socket[], outs: Socket[]): NodeView => ({
    id: n, title: t(NODE_TITLE[n]), steps: t(NODE_STEPS[n]), state: nodeState(n), status: status(n), inputs: ins, outputs: outs,
  });
  const sock = {
    leadIn: { id: "lead_dry", label: t("主唱干声"), on: hasLead, kind: "audio" } as Socket,
    tempoIn: { id: "tempo", label: t("拍速"), on: hasTempo, kind: "data" } as Socket,
    langIn: { id: "lang", label: t("歌曲语言"), on: true, kind: "data", file: langNames } as Socket,
    refIn: { id: "ref", label: t("参考歌词"), on: !!lyricsName, kind: "data", optional: true, file: lyricsName } as Socket,
  };
  const nodes: NodeView[] = MODE.nodes.map((n) => {
    if (n === "separate") return node("separate",
      [{ id: "source", label: t("原曲"), on: given("source"), kind: "data" }],
      [{ id: "lead_dry", label: t("主唱干声"), on: fresh("lead"), kind: "audio" },
       { id: "accompaniment", label: t("伴奏"), on: fresh("separate"), kind: "audio" },
       { id: "tempo", label: bpm != null && fresh("tempo") ? t("拍速 · {bpm}", { bpm }) : t("拍速"), on: fresh("tempo"), kind: "data" }]);
    if (n === "notes") return node("notes",
      mode === "standard"
        ? [sock.leadIn, { id: "file", label: t("音符文件"), on: !!notesFile, kind: "notes", optional: true, file: notesFile }]
        : [sock.leadIn],
      [{ id: "notes", label: noteCount != null && fresh("splits") ? t("音符 · {n}", { n: noteCount }) : t("音符"), on: fresh("splits"), kind: "notes" }]);
    const notesIn: Socket = { id: "notes", label: mode === "standard" ? t("音符") : t("音符文件"), on: mode === "standard" ? fresh("splits") : !!notesFile, kind: "notes", file: mode === "standard" ? undefined : notesFile };
    return node("lyrics",
      // in the same order as the 输入 column, so the wires do not cross
      mode === "standard" ? [notesIn, sock.leadIn, sock.tempoIn, sock.refIn, sock.langIn] : [sock.leadIn, notesIn, sock.tempoIn, sock.refIn, sock.langIn],
      [{ id: "score", label: t("乐谱"), on: fresh("assign") && j.has_score, kind: "notes" }]);
  });
  const wires: Wire[] = ({
    standard: [
      { from: ["inputs", "source"], to: ["separate", "source"], kind: "data", on: given("source") },
      { from: ["inputs", "notes"], to: ["notes", "file"], kind: "notes", on: !!notesFile },
      { from: ["inputs", "lyrics"], to: ["lyrics", "ref"], kind: "data", on: !!lyricsName },
      { from: ["inputs", "lang"], to: ["lyrics", "lang"], kind: "data", on: true },
      { from: ["separate", "lead_dry"], to: ["notes", "lead_dry"], kind: "audio", on: fresh("lead") },
      { from: ["separate", "lead_dry"], to: ["lyrics", "lead_dry"], kind: "audio", on: fresh("lead") },
      { from: ["separate", "tempo"], to: ["lyrics", "tempo"], kind: "data", on: fresh("tempo") },
      { from: ["notes", "notes"], to: ["lyrics", "notes"], kind: "notes", on: fresh("splits") },
    ],
    separate: [{ from: ["inputs", "source"], to: ["separate", "source"], kind: "data", on: given("source") }],
    notes: [{ from: ["inputs", "lead_dry"], to: ["notes", "lead_dry"], kind: "audio", on: given("lead_dry") }],
    lyrics: [
      { from: ["inputs", "lead_dry"], to: ["lyrics", "lead_dry"], kind: "audio", on: given("lead_dry") },
      { from: ["inputs", "notes"], to: ["lyrics", "notes"], kind: "notes", on: !!notesFile },
      { from: ["inputs", "lyrics"], to: ["lyrics", "ref"], kind: "data", on: !!lyricsName },
      { from: ["inputs", "lang"], to: ["lyrics", "lang"], kind: "data", on: true },
      { from: ["inputs", "tempo"], to: ["lyrics", "tempo"], kind: "data", on: hasTempo },
    ],
  } as Record<Mode, Wire[]>)[mode];
  // the node to open first: the one running, else the first that is not done, else the last
  const current: NodeId = (selected && MODE.nodes.includes(selected) ? selected : null)
    ?? nodes.find((n) => n.state === "running")?.id
    ?? nodes.find((n) => n.state !== "done")?.id
    ?? nodes[nodes.length - 1].id;
  const panelNode = nodes.find((n) => n.id === current)!;

  // -- headline and progress -----------------------------------------------------------------
  const active = j.stages.filter((s) => s.state !== "skipped" && s.state !== "blocked");
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
    const quiet = running || busy;
    const fileMenu = (kind: Kind, choose: string, remove: string | null): { title: string; items: Item[] } => {
      const name = nameOf(kind);
      if (!name) return { title: notYet(t(KIND_LABEL[kind])), items: [{ label: choose, primary: true, disabled: quiet, run: () => pick(kind) }] };
      const items: Item[] = [{ label: t("换一个"), disabled: quiet, run: () => pick(kind) }];
      if (remove) items.push({ label: remove, disabled: quiet, run: () => clear(kind) });
      return { title: `${t(KIND_LABEL[kind])} · ${name}`, items: [...items, ...(kind === "audio" || kind === "lead_dry" ? open_ : [])] };
    };
    const fromNode = (what: string, n: NodeId, ok: boolean) => ({
      title: `${what} · ${t(n === "separate" ? "来自人声分离节点" : "来自音符音高推理节点")}`,
      items: ok
        ? [{ label: t(n === "separate" ? "看人声分离节点" : "看音符音高推理节点"), run: () => setSelected(n) }]
        : [runTo(n, t(n === "separate" ? "运行人声分离" : "运行音符音高推理"))],
    });
    // the same input reached from the 输入 column or from a node socket
    let key: string;
    const s = m.socket;
    if (m.side === "out" && m.node !== "inputs") key = `out:${s}`;
    else if (s === "source") key = "file:source";
    else if (s === "file") key = "file:notes";
    else if (s === "ref" || s === "lyrics") key = "file:lyrics";
    else if (s === "lang") key = "lang";
    else if (s === "instrumental") key = "file:instrumental";
    else if (s === "tempo") key = mode === "standard" && m.node !== "inputs" ? "link:tempo" : "tempo";
    else if (s === "lead_dry") key = mode === "standard" && m.node !== "inputs" ? "link:lead_dry" : "file:lead_dry";
    else if (s === "notes") key = mode === "standard" && m.node !== "inputs" ? "link:notes" : "file:notes";
    else key = `${m.node}/${m.side}/${s}`;
    switch (key) {
      case "file:source":
        if (!job) return { title: notYet(t("原曲")), items: [{ label: t("选择文件…"), primary: true, disabled: busy, run: () => pick("audio") }] };
        return { title: `${t("原曲")} · ${nameOf("audio") ?? "—"}`, items: open_ };
      case "file:lead_dry":
        return fileMenu("lead_dry", t("选择主唱干声…"), null);
      case "file:instrumental":
        return fileMenu("instrumental", t("选择伴奏…"), t("去掉"));
      case "file:lyrics":
        if (!lyricsName) return { title: t("有歌词文件的话，用来校正听写的字"), items: [{ label: t("选择歌词文件…"), primary: true, disabled: quiet, run: () => pick("lyrics") }] };
        return fileMenu("lyrics", "", t("去掉"));
      case "file:notes": {
        if (mode !== "standard" && !job) return fileMenu("notes", t("选择 SV 工程或 MIDI…"), t("去掉"));
        if (!job) return { title: t("先选原曲"), items: [] };
        if (j.notes) {
          const bits = [notesFile ?? ""];
          if (j.notes.notes != null) bits.push(t("{n} 个音符", { n: j.notes.notes }));
          if (j.notes.track != null) bits.push(t("第 {n} 轨", { n: j.notes.track }));
          return { title: bits.join(" · "), items: [
            { label: t("换一个"), disabled: quiet, run: () => pick("notes") },
            { label: mode === "standard" ? t("改回自动转谱") : t("去掉"), disabled: quiet, run: () => clear("notes") }] };
        }
        return { title: mode === "standard" ? t("自动转谱。也可以用 SV 工程里的音符，比如 SV2 从主唱干声提取的") : notYet(t("音符文件")),
          items: [{ label: t("选择 SV 工程或 MIDI…"), primary: true, disabled: quiet, run: () => pick("notes") }] };
      }
      case "tempo": {
        const items: Item[] = [{ label: t("手填 BPM"), input: { placeholder: "125", submit: (v) => { const n = Number(v); if (n > 0) setTempo(n); } } }];
        if (j.tempo?.origin === "user" || preTempo != null) items.push({ label: t("去掉手填的拍速"), disabled: quiet, run: () => clear("tempo") });
        if (mode === "notes" && !given("instrumental") && !hasTempo) items.push({ label: t("选择伴奏来测…"), disabled: quiet, run: () => pick("instrumental") });
        return { title: tempoText ? `${t("拍速")} · ${tempoText}` : notYet(t("拍速")), items };
      }
      case "lang":
        return { title: t("歌曲语言"), items: LANGS.map(([code, name]) => ({
          label: `${langs.includes(code) ? "✓" : "　"} ${t(name)}`, keep: true, disabled: running, run: () => toggleLang(code),
        })) };
      case "link:lead_dry":
        return fromNode(t("主唱干声"), "separate", fresh("lead"));
      case "link:tempo":
        return fromNode(t("拍速"), "separate", fresh("tempo"));
      case "link:notes":
        return fromNode(t("音符"), "notes", fresh("splits"));
      case "out:lead_dry":
      case "out:accompaniment": {
        const what = t(s === "lead_dry" ? "主唱干声" : "伴奏");
        if (!fresh("lead")) return { title: notYet(what), items: [runTo("separate")] };
        return { title: what, items: [
          { label: t("存 wav"), disabled: !idle, run: () => exportNow(["wav"]) },
          { label: t("存 svp（只有音轨）"), disabled: !idle || !fresh("tempo"), run: () => exportNow(["svp-audio"]) },
          ...open_] };
      }
      case "out:tempo":
        if (!fresh("tempo")) return { title: notYet(t("拍速")), items: [runTo("separate")] };
        return { title: `${t("拍速")} · ${bpm} BPM`, items: [
          { label: t("存 svp（只有音轨）"), disabled: !idle || !fresh("lead"), run: () => exportNow(["svp-audio"]) }] };
      case "out:notes":
        if (!fresh("splits")) return { title: notYet(t("音符")), items: [runTo("notes")] };
        return { title: noteCount != null ? t("{n} 个音符", { n: noteCount }) : t("音符"), items: [
          { label: t("存 svp（音符）"), disabled: !idle || !hasTempo, run: () => exportNow(["svp-notes"]) },
          ...open_] };
      case "out:score": {
        if (!(fresh("assign") && j.has_score)) return { title: notYet(t("乐谱")), items: [runTo("lyrics", t("运行"))] };
        const n = metric("assign", "notes");
        const bits = [t("乐谱")];
        if (n != null) bits.push(t("{n} 个音符", { n }));
        if (j.attention != null) bits.push(t("待确认 {n} 处", { n: j.attention }));
        return { title: bits.join(" · "), items: [
          { label: t("检查"), primary: true, run: () => setChecking(true) },
          { label: t("存 svp"), disabled: !idle, run: () => exportNow(["svp"]) },
          { label: t("存 MIDI"), disabled: !idle, run: () => exportNow(["mid"]) },
          ...open_] };
      }
      default:
        return { title: key, items: [] };
    }
  };
  const openMenu = (node_: Port, side: Side, socket: string, anchor: Anchor) => {
    setTyped("");
    setMenu((cur) => (cur && cur.node === node_ && cur.side === side && cur.socket === socket ? null : { node: node_, side, socket, anchor }));
  };
  const shown = menu ? menuFor(menu) : null;

  // -- the selected node's panel -----------------------------------------------------------------
  const fileWidget = (kind: Kind, icon: "music" | "text", empty: string, remove: string | null) => {
    const name = nameOf(kind);
    const quiet = running || busy;
    return name ? (
      <div className="drop slim filled">
        <span style={{ color: "var(--accent)" }}><Icon name={icon} size={20} /></span>
        <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}><b style={{ fontWeight: 500 }}>{name}</b>
          {kind === "notes" && j.notes && (
            <span className="muted" style={{ fontSize: 13, marginLeft: 8 }}>
              {j.notes.notes != null && t("{n} 个音符", { n: j.notes.notes })}
              {j.notes.track != null && ` · ${t("第 {n} 轨", { n: j.notes.track })}`}
              {mode === "standard" && <> · {t("转谱已跳过，用的是这个文件")}</>}
            </span>
          )}
        </span>
        <button type="button" className="link" disabled={quiet} onClick={() => pick(kind)}>{t("换一个")}</button>
        {remove && <button type="button" className="link" disabled={quiet} onClick={() => clear(kind)}>{remove}</button>}
      </div>
    ) : (
      <button type="button" className="drop slim" disabled={quiet} onClick={() => pick(kind)}>
        <span className="muted"><Icon name={icon} size={20} /></span>
        <span style={{ flex: 1 }} className="muted">{empty}</span>
        <span className="link">{t("选择")}</span>
      </button>
    );
  };
  const tempoRow = (
    <div className="row"><span className="k">{t("拍速")}</span>
      <span className="v">{tempoText ?? no}</span>
    </div>
  );
  const trackChoice = tracks && pending && (
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
  );

  let rows;
  let actions;
  let hint: string | null = null;
  if (panelNode.id === "separate") {
    const conf = metric("tempo", "confidence");
    rows = (
      <div className="rows">
        <div className="row"><span className="k">{t("原曲")}</span>
          <span className="v">{nameOf("audio") ?? <button type="button" className="link" disabled={busy} onClick={() => pick("audio")}>{t("选择文件…")}</button>}</span></div>
        <div className="row"><span className="k">{t("主唱干声")}</span><span className="v">{fresh("lead") ? yes : no}</span></div>
        <div className="row"><span className="k">{t("伴奏")}</span><span className="v">{fresh("separate") ? yes : no}</span></div>
        <div className="row"><span className="k">{t("拍速")}</span>
          <span className="v">{fresh("tempo") && bpm != null ? <>{bpm} BPM{conf === "low" && <span className="muted"> · {t("置信度低")}</span>}</> : no}</span></div>
      </div>
    );
    actions = (
      <>
        <button type="button" className={`btn ${panelNode.state === "done" ? "" : "primary"}`} disabled={!idle} onClick={() => run(NODE_LAST.separate)}>{t("运行到这里")}</button>
        <button type="button" className="btn" disabled={!idle || !fresh("lead")} onClick={() => exportNow(["wav"])}>{t("存 wav")}</button>
        <button type="button" className="btn" disabled={!idle || !fresh("lead") || !fresh("tempo")} onClick={() => exportNow(["svp-audio"])}>{t("存 svp（只有音轨）")}</button>
        {desktop && job && <button type="button" className="btn" onClick={() => revealInFolder(j.folder)}>{t("打开文件夹")}</button>}
      </>
    );
    hint = t("存出的 .svp 只有伴奏和主唱干声两条音轨。在 SV2 里对主唱干声提取音高、保存，再到“音符音高推理”节点把那个工程选进来。");
  } else if (panelNode.id === "notes") {
    const merged = metric("splits", "auto_merged");
    rows = (
      <>
        {mode !== "standard" && fileWidget("lead_dry", "music", t("选一个干声文件就新建工程"), null)}
        {mode !== "standard" && fileWidget("instrumental", "music", t("伴奏（可选）：用来测拍速"), t("去掉"))}
        <div className="rows">
          {mode === "standard" && <div className="row"><span className="k">{t("主唱干声")}</span><span className="v">{fresh("lead") ? yes : no}</span></div>}
          {mode !== "standard" && tempoRow}
          <div className="row"><span className="k">{t("音符")}</span>
            <span className="v">{fresh("splits") && noteCount != null
              ? <>{t("{n} 个音符", { n: noteCount })}{typeof merged === "number" && merged > 0 && <span className="muted"> · {t("自动合并 {n} 处", { n: merged })}</span>}</>
              : no}</span></div>
        </div>
        {mode === "standard" && fileWidget("notes", "music", t("自动转谱。也可以用 SV 工程里的音符，比如 SV2 从主唱干声提取的"), t("改回自动转谱"))}
        {trackChoice}
      </>
    );
    actions = (
      <>
        <button type="button" className={`btn ${panelNode.state === "done" ? "" : "primary"}`} disabled={!idle} onClick={() => run(NODE_LAST.notes)}>{t("运行到这里")}</button>
        <button type="button" className="btn" disabled={!idle || !fresh("splits") || !hasTempo} onClick={() => exportNow(["svp-notes"])}>{t("存 svp（音符）")}</button>
      </>
    );
    hint = mode === "standard"
      ? t("存出的 .svp 里的音符还没有歌词（先填 la），带两条音轨，可以先在 SV 里听一遍。")
      : t("存 svp 需要拍速：给伴奏让 Bapu 测，或者在输入里手填 BPM。");
  } else {
    const scoreNotes = metric("assign", "notes");
    rows = (
      <>
        {mode !== "standard" && fileWidget("lead_dry", "music", t("选一个干声文件就新建工程"), null)}
        {mode !== "standard" && fileWidget("notes", "music", t("要填词的音符：SV 工程或 MIDI"), t("去掉"))}
        {trackChoice}
        {fileWidget("lyrics", "text", t("有歌词文件的话，用来校正听写的字"), t("去掉"))}
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
          {mode !== "standard" && tempoRow}
          <div className="row"><span className="k">{t("乐谱")}</span>
            <span className="v">{fresh("assign") && j.has_score
              ? <>{scoreNotes != null && t("{n} 个音符", { n: scoreNotes })}{j.attention != null && <span className="muted"> · {t("待确认 {n} 处", { n: j.attention })}</span>}</>
              : no}</span></div>
        </div>
      </>
    );
    actions = (
      <>
        <button type="button" className={`btn ${panelNode.state === "done" ? "" : "primary"}`} disabled={!idle} onClick={() => run()}>{t("运行")}</button>
        <button type="button" className="btn" disabled={!j.has_score} onClick={() => setChecking((c) => !c)}>{checking ? t("收起检查") : t("检查")}</button>
        <button type="button" className="btn" disabled={!idle || !j.has_score} onClick={() => exportNow(["svp"])}>{t("存 svp")}</button>
        <button type="button" className="btn" disabled={!idle || !j.has_score} onClick={() => exportNow(["mid"])}>{t("存 MIDI")}</button>
      </>
    );
  }

  const primaryLabel = MODE.primary === "audio" ? t("选择原曲…") : t("选择主唱干声…");
  return (
    <div className="flow">
      <section className="card" aria-labelledby="h-flow">
        <div className="seg" role="radiogroup" aria-label={t("模式")}>
          {MODES.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} className={mode === m.id ? "on" : ""}
              disabled={!!job && m.id !== mode} title={job ? t("模式在新建工程时选") : undefined}
              onClick={() => { if (!job) { setModeChoice(m.id); setSelected(null); setMenu(null); } }}>
              {t(m.label)}
            </button>
          ))}
        </div>
        <div className="foot">
          <h1 id="h-flow">{headline}</h1>
          <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className="mono" style={{ fontSize: 18 }}>{pct}%</span>
            {!job
              ? <button type="button" className="btn primary" disabled={busy} onClick={() => pick(MODE.primary)}>{busy && <span className="spin inline" aria-hidden="true" />}{primaryLabel}</button>
              : running
                ? <button type="button" className="btn" onClick={stop}>{t("停止")}</button>
                : <button type="button" className={`btn ${allDone ? "" : "primary"}`} disabled={busy} onClick={() => run(MODE.until)}>{t("运行")}</button>}
          </span>
        </div>
        <div className="bar" role="progressbar" aria-label={t("总进度")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <i style={{ width: `${pct}%` }} />
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
              {shown.items.map((it) => it.input ? (
                <form key={it.label} className="pop-input" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { it.input!.submit(typed.trim()); setMenu(null); } }}>
                  <span>{it.label}</span>
                  <input type="number" min={20} max={400} step="0.01" placeholder={it.input.placeholder} value={typed} aria-label={it.label}
                    onChange={(e) => setTyped(e.target.value)} />
                  <button type="submit" className="btn" disabled={!typed.trim()}>{t("确定")}</button>
                </form>
              ) : (
                <button key={it.label} type="button" role="menuitem" className={it.primary ? "primary" : ""} disabled={it.disabled}
                  onClick={() => { if (!it.keep) setMenu(null); it.run?.(); }}>
                  {it.label}
                </button>
              ))}
            </div>
          )}
        </FlowGraph>
      </section>

      <section className="card sec" aria-label={panelNode.title} style={{ gap: 14 }}>
        <div>
          <h1 style={{ fontSize: 18 }}>{panelNode.title}</h1>
          <p className="lead" style={{ fontSize: 14 }}>
            {panelNode.id === "separate" ? t("分离原曲，得到主唱干声、伴奏和拍速。") : panelNode.id === "notes" ? t("从主唱干声转出音符，再把切开的音符合并。") : t("听写、对齐，把每个字放到音符上。")}
          </p>
        </div>
        {rows}
        <div className="actions">{actions}</div>
        <div className="foot" style={{ justifyContent: "flex-start", gap: 10 }}>
          <span className="muted" style={{ fontSize: 13 }}>{t("存 svp 时对齐 SV 的网格")}</span>
          <span className="seg" role="group" aria-label={t("对齐网格")}>
            {GRIDS.map(([g, name]) => (
              <button key={g} type="button" className={grid === g ? "on" : ""} aria-pressed={grid === g} onClick={() => setGrid(g)}>{t(name)}</button>
            ))}
          </span>
        </div>
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
        {checking && job && j.has_score && (
          <div className="sec" style={{ marginTop: 6 }}>
            <div className="foot">
              <h2>{t("检查乐谱")}</h2>
              <button type="button" className="link" onClick={() => setChecking(false)}>{t("收起")}</button>
            </div>
            <CheckPage key={job.id} id={job.id} />
          </div>
        )}
        {(Object.keys(ACCEPT) as Kind[]).map((kind) => (
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

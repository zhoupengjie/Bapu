// Client for the local Bapu API (`bapu serve`, proxied at /api in development).

export type StageRow = {
  stage: string;
  title: string;
  index: number;
  state: "fresh" | "stale" | "missing";
  seconds: number | null;
  metrics: Record<string, unknown>;
  params: Record<string, unknown>;
};

export type JobSummary = {
  id: string;
  name: string;
  folder: string; // the project folder
  project_file: string | null; // <name>.bapu inside it
  state: "idle" | "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string | null;
  fraction: number | null;
  error: string | null;
  has_score: boolean;
  attention: number | null;
  has_lyrics: boolean;
  exported_at: number | null; // newest file in export/, unix seconds
  export_stale: boolean; // the score changed after that export
};

export type JobDetail = JobSummary & {
  stages: StageRow[];
  params: Record<string, Record<string, unknown>>;
  sources: Record<string, string>;
  exports: { name: string; size: number; mtime: number }[];
};

export type StageInfo = {
  name: string;
  title: string;
  index: number;
  defaults: Record<string, unknown>;
  model_params: Record<string, string>;
  optional_inputs: string[];
};

export type License = { name: string; url: string; commercial: "yes" | "no" | "unknown"; note: string };

export type Model = {
  id: string;
  stage: string;
  name: string;
  backend: string;
  verified: boolean;
  size: number | null;
  status: "ready" | "missing" | "corrupt" | "builtin";
  license: License;
  license_accepted: boolean;
  downloading: boolean;
  download: {
    dir: string | null;
    files: { name: string; size: number | null; sha256: string; urls: string[]; path: string | null; present: boolean }[];
    archive: { url: string; size: number | null; name: string } | null;
  };
};

export type SystemInfo = {
  queue: number;
  running: string[];
  gpu?: { name: string; free: number; total: number };
  disk_free: number;
};

export type Kind = "word" | "sustain" | "cont";

export type ScoreNote = {
  index: number;
  start: number;
  dur: number;
  pitch: number;
  cents: number;
  kind: Kind;
  lyric: string;
  auto_lyric: string;
  lang: string;
  line: number;
  conf: { align: number | null; onset: number | null };
  merged_from: number[];
  flags: string[];
};

export type ScoreLine = {
  line: number;
  start: number | null;
  end: number | null;
  t0: number | null;
  t1: number | null;
  conf: number | null;
  version: "auto" | "manual";
  syllables: number;
  notes: number;
  sustain: number;
  first_note: number | null;
  last_note: number | null;
};

export type Score = {
  tempo: { bpm: number; time_signature: string; source: string; confidence: string };
  lang: string;
  lines: ScoreLine[];
  notes: ScoreNote[];
};

export type ReviewLine = ScoreLine & {
  low_conf: boolean;
  fewer_notes: boolean;
  maybe_missing: { notes: number[]; time: number } | null;
  dictation_only?: boolean;
  attention: boolean;
};

export type Review = {
  summary: Record<string, number | string | boolean>;
  dictation: { kind: string; line: number; time: number | null; conf: number | null; status: string; attention: boolean }[];
  merges: { time: number; note: number | null; line: number | null; score: number; status: string; attention: boolean }[];
  sustains: { time: number; note: number; line: number; onset: number | null; after_rest: boolean; fixed: boolean; attention: boolean }[];
  lines: ReviewLine[];
  stale_corrections: { id: number; type: string; at: number | null; line: number | null }[];
  conflicting_corrections: { id: number; line: number; reason: string }[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) return (await r.json()) as T;
  let body: { error?: string; detail?: unknown } = {};
  try {
    body = await r.json();
  } catch {
    /* not JSON */
  }
  const message = body.error ?? (typeof body.detail === "string" ? body.detail : r.statusText);
  throw new ApiError(r.status, message, body.detail);
}

export const api = {
  get: <T>(path: string) => fetch(`/api${path}`).then((r) => handle<T>(r)),
  post: <T>(path: string, body?: unknown) =>
    fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }).then((r) => handle<T>(r)),
  put: <T>(path: string, body: unknown) =>
    fetch(`/api${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  del: <T>(path: string) => fetch(`/api${path}`, { method: "DELETE" }).then((r) => handle<T>(r)),
  form: <T>(path: string, form: FormData) =>
    fetch(`/api${path}`, { method: "POST", body: form }).then((r) => handle<T>(r)),
};

/** Follow a Server-Sent Events topic; returns a function that stops listening. */
export function follow(path: string, onEvent: (ev: Record<string, unknown> & { type: string; seq: number }) => void) {
  const es = new EventSource(`/api${path}`);
  const handler = (e: MessageEvent) => onEvent(JSON.parse(e.data));
  for (const t of ["state", "progress", "message"]) es.addEventListener(t, handler as EventListener);
  return () => es.close();
}

export const audioUrl = (job: string, stem: string) => `/api/jobs/${encodeURIComponent(job)}/audio/${stem}`;

export function fmtSize(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.round(n / 1e3)} KB`;
}

export function mmss(t: number | null | undefined): string {
  if (t == null) return "—";
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
}

export const KIND_LABEL: Record<Kind, string> = { word: "普通字", sustain: "- 延音", cont: "+ 连音" };

export const FLAG_LABEL: Record<string, string> = {
  auto_merged: "自动合并", user_merged: "你确认的合并", manual_line: "人工版本", kind_fixed: "你指定的类型",
  sustain_after_rest: "休止后的 -", low_conf_line: "所在句置信度低",
};

/** A job that finished before the server restarted is idle in memory but has a score. */
export function jobLabel(j: { state: string; has_score: boolean }): [string, string] {
  const map: Record<string, [string, string]> = {
    queued: ["排队中", "info"], running: ["运行中", "info"], done: ["已完成", "ok"],
    failed: ["失败", "bad"], cancelled: ["已取消", "warn"],
  };
  if (j.state === "idle") return j.has_score ? ["已完成", "ok"] : ["未运行", ""];
  return map[j.state] ?? [j.state, ""];
}

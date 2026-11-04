import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import { FLAG_LABEL, KIND_LABEL, api, audioUrl, mmss, type JobDetail, type Kind, type Review, type Score } from "../api";
import { JobHeader, go } from "../App";
import PianoRoll, { pitchName } from "../components/PianoRoll";

type Item = { id: string; cat: string; time: number; line: number | null; note?: number | null; text: string; attention: boolean };
type Pending = { key: string; entry: Record<string, unknown>; label: string };

const CATS: Record<string, string> = {
  dictation: "听写", merge: "合并", line: "句子", sustain: "- 位置", harmony: "和声", correction: "修正",
};

function buildItems(r: Review): Item[] {
  const items: Item[] = [];
  r.dictation.forEach((d, k) => items.push({ id: `d${k}`, cat: "dictation", time: d.time ?? 0, line: d.line, text: d.status, attention: d.attention }));
  r.merges.forEach((m, k) => items.push({ id: `m${k}`, cat: "merge", time: m.time, line: m.line, note: m.note, text: `${m.status || "已合并"} · 分数 ${m.score.toFixed(3)}`, attention: m.attention }));
  r.lines.filter((l) => l.attention).forEach((l) => {
    const why = [l.low_conf && "对齐置信度低", l.fewer_notes && "音符比音节少", l.maybe_missing && "可能漏字", l.dictation_only && "只有听写"].filter(Boolean).join("、");
    items.push({ id: `l${l.line}`, cat: "line", time: l.t0 ?? l.start ?? 0, line: l.line, note: l.first_note, text: `${why} · 置信度 ${l.conf?.toFixed(2) ?? "—"}`, attention: true });
  });
  r.sustains.filter((s) => s.attention).forEach((s, k) => items.push({ id: `s${k}`, cat: "sustain", time: s.time, line: s.line, note: s.note, text: `${s.after_rest ? "休止后" : "起音明显"} · 起音 ${s.onset?.toFixed(2) ?? "—"}`, attention: true }));
  (r.harmony ?? []).forEach((h, k) => items.push({ id: `h${k}`, cat: "harmony", time: h.time, line: h.line, text: `和声 #${h.note} · ${h.status}`, attention: h.attention }));
  r.stale_corrections.forEach((c) => items.push({ id: `c${c.id}`, cat: "correction", time: c.at ?? 0, line: c.line, text: `第 ${c.id} 条修正失效（${c.type}）`, attention: true }));
  r.conflicting_corrections.forEach((c) => items.push({ id: `x${c.id}`, cat: "correction", time: 0, line: c.line, text: `第 ${c.id} 条修正冲突：${c.reason}`, attention: true }));
  return items.sort((a, b) => a.time - b.time);
}

export default function ReviewPage({ id }: { id: string }) {
  const enc = encodeURIComponent(id);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [score, setScore] = useState<Score | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stem, setStem] = useState("lead_dry");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [span, setSpan] = useState(12);
  const [t0, setT0] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState<string>("attention");
  const [itemId, setItemId] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [showHarmony, setShowHarmony] = useState(true);
  const [harmonySel, setHarmonySel] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const waveEl = useRef<HTMLDivElement>(null);
  const ws = useRef<WaveSurfer | null>(null);

  const load = useCallback(() => {
    api.get<JobDetail>(`/jobs/${enc}`).then(setJob).catch(() => {});
    api.get<Score>(`/jobs/${enc}/score`).then(setScore).catch((e) => setError(e.message));
    api.get<Review>(`/jobs/${enc}/review`).then(setReview).catch(() => {});
  }, [enc]);
  useEffect(load, [load]);

  // audio + waveform overview
  useEffect(() => {
    if (!waveEl.current) return;
    const style = getComputedStyle(document.documentElement);
    const w = WaveSurfer.create({
      container: waveEl.current, url: audioUrl(id, stem), height: 64, normalize: true,
      waveColor: style.getPropertyValue("--border-strong").trim() || "#c7ced8",
      progressColor: style.getPropertyValue("--accent").trim() || "#2457d6",
      cursorColor: "#c2410c", barWidth: 2, barGap: 1,
    });
    w.on("timeupdate", (t) => setTime(t));
    w.on("play", () => setPlaying(true));
    w.on("pause", () => setPlaying(false));
    w.on("error", () => setError("这条音轨还没有生成"));
    ws.current = w;
    return () => {
      w.destroy();
      ws.current = null;
    };
  }, [id, stem]);

  // keep the piano roll on the playhead while playing
  useEffect(() => {
    if (playing && (time < t0 || time > t0 + span * 0.85)) setT0(Math.max(0, time - span * 0.15));
  }, [time, playing, t0, span]);

  const seek = (t: number) => {
    const w = ws.current;
    if (w && w.getDuration() > 0) w.setTime(Math.max(0, t));
    setTime(t);
  };
  const focus = (t: number) => {
    setT0(Math.max(0, t - span * 0.3));
    seek(t);
  };

  const items = useMemo(() => (review ? buildItems(review) : []), [review]);
  const shown = items.filter((i) => (filter === "attention" ? i.attention : filter === "all" ? true : i.cat === filter));
  const notesByIndex = useMemo(() => new Map(score?.notes.map((n) => [n.index, n]) ?? []), [score]);
  const sel = selected != null ? notesByIndex.get(selected) ?? null : null;
  const kindOverride = useMemo(() => {
    const o: Record<number, Kind> = {};
    for (const p of pending) if (p.entry.type === "kind" && p.entry.note) o[p.entry.note as number] = p.entry.kind as Kind;
    return o;
  }, [pending]);
  const cuts = useMemo(() => review?.merges.filter((m) => m.status !== "已拒绝合并").map((m) => m.time) ?? [], [review]);
  const reviewLines = useMemo(() => {
    const att = new Set(review?.lines.filter((l) => l.attention).map((l) => l.line));
    return (score?.lines ?? []).map((l) => ({ ...l, attention: att.has(l.line) }));
  }, [score, review]);

  function pickItem(it: Item) {
    setItemId(it.id);
    setHarmonySel(it.cat === "harmony" ? Number(it.text.match(/#(\d+)/)?.[1]) : null);
    const n = it.note != null ? notesByIndex.get(it.note) : score?.notes.find((x) => x.start >= it.time - 0.05);
    if (n) setSelected(n.index);
    focus(it.time);
  }

  function addPending(p: Pending) {
    setPending((list) => [...list.filter((x) => x.key !== p.key), p]);
  }

  async function saveAndRerun() {
    setSaving(true);
    setError(null);
    try {
      const cur = await api.get<{ text: string; version: string }>(`/jobs/${enc}/corrections`);
      // JSON is valid YAML: one flow-style entry per line keeps the user's own text untouched.
      const block = pending.map((p) => {
        const e = { ...p.entry };
        delete e.note;
        return `- ${JSON.stringify(e)}`;
      }).join("\n");
      const text = (cur.text && !cur.text.endsWith("\n") ? cur.text + "\n" : cur.text) + `\n# 审查页 ${new Date().toLocaleString()}\n${block}\n`;
      await api.put(`/jobs/${enc}/corrections`, { text, version: cur.version });
      await api.post(`/jobs/${enc}/run`, {});
      setPending([]);
      go(`/job/${enc}/run`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // a line's Chinese language: the language of its Chinese notes (English words are always English)
  const lineLang = (line: number) => score?.notes.find((n) => n.line === line && n.lang !== "en")?.lang ?? "en";
  const noteMerges = sel ? review?.merges.filter((m) => m.note === sel.index) ?? [] : [];
  const lineNotes = sel ? score?.notes.filter((n) => n.line === sel.line) ?? [] : [];

  return (
    <main className="page">
      <JobHeader job={job} tab="review" />
      {error && <div className="notice bad">{error}</div>}
      {review && (
        <div className="notice">
          {score?.notes.length} 个音符 · {score?.lines.length} 句 · {score?.tempo.bpm} BPM ·
          歌词来源：{review.summary.lyrics_mode === "dictation_only" ? "只有听写" : `听写 + 参考歌词（听写改了 ${review.summary.dictation_changes} 处）`} ·
          <b> 需要确认 {String(review.summary.attention)} 条</b>
        </div>
      )}
      <div className="row">
        <div className="grow" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card" aria-label="播放">
            <div className="row" style={{ gap: 10, alignItems: "center" }}>
              <button type="button" className="btn" onClick={() => ws.current?.playPause()} aria-label={playing ? "暂停" : "播放"}>{playing ? "❚❚ 暂停" : "▶ 播放"}</button>
              <span className="mono">{mmss(time)}</span>
              <label className="small muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>音轨
                <select value={stem} onChange={(e) => setStem(e.target.value)}>
                  <option value="lead_dry">主唱干声</option>
                  <option value="source">原曲</option>
                  <option value="instrumental">伴奏</option>
                  <option value="lead">主唱（带混响）</option>
                </select>
              </label>
              <label className="small muted" style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: "auto" }}>窗口
                <select value={span} onChange={(e) => setSpan(Number(e.target.value))}>
                  {[6, 12, 24, 48].map((s) => <option key={s} value={s}>{s} 秒</option>)}
                </select>
              </label>
            </div>
            <div ref={waveEl} />
          </section>

          <section className="card flush" aria-label="钢琴卷帘">
            <div className="head">
              <span className="mono small">{mmss(t0)} – {mmss(t0 + span)}</span>
              <span className="legend">
                <span><i style={{ background: "var(--word)" }} />普通字</span>
                <span><i style={{ background: "var(--sustain)" }} />- 延音</span>
                <span><i style={{ background: "var(--cont)" }} />+ 连音</span>
                <span>虚线 = 合并前的切口</span>
              </span>
              {!!score?.harmony?.length && (
                <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input type="checkbox" checked={showHarmony} onChange={() => setShowHarmony(!showHarmony)} />
                  显示和声（粉色框，{score.harmony.length} 个）
                </label>
              )}
              <span className="seg">
                <button type="button" onClick={() => setT0(Math.max(0, t0 - span * 0.8))}>◀</button>
                <button type="button" onClick={() => setT0(t0 + span * 0.8)}>▶</button>
              </span>
            </div>
            {score && (
              <PianoRoll notes={score.notes} lines={reviewLines} t0={t0} span={span} playhead={time} selected={selected}
                kindOverride={kindOverride} cuts={cuts} harmony={showHarmony ? score.harmony ?? [] : []} selectedHarmony={harmonySel} onSelect={(i) => { setSelected(i); seek(notesByIndex.get(i)!.start); }} onSeek={seek} />
            )}
          </section>

          {sel && (
            <section className="card" aria-labelledby="insp">
              <div className="row" style={{ gap: 10, alignItems: "center" }}>
                <h2 id="insp">音符 #{sel.index}</h2>
                <span className="badge">L{String(sel.line).padStart(2, "0")}</span>
                {[...new Set(sel.flags)].map((f) => <span key={f} className="badge warn">{FLAG_LABEL[f] ?? f}</span>)}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12 }}>
                <div><div className="muted small">时间</div><div className="mono">{mmss(sel.start)} – {mmss(sel.start + sel.dur)}</div></div>
                <div><div className="muted small">音高</div><div className="mono">{pitchName(sel.pitch)} {sel.cents >= 0 ? "+" : ""}{sel.cents} 音分</div></div>
                <div><div className="muted small">歌词</div><div>{sel.lyric}{sel.auto_lyric !== sel.lyric && <span className="muted small">（自动：{sel.auto_lyric}）</span>}</div></div>
                <div><div className="muted small">对齐 / 起音</div><div className="mono">{sel.conf.align?.toFixed(2) ?? "—"} / {sel.conf.onset?.toFixed(2) ?? "—"}</div></div>
              </div>
              <div className="field">类型
                <div className="seg">
                  {(["word", "sustain", "cont"] as Kind[]).map((k) => (
                    <button key={k} type="button" className={(kindOverride[sel.index] ?? sel.kind) === k ? "on" : ""}
                      onClick={() => k === sel.kind
                        ? setPending((l) => l.filter((p) => p.key !== `kind${sel.index}`))
                        : addPending({ key: `kind${sel.index}`, entry: { type: "kind", at: +sel.start.toFixed(3), kind: k, note: sel.index }, label: `#${sel.index} 改成${KIND_LABEL[k]}` })}>
                      {KIND_LABEL[k]}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field">这一句（L{String(sel.line).padStart(2, "0")}）的语言
                <div className="seg">
                  {([["yue", "粤语"], ["zh", "普通话"], ["en", "英语"]] as const).map(([id, label]) => {
                    const pendingLang = pending.find((p) => p.key === `lang${sel.line}`)?.entry.lang;
                    const current = (pendingLang as string | undefined) ?? (sel.lang === "en" ? lineLang(sel.line) : sel.lang);
                    return (
                      <button key={id} type="button" className={current === id ? "on" : ""}
                        onClick={() => addPending({ key: `lang${sel.line}`, entry: { type: "line_lang", line: sel.line, lang: id }, label: `L${String(sel.line).padStart(2, "0")} 改成${label}` })}>
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="row" style={{ gap: 10 }}>
                {noteMerges.map((m) => (
                  <span key={m.time} className="row" style={{ gap: 6, alignItems: "center" }}>
                    <span className="small muted">{mmss(m.time)} 的合并：</span>
                    <button type="button" className="btn small" onClick={() => addPending({ key: `m${m.time}`, entry: { type: "merge", at: +m.time.toFixed(3), action: "confirm" }, label: `确认 ${mmss(m.time)} 的合并` })}>确认合并</button>
                    <button type="button" className="btn small" onClick={() => addPending({ key: `m${m.time}`, entry: { type: "merge", at: +m.time.toFixed(3), action: "reject" }, label: `拆开 ${mmss(m.time)} 的合并` })}>拆回两个音符</button>
                  </span>
                ))}
                <button type="button" className="btn small" onClick={() => addPending({
                  key: `line${sel.line}`,
                  entry: { type: "line_version", line: sel.line, notes: lineNotes.map((n) => ({ at: +n.start.toFixed(3), lyric: kindOverride[n.index] === "sustain" ? "-" : kindOverride[n.index] === "cont" ? "+" : n.lyric })) },
                  label: `锁定 L${String(sel.line).padStart(2, "0")} 的当前版本`,
                })}>锁定这一句的当前版本</button>
                <button type="button" className="btn small" onClick={() => {
                  const end = lineNotes.at(-1);
                  const st = lineNotes[0]?.start ?? sel.start;
                  focus(st);
                  ws.current?.play(st, end ? end.start + end.dur : undefined);
                }}>试听这一句</button>
              </div>
            </section>
          )}
        </div>

        <aside className="side" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card flush" aria-labelledby="items">
            <div className="head" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <h2 id="items" style={{ margin: 0, fontSize: 17 }}>需要确认</h2>
              <div className="seg" role="group" aria-label="筛选">
                {[["attention", "待确认"], ["all", "全部"], ...Object.entries(CATS)].map(([k, label]) => {
                  const n = k === "attention" ? items.filter((i) => i.attention).length : k === "all" ? items.length : items.filter((i) => i.cat === k).length;
                  return <button key={k} type="button" className={filter === k ? "on" : ""} onClick={() => setFilter(k)} disabled={!n}>{label} {n}</button>;
                })}
              </div>
            </div>
            <div className="items">
              {shown.map((it) => (
                <button key={it.id} type="button" className={it.id === itemId ? "on" : ""} onClick={() => pickItem(it)}>
                  <span style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span className={`badge ${it.attention ? "warn" : ""}`}>{CATS[it.cat]}</span>
                    <span className="mono small">{mmss(it.time)}{it.line ? ` · L${String(it.line).padStart(2, "0")}` : ""}</span>
                  </span>
                  <span className="small">{it.text}</span>
                </button>
              ))}
              {!shown.length && <div className="muted small" style={{ padding: 16 }}>没有需要确认的条目。</div>}
            </div>
          </section>

          <section className="card" aria-labelledby="pending">
            <h2 id="pending">待保存的修正 {pending.length}</h2>
            {pending.map((p) => (
              <div key={p.key} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span>{p.label}</span>
                <button type="button" className="link" onClick={() => setPending((l) => l.filter((x) => x.key !== p.key))}>撤销</button>
              </div>
            ))}
            <p className="muted small" style={{ margin: 0 }}>保存到 corrections.yaml（按时间定位，重跑后编号变了也能套上），然后只重跑受影响的步骤。</p>
            <button type="button" className="btn primary" disabled={!pending.length || saving} onClick={saveAndRerun}>
              {saving ? "保存中…" : "保存并重跑"}
            </button>
          </section>
        </aside>
      </div>
    </main>
  );
}

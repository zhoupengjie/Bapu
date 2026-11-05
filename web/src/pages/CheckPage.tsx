import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import { api, audioUrl, mmss, type Review, type Score } from "../api";
import PianoRoll from "../components/PianoRoll";
import { t } from "../i18n";
import { Icon } from "../icons";
import { useThemeVersion } from "../theme";

// 检查: view and listen only. The list points at places that may be wrong; fixing happens in SV.

type Item = { id: string; time: number; why: string; line: number | null; note: number | null };

const STEMS = [["lead_dry", "主唱"], ["source", "原曲"], ["instrumental", "伴奏"]] as const;

function dictationWhy(kind: string) {
  if (kind === "insert") return "可能漏了字";
  if (kind === "alternative") return "听写改了字";
  return "听写和参考不一致";
}

/** Only the attention items, each with a short reason; never any lyric text. */
function buildItems(r: Review): Item[] {
  const items: Item[] = [];
  for (const l of r.lines) {
    if (!l.attention) continue;
    const start = l.t0 ?? l.start ?? 0;
    if (l.low_conf || l.fewer_notes || l.dictation_only) {
      items.push({ id: `l${l.line}`, time: start, why: "这句对不太准", line: l.line, note: l.first_note });
    }
    if (l.maybe_missing) {
      items.push({ id: `g${l.line}`, time: l.maybe_missing.time, why: "可能漏了字", line: l.line, note: l.maybe_missing.notes[0] ?? null });
    }
  }
  r.dictation.forEach((d, k) => {
    if (d.attention) items.push({ id: `d${k}`, time: d.time ?? 0, why: dictationWhy(d.kind), line: d.line, note: null });
  });
  r.merges.forEach((m, k) => {
    if (m.attention) items.push({ id: `m${k}`, time: m.time, why: "两个音合成了一个", line: m.line, note: m.note });
  });
  r.sustains.forEach((s, k) => {
    if (s.attention) items.push({ id: `s${k}`, time: s.time, why: "延音位置不确定", line: s.line, note: s.note });
  });
  return items.sort((a, b) => a.time - b.time);
}

/** mm:ss for the roll's range. */
function short(t: number) {
  const s = Math.max(0, Math.floor(t));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export default function CheckPage({ id }: { id: string }) {
  const enc = encodeURIComponent(id);
  const themeV = useThemeVersion();
  const [score, setScore] = useState<Score | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [stem, setStem] = useState<string>("lead_dry");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [span, setSpan] = useState(12);
  const [t0, setT0] = useState(0);
  const [selection, setSelection] = useState<ReadonlySet<number>>(new Set());
  const [selected, setSelected] = useState<number | null>(null);
  const [itemId, setItemId] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const waveEl = useRef<HTMLDivElement>(null);
  const ws = useRef<WaveSurfer | null>(null);
  const timeRef = useRef(0);
  timeRef.current = time;

  useEffect(() => {
    api.get<Score>(`/jobs/${enc}/score`).then(setScore).catch((e) => setError(t(e.message)));
    api.get<Review>(`/jobs/${enc}/review`).then(setReview).catch(() => {});
  }, [enc]);

  // audio + waveform; recreated for another track or when light / dark changes, keeping the position
  useEffect(() => {
    if (!waveEl.current) return;
    const style = getComputedStyle(document.documentElement);
    const css = (n: string, fallback: string) => style.getPropertyValue(n).trim() || fallback;
    const resume = timeRef.current;
    const w = WaveSurfer.create({
      container: waveEl.current, url: audioUrl(id, stem), height: 54, normalize: true,
      waveColor: css("--border-strong", css("--surface-3", "#d4d4d8")),
      progressColor: css("--accent", "#5b45f5"),
      cursorColor: css("--playhead", "#e5484d"), cursorWidth: 2, barWidth: 2, barGap: 1, barRadius: 1,
    });
    w.on("timeupdate", (t) => setTime(t));
    w.on("ready", (d) => {
      setDuration(d);
      if (resume > 0 && resume < d) w.setTime(resume);
    });
    w.on("play", () => setPlaying(true));
    w.on("pause", () => setPlaying(false));
    w.on("finish", () => setPlaying(false));
    w.on("error", () => setAudioError(t("这条音轨还没有生成")));
    ws.current = w;
    setAudioError(null);
    return () => {
      w.destroy();
      ws.current = null;
      setPlaying(false);
    };
  }, [id, stem, themeV]);

  // space plays / pauses
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.code !== "Space" || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(el.tagName)) return;
      e.preventDefault();
      ws.current?.playPause();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // keep the piano roll on the playhead while playing
  useEffect(() => {
    if (playing && (time < t0 || time > t0 + span * 0.85)) setT0(Math.max(0, time - span * 0.15));
  }, [time, playing, t0, span]);

  const seek = useCallback((t: number) => {
    const w = ws.current;
    if (w && w.getDuration() > 0) w.setTime(Math.max(0, t));
    setTime(Math.max(0, t));
  }, []);
  const select = useCallback((indices: number[], primary: number | null) => {
    setSelection(new Set(indices));
    setSelected(primary);
  }, []);

  const songLength = useMemo(
    () => duration || Math.max(0, ...(score?.notes.map((n) => n.start + n.dur) ?? [0])) + 2,
    [duration, score],
  );
  const zoom = (factor: number) => {
    const mid = t0 + span / 2;
    const ns = Math.min(Math.max(songLength, 10), Math.max(1.5, span * factor));
    setSpan(ns);
    setT0(Math.max(0, mid - ns / 2));
  };
  const move = (dir: number) => setT0(Math.max(0, Math.min(Math.max(0, songLength - span * 0.1), t0 + dir * span * 0.8)));

  const items = useMemo(() => (review ? buildItems(review) : []), [review]);
  const reviewLines = useMemo(() => {
    const att = new Set(review?.lines.filter((l) => l.attention).map((l) => l.line));
    return (score?.lines ?? []).map((l) => ({ ...l, attention: att.has(l.line) }));
  }, [score, review]);

  function pickItem(it: Item) {
    setItemId(it.id);
    const notes = score?.notes ?? [];
    const n = (it.note != null ? notes.find((x) => x.index === it.note) : undefined)
      ?? notes.find((x) => (it.line == null || x.line === it.line) && x.start + x.dur >= it.time - 0.05)
      ?? notes.find((x) => x.start >= it.time - 0.05);
    if (n) select([n.index], n.index);
    else select([], null);

    const ln = it.line != null ? score?.lines.find((l) => l.line === it.line) : undefined;
    const a = ln ? ln.t0 ?? ln.start : null;
    const b = ln ? ln.t1 ?? ln.end : null;
    const [from, to] = a != null && b != null && b > a ? [a, b] : [Math.max(0, it.time - 1), it.time + 4];
    setT0(Math.max(0, Math.min(from, it.time) - span * 0.15));
    seek(from);
    const w = ws.current;
    if (w && w.getDuration() > 0) w.play(from, to).catch(() => {});
  }

  return (
    <div className="review">
      <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>
        {(error || audioError) && <div className="notice bad">{error ?? audioError}</div>}
        <section className="card" aria-label={t("播放")} style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="player">
            <button type="button" className="btn primary" style={{ display: "flex", gap: 6, alignItems: "center" }}
              onClick={() => ws.current?.playPause()}>
              <Icon name={playing ? "pause" : "play"} size={16} />{playing ? t("暂停") : t("播放")}
            </button>
            <span className="mono">{mmss(time)}</span>
            <div className="seg" role="group" aria-label={t("音轨")}>
              {STEMS.map(([k, label]) => (
                <button key={k} type="button" className={stem === k ? "on" : ""} aria-pressed={stem === k} onClick={() => setStem(k)}>{t(label)}</button>
              ))}
            </div>
          </div>
          <div className="wave-box"><div ref={waveEl} /></div>
        </section>

        <section className="card roll" aria-label={t("钢琴卷帘")}>
          <div className="roll-head foot">
            <span className="mono muted" style={{ fontSize: 14 }}>{short(t0)} – {short(t0 + span)}</span>
            <span style={{ display: "flex", gap: 6 }}>
              <button type="button" className="icon-btn" aria-label={t("往前")} title={t("往前")} onClick={() => move(-1)}><Icon name="left" size={16} /></button>
              <button type="button" className="icon-btn" aria-label={t("往后")} title={t("往后")} onClick={() => move(1)}><Icon name="right" size={16} /></button>
              <button type="button" className="icon-btn" aria-label={t("缩小")} title={t("缩小")} onClick={() => zoom(1.5)}><Icon name="minus" size={16} /></button>
              <button type="button" className="icon-btn" aria-label={t("放大")} title={t("放大")} onClick={() => zoom(1 / 1.5)}><Icon name="plus" size={16} /></button>
              <span className="help">
                <button type="button" className="icon-btn" aria-label={t("操作和颜色说明")} aria-expanded={help} onClick={() => setHelp((h) => !h)}>
                  <Icon name="help" size={16} />
                </button>
                <div className="pop" hidden={!help}>
                  <b style={{ fontWeight: 500 }}>{t("颜色")}</b>
                  <span>
                    <span className="pill" style={{ background: "var(--word)", color: "var(--note-ink)" }}>{t("字")}</span>{" "}
                    <span className="pill" style={{ background: "var(--sustain)", color: "#3d2a0a" }}>-</span> {t("延音")}{" "}
                    <span className="pill" style={{ background: "var(--cont)", color: "var(--note-ink)" }}>+</span> {t("连音")}
                  </span>
                  <b style={{ fontWeight: 500, marginTop: 6 }}>{t("操作")}</b>
                  <span>{t("拖动或中键平移 · 滚轮上下")}</span>
                  <span>{t("Shift+滚轮左右 · Ctrl+滚轮缩放")}</span>
                  <span>{t("点标尺定位 · 空格播放")}</span>
                </div>
              </span>
            </span>
          </div>
          {score ? (
            <PianoRoll readOnly label={t("主唱")} notes={score.notes} lines={reviewLines} t0={t0} span={span} playhead={time}
              duration={songLength} selected={selection} primary={selected}
              bpm={score.tempo.bpm} beatsPerBar={parseInt(score.tempo.time_signature) || 4} height={360}
              onSelect={select} onSeek={seek} onView={(a, b) => { setT0(a); setSpan(b); }} />
          ) : (
            <div className="muted small" style={{ padding: 16 }}>{error ? t("没有读到乐谱") : t("正在读取乐谱…")}</div>
          )}
        </section>
      </div>

      <section className="card" aria-labelledby="check-title" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: 16 }}>
          <h1 id="check-title" style={{ fontSize: 18 }}>{review ? t("可能不准 {n} 处", { n: items.length }) : t("可能不准")}</h1>
          <p className="lead" style={{ fontSize: 14 }}>{t("点一处听一听，到 SV 里再改")}</p>
        </div>
        <div className="items">
          {items.map((it) => {
            const on = it.id === itemId;
            return (
              <button key={it.id} type="button" className={on ? "on" : ""} aria-pressed={on} onClick={() => pickItem(it)}>
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {on && <Icon name="play" size={14} />}{t(it.why)}
                </span>
                <span className="mono muted" style={{ fontSize: 13 }}>{mmss(it.time)}</span>
              </button>
            );
          })}
          {review && !items.length && <div className="muted small" style={{ padding: "4px 12px 12px" }}>{t("没有要提醒的地方。")}</div>}
          {!review && <div className="muted small" style={{ padding: "4px 12px 12px" }}>{t("正在读取…")}</div>}
        </div>
      </section>
    </div>
  );
}

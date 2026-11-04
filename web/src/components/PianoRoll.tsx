import { useEffect, useRef, useState } from "react";
import type { HarmonyNote, Kind, ScoreLine, ScoreNote } from "../api";

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
export const pitchName = (p: number) => `${NAMES[((p % 12) + 12) % 12]}${Math.floor(p / 12) - 1}`;

type Props = {
  notes: ScoreNote[];
  lines: (ScoreLine & { attention?: boolean })[];
  t0: number;
  span: number;
  playhead: number;
  selected: number | null; // note index (1-based)
  kindOverride: Record<number, Kind>;
  cuts: number[]; // boundary times of merged notes
  onSelect: (index: number) => void;
  onSeek: (t: number) => void;
  height?: number;
  harmony?: HarmonyNote[]; // drawn as outlines behind the lead
  selectedHarmony?: number | null;
};

function cssVar(el: Element, name: string) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

export default function PianoRoll({ notes, lines, t0, span, playhead, selected, kindOverride, cuts, onSelect, onSeek, height = 340, harmony = [], selectedHarmony = null }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const KEYS = 44;
  const TAGS = 22;

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const t1 = t0 + span;
  const visible = notes.filter((n) => n.start + n.dur >= t0 && n.start <= t1);
  const visibleH = harmony.filter((n) => n.start + n.dur >= t0 && n.start <= t1);
  const ps = visible.length || visibleH.length ? [...visible, ...visibleH].map((n) => n.pitch) : [60, 72];
  const pMax = Math.max(...ps) + 2;
  const pMin = Math.min(...ps) - 2;
  const rows = pMax - pMin + 1;
  const rowH = (height - TAGS) / rows;
  const x = (t: number) => KEYS + ((t - t0) / span) * (width - KEYS);
  const y = (p: number) => TAGS + (pMax - p) * rowH;

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = height * dpr;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const v = (n: string) => cssVar(c, n);
    g.fillStyle = v("--surface");
    g.fillRect(0, 0, width, height);
    // lanes and key names
    for (let p = pMin; p <= pMax; p++) {
      const black = [1, 3, 6, 8, 10].includes(((p % 12) + 12) % 12);
      g.fillStyle = black ? v("--surface-2") : v("--surface");
      g.fillRect(KEYS, y(p), width - KEYS, rowH);
      g.fillStyle = v("--muted");
      g.font = "10px ui-monospace, Consolas, monospace";
      if (!black) g.fillText(pitchName(p), 4, y(p) + rowH * 0.7);
    }
    // line bands and tags
    g.font = "11px system-ui, sans-serif";
    for (const ln of lines) {
      const a = ln.t0 ?? ln.start, b = ln.t1 ?? ln.end;
      if (a == null || b == null || b < t0 || a > t1) continue;
      if (ln.attention) {
        g.fillStyle = "rgba(242, 178, 102, 0.14)";
        g.fillRect(x(a), TAGS, x(b) - x(a), height - TAGS);
      }
      g.fillStyle = ln.attention ? v("--warn") : v("--muted");
      g.fillText(`L${String(ln.line).padStart(2, "0")}${ln.version === "manual" ? " 人工" : ""}`, Math.max(KEYS + 2, x(a) + 3), 15);
    }
    // harmony: outlined boxes with their own syllables
    for (const n of visibleH) {
      const nx = x(n.start), nw = Math.max(2, x(n.start + n.dur) - nx - 1);
      const ny = y(n.pitch) + 1, nh = Math.max(4, rowH - 2);
      g.fillStyle = "rgba(209, 107, 165, 0.18)";
      g.fillRect(nx, ny, nw, nh);
      g.strokeStyle = n.flags.some((f) => f === "alone" || f === "octave" || f === "late_onset") ? "#c2410c" : "#d16ba5";
      g.lineWidth = n.index === selectedHarmony ? 2.5 : 1.2;
      g.strokeRect(nx + 0.5, ny + 0.5, nw - 1, nh - 1);
      g.fillStyle = v("--ink");
      g.font = `${Math.min(12, nh - 3)}px system-ui, sans-serif`;
      g.save();
      g.beginPath();
      g.rect(nx, ny, nw, nh);
      g.clip();
      g.fillText(n.lyric, nx + 3, ny + nh * 0.75);
      g.restore();
    }
    // notes
    const fill: Record<Kind, string> = { word: v("--word"), sustain: v("--sustain"), cont: v("--cont") };
    for (const n of visible) {
      const kind = kindOverride[n.index] ?? n.kind;
      const nx = x(n.start), nw = Math.max(2, x(n.start + n.dur) - nx - 1);
      const ny = y(n.pitch) + 1, nh = Math.max(4, rowH - 2);
      g.fillStyle = fill[kind];
      g.beginPath();
      g.roundRect(nx, ny, nw, nh, 3);
      g.fill();
      if (n.index === selected) {
        g.strokeStyle = v("--ink");
        g.lineWidth = 2;
        g.strokeRect(nx - 2, ny - 2, nw + 4, nh + 4);
      }
      if (kindOverride[n.index]) {
        g.strokeStyle = v("--accent");
        g.setLineDash([3, 2]);
        g.lineWidth = 1.5;
        g.strokeRect(nx, ny, nw, nh);
        g.setLineDash([]);
      }
      const text = kind === "sustain" ? "-" : kind === "cont" ? "+" : n.lyric;
      g.fillStyle = kind === "sustain" ? "#3d2a0a" : "#fff";
      g.font = `${Math.min(13, nh - 3)}px system-ui, sans-serif`;
      g.save();
      g.beginPath();
      g.rect(nx, ny, nw, nh);
      g.clip();
      g.fillText(text, nx + 4, ny + nh * 0.75);
      g.restore();
    }
    // merge cuts
    g.strokeStyle = v("--ink");
    g.setLineDash([3, 3]);
    g.lineWidth = 1;
    for (const c0 of cuts) {
      if (c0 < t0 || c0 > t1) continue;
      g.beginPath();
      g.moveTo(x(c0), TAGS);
      g.lineTo(x(c0), height);
      g.stroke();
    }
    g.setLineDash([]);
    // playhead
    if (playhead >= t0 && playhead <= t1) {
      g.fillStyle = "#c2410c";
      g.fillRect(x(playhead) - 1, 0, 2, height);
    }
  }, [notes, lines, t0, span, playhead, selected, kindOverride, cuts, width, height, pMin, pMax, rowH, harmony, selectedHarmony]);

  function onClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    const t = t0 + ((px - KEYS) / (width - KEYS)) * span;
    const hit = visible.find((n) => t >= n.start && t <= n.start + n.dur && py >= y(n.pitch) && py <= y(n.pitch) + rowH);
    if (hit) onSelect(hit.index);
    else if (px > KEYS) onSeek(Math.max(0, t));
  }

  return (
    <div ref={wrap} style={{ width: "100%" }}>
      <canvas ref={canvas} onClick={onClick} style={{ width, height, display: "block", cursor: "pointer" }}
        role="img" aria-label={`钢琴卷帘 ${t0.toFixed(1)}–${t1.toFixed(1)} 秒`} />
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import type { Kind, ScoreLine } from "../api";
import { t, useLang } from "../i18n";
import { useThemeVersion } from "../theme";

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
export const pitchName = (p: number) => `${NAMES[((p % 12) + 12) % 12]}${Math.floor(p / 12) - 1}`;
const isBlack = (p: number) => [1, 3, 6, 8, 10].includes(((p % 12) + 12) % 12);

export type RollNote = { index: number; start: number; dur: number; pitch: number; kind: Kind; lyric: string; flags: string[] };

type Props = {
  notes: RollNote[];
  lines: (ScoreLine & { attention?: boolean })[];
  t0: number; // left edge of the view (s)
  span: number; // seconds shown
  duration: number; // length of the song (s)
  playhead: number;
  selected: ReadonlySet<number>; // note indices (1-based)
  primary: number | null; // the note shown in the inspector
  onSelect: (indices: number[], primary: number | null) => void;
  onSeek: (t: number) => void;
  onView: (t0: number, span: number) => void;
  label: string; // accessible name, e.g. "主唱"
  kindOverride?: Record<number, Kind>;
  cuts?: number[]; // boundary times of merged notes
  flagged?: (n: RollNote) => boolean; // drawn dashed with a ⚠
  bpm?: number;
  beatsPerBar?: number;
  height?: number;
  rowHeight?: number;
  /** View only: left-drag pans, a click on a note highlights it, no box selection. */
  readOnly?: boolean;
};

const KEYS = 44; // keyboard column
const RULER = 22; // bar numbers; click / drag to move the playhead
const TAGS = 18; // line tags
const TOP = RULER + TAGS;
const MIN_SPAN = 1.5;

function cssVar(el: Element, name: string) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

type Drag =
  | { mode: "pan"; x: number; y: number; t0: number; pTop: number; hit?: number | null; moved?: boolean }
  | { mode: "scrub" }
  | { mode: "box"; x0: number; y0: number; x1: number; y1: number; add: boolean };

/** Piano roll with SV-style navigation: left-drag selects a box, middle-drag pans,
 *  the wheel scrolls (Shift: sideways, Ctrl: zoom time), the ruler moves the playhead.
 *  With `readOnly`, left-drag pans too and a click on a note only highlights it. */
export default function PianoRoll({
  notes, lines, t0, span, duration, playhead, selected, primary, onSelect, onSeek, onView, label,
  kindOverride = {}, cuts = [], flagged, bpm, beatsPerBar = 4, height = 360, rowHeight = 14, readOnly = false,
}: Props) {
  const themeV = useThemeVersion(); // colours come from CSS vars: redraw when light / dark changes
  const lang = useLang(); // canvas text is translated: redraw when the language changes
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const [pTop, setPTop] = useState<number | null>(null); // highest visible pitch (may be fractional)
  const [drag, setDrag] = useState<Drag | null>(null);
  const rowH = rowHeight;
  const rows = (height - TOP) / rowH;

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // vertical limits: an octave around the song's range
  const [pLo, pHi] = useMemo(() => {
    if (!notes.length) return [48, 84];
    const ps = notes.map((n) => n.pitch);
    return [Math.max(0, Math.min(...ps) - 12), Math.min(127, Math.max(...ps) + 12)];
  }, [notes]);
  const clampTop = (p: number) => (pHi - pLo + 1 <= rows ? (pHi + pLo + rows) / 2 : Math.min(pHi + 1, Math.max(pLo + rows, p)));
  const clampT0 = (t: number, s = span) => Math.max(0, Math.min(Math.max(0, duration - s * 0.1), t));

  // start centred on the notes in view; follow the inspected note when it is out of view
  useEffect(() => {
    if (pTop != null || !notes.length) return;
    const inView = notes.filter((n) => n.start + n.dur >= t0 && n.start <= t0 + span);
    const ps = (inView.length ? inView : notes).map((n) => n.pitch).sort((a, b) => a - b);
    setPTop(clampTop(ps[Math.floor(ps.length / 2)] + rows / 2));
  }, [notes]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (primary == null || pTop == null) return;
    const n = notes.find((x) => x.index === primary);
    if (n && (n.pitch > pTop - 1 || n.pitch < pTop - rows + 1)) setPTop(clampTop(n.pitch + rows / 2));
  }, [primary]); // eslint-disable-line react-hooks/exhaustive-deps

  const top = pTop ?? 72;
  const t1 = t0 + span;
  const pxPerSec = (width - KEYS) / span;
  const x = (t: number) => KEYS + (t - t0) * pxPerSec;
  const timeAt = (px: number) => t0 + (px - KEYS) / pxPerSec;
  const y = (p: number) => TOP + (top - p - 1) * rowH;
  const visible = notes.filter((n) => n.start + n.dur >= t0 && n.start <= t1);

  const noteAt = (px: number, py: number) =>
    visible.find((n) => px >= x(n.start) - 1 && px <= x(n.start + n.dur) + 1 && py >= y(n.pitch) && py <= y(n.pitch) + rowH);

  // ---- drawing -------------------------------------------------------------------------
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = height * dpr;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const v = (n: string) => cssVar(c, n);
    const pFirst = Math.floor(top - rows - 1), pLast = Math.ceil(top);

    g.fillStyle = v("--surface");
    g.fillRect(0, 0, width, height);

    // note area, clipped below the ruler and right of the keys
    g.save();
    g.beginPath();
    g.rect(KEYS, TOP, width - KEYS, height - TOP);
    g.clip();
    for (let p = pFirst; p <= pLast; p++) {
      g.fillStyle = isBlack(p) ? v("--surface-2") : v("--surface");
      g.fillRect(KEYS, y(p), width - KEYS, rowH);
      if (((p % 12) + 12) % 12 === 0) {
        g.fillStyle = v("--border");
        g.fillRect(KEYS, y(p) + rowH - 1, width - KEYS, 1);
      }
    }
    // beat / bar grid
    const beat = bpm ? 60 / bpm : 1;
    const step = beat * pxPerSec < 6 ? beat * beatsPerBar : beat;
    for (let k = Math.ceil(t0 / step); k * step <= t1; k++) {
      const t = k * step;
      const bar = bpm ? Math.round(t / beat) % beatsPerBar === 0 : true;
      g.fillStyle = bar ? v("--border-strong") : v("--border");
      g.globalAlpha = bar ? 0.9 : 0.5;
      g.fillRect(Math.round(x(t)), TOP, 1, height - TOP);
    }
    g.globalAlpha = 1;
    // line starts
    for (const ln of lines) {
      const a = ln.t0 ?? ln.start;
      if (a == null || a < t0 || a > t1) continue;
      g.fillStyle = v("--muted");
      g.globalAlpha = 0.5;
      g.fillRect(Math.round(x(a)), TOP, 1, height - TOP);
      g.globalAlpha = 1;
    }
    // merge cuts
    g.strokeStyle = v("--muted");
    g.setLineDash([3, 3]);
    g.lineWidth = 1;
    for (const c0 of cuts) {
      if (c0 < t0 || c0 > t1) continue;
      g.beginPath();
      g.moveTo(x(c0), TOP);
      g.lineTo(x(c0), height);
      g.stroke();
    }
    g.setLineDash([]);
    // notes
    const fill: Record<Kind, string> = { word: v("--word"), sustain: v("--sustain"), cont: v("--cont") };
    for (const n of visible) {
      const kind = kindOverride[n.index] ?? n.kind;
      const nx = x(n.start), nw = Math.max(2, x(n.start + n.dur) - nx - 1);
      const ny = y(n.pitch) + 1, nh = rowH - 2;
      if (ny + nh < TOP || ny > height) continue;
      const warn = flagged?.(n) ?? false;
      const isSel = selected.has(n.index);
      g.fillStyle = fill[kind];
      g.beginPath();
      g.roundRect(nx, ny, nw, nh, 3);
      g.fill();
      if (warn || kindOverride[n.index]) {
        g.strokeStyle = warn ? v("--warn") : v("--accent");
        g.setLineDash([3, 2]);
        g.lineWidth = 2;
        g.strokeRect(nx - 1, ny - 1, nw + 2, nh + 2);
        g.setLineDash([]);
      }
      if (isSel) {
        g.strokeStyle = v("--ink");
        g.lineWidth = n.index === primary ? 2.5 : 1.5;
        g.strokeRect(nx - 2, ny - 2, nw + 4, nh + 4);
      }
      const text = (warn ? "⚠" : "") + (kind === "sustain" ? "-" : kind === "cont" ? "+" : n.lyric);
      g.fillStyle = kind === "sustain" ? "#3d2a0a" : v("--note-ink") || "#fff";
      g.font = `${Math.min(12, nh - 2)}px system-ui, sans-serif`;
      g.save();
      g.beginPath();
      g.rect(nx, ny, nw, nh);
      g.clip();
      g.fillText(text, nx + 3, ny + nh * 0.78);
      g.restore();
    }
    // selection box
    if (drag?.mode === "box") {
      const bx = Math.min(drag.x0, drag.x1), by = Math.min(drag.y0, drag.y1);
      g.fillStyle = v("--accent");
      g.globalAlpha = 0.12;
      g.fillRect(bx, by, Math.abs(drag.x1 - drag.x0), Math.abs(drag.y1 - drag.y0));
      g.globalAlpha = 1;
      g.strokeStyle = v("--accent");
      g.lineWidth = 1;
      g.strokeRect(bx + 0.5, by + 0.5, Math.abs(drag.x1 - drag.x0), Math.abs(drag.y1 - drag.y0));
    }
    g.restore();

    // ruler: bar numbers
    g.fillStyle = v("--surface-2");
    g.fillRect(KEYS, 0, width - KEYS, RULER);
    g.fillStyle = v("--surface");
    g.fillRect(KEYS, RULER, width - KEYS, TAGS);
    g.fillStyle = v("--border");
    g.fillRect(0, RULER - 1, width, 1);
    g.fillRect(0, TOP - 1, width, 1);
    g.save();
    g.beginPath();
    g.rect(KEYS, 0, width - KEYS, TOP);
    g.clip();
    g.font = "11px ui-monospace, Consolas, monospace";
    if (bpm) {
      const barLen = beat * beatsPerBar;
      const every = Math.max(1, Math.ceil(36 / (barLen * pxPerSec))); // label spacing ≥ 36 px
      for (let b = Math.floor(t0 / barLen); b * barLen <= t1; b++) {
        const bx = x(b * barLen);
        g.fillStyle = v("--muted");
        g.fillRect(Math.round(bx), RULER - 8, 1, 7);
        if (b % every === 0) g.fillText(String(b + 1), bx + 3, 13);
        if (beat * pxPerSec >= 8) {
          for (let k = 1; k < beatsPerBar; k++) g.fillRect(Math.round(x((b * beatsPerBar + k) * beat)), RULER - 4, 1, 3);
        }
      }
    } else {
      for (let s = Math.ceil(t0); s <= t1; s++) {
        g.fillStyle = v("--muted");
        g.fillRect(Math.round(x(s)), RULER - 6, 1, 5);
        if (s % Math.max(1, Math.ceil(40 / pxPerSec)) === 0) g.fillText(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`, x(s) + 3, 13);
      }
    }
    // line tags; lines to review get an orange strip
    g.font = "11px system-ui, sans-serif";
    for (const ln of lines) {
      const a = ln.t0 ?? ln.start, b = ln.t1 ?? ln.end;
      if (a == null || b == null || b < t0 || a > t1) continue;
      if (ln.attention) {
        g.fillStyle = v("--warn");
        g.fillRect(x(a), TOP - 4, x(b) - x(a), 3);
      }
      g.fillStyle = ln.attention ? v("--warn") : v("--muted");
      g.fillText(t("第{n}句", { n: ln.line }), Math.max(KEYS + 2, x(a) + 3), RULER + 12);
    }
    g.restore();

    // keyboard column
    g.save();
    g.beginPath();
    g.rect(0, TOP, KEYS, height - TOP);
    g.clip();
    for (let p = pFirst; p <= pLast; p++) {
      g.fillStyle = isBlack(p) ? v("--surface-2") : v("--surface");
      g.fillRect(0, y(p), KEYS, rowH);
      if (!isBlack(p)) {
        g.fillStyle = ((p % 12) + 12) % 12 === 0 ? v("--ink") : v("--muted");
        g.font = "10px ui-monospace, Consolas, monospace";
        g.fillText(pitchName(p), 4, y(p) + rowH * 0.75);
      }
    }
    g.restore();
    g.fillStyle = v("--border");
    g.fillRect(KEYS - 1, 0, 1, height);
    g.fillStyle = v("--surface-2");
    g.fillRect(0, 0, KEYS - 1, TOP - 1);
    g.fillStyle = v("--muted");
    g.font = "10px system-ui, sans-serif";
    g.fillText(bpm ? t("小节") : t("时间"), 6, 14);

    // playhead
    if (playhead >= t0 && playhead <= t1) {
      const px = Math.round(x(playhead));
      g.fillStyle = v("--playhead");
      g.fillRect(px - 1, 0, 2, height);
      g.beginPath();
      g.moveTo(px - 6, 0);
      g.lineTo(px + 6, 0);
      g.lineTo(px, 8);
      g.fill();
    }
  }, [notes, lines, t0, span, playhead, selected, primary, kindOverride, cuts, flagged, width, height, top, drag, bpm, beatsPerBar, themeV, lang]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- wheel: scroll (Shift: sideways), Ctrl: zoom time around the pointer ---------------
  const view = useRef({ t0, span, top, duration, width });
  view.current = { t0, span, top, duration, width };
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { t0: vt0, span: vs, top: vtop, duration: vd, width: vw } = view.current;
      const unit = e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1; // lines / pages -> px
      const dy = e.deltaY * unit, dx = e.deltaX * unit;
      const rect = c.getBoundingClientRect();
      const px = e.clientX - rect.left;
      if (e.ctrlKey) {
        const ns = Math.min(Math.max(vd, 10), Math.max(MIN_SPAN, vs * Math.exp(dy * 0.0015)));
        const anchor = vt0 + (Math.max(KEYS, px) - KEYS) / (vw - KEYS) * vs;
        const nt0 = anchor - (Math.max(KEYS, px) - KEYS) / (vw - KEYS) * ns;
        onView(Math.max(0, Math.min(Math.max(0, vd - ns * 0.1), nt0)), ns);
      } else if (e.shiftKey || Math.abs(dx) > Math.abs(dy)) {
        const d = e.shiftKey ? dy || dx : dx;
        onView(Math.max(0, Math.min(Math.max(0, vd - vs * 0.1), vt0 + (d / (vw - KEYS)) * vs)), vs);
      } else {
        setPTop(clampTop(vtop - (dy / 100) * 3)); // 3 semitones per wheel notch
      }
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => c.removeEventListener("wheel", onWheel);
  }); // re-attached each render so it sees the current limits

  // ---- mouse: left = select (box on empty space), middle = pan, ruler = playhead --------
  // (readOnly: left = pan, a click without moving on a note highlights it)
  function point(e: React.PointerEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const [px, py] = point(e);
    e.currentTarget.focus();
    if (e.button === 1) {
      e.preventDefault(); // no autoscroll
      e.currentTarget.setPointerCapture(e.pointerId);
      setDrag({ mode: "pan", x: px, y: py, t0, pTop: top });
      return;
    }
    if (e.button !== 0 || px < KEYS) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    if (py < TOP) {
      onSeek(Math.max(0, timeAt(px)));
      setDrag({ mode: "scrub" });
      return;
    }
    if (readOnly) {
      setDrag({ mode: "pan", x: px, y: py, t0, pTop: top, hit: noteAt(px, py)?.index ?? null, moved: false });
      return;
    }
    const add = e.ctrlKey || e.metaKey || e.shiftKey;
    const hit = noteAt(px, py);
    if (hit) {
      if (add) {
        const next = new Set(selected);
        if (next.has(hit.index)) next.delete(hit.index);
        else next.add(hit.index);
        onSelect([...next], next.has(hit.index) ? hit.index : [...next][0] ?? null);
      } else if (!selected.has(hit.index)) onSelect([hit.index], hit.index);
      else onSelect([...selected], hit.index);
      return;
    }
    setDrag({ mode: "box", x0: px, y0: py, x1: px, y1: py, add });
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    const [px, py] = point(e);
    const c = e.currentTarget;
    if (!drag) {
      c.style.cursor = px >= KEYS && py < TOP ? "pointer" : readOnly ? (noteAt(px, py) ? "pointer" : px >= KEYS ? "grab" : "default")
        : noteAt(px, py) ? "default" : "crosshair";
      return;
    }
    if (drag.mode === "pan") {
      const moved = drag.moved || Math.abs(px - drag.x) > 3 || Math.abs(py - drag.y) > 3;
      if (moved !== drag.moved) setDrag({ ...drag, moved });
      if (!moved) return;
      c.style.cursor = "grabbing";
      const nt0 = clampT0(drag.t0 - (px - drag.x) / pxPerSec);
      setPTop(clampTop(drag.pTop + (py - drag.y) / rowH));
      if (nt0 !== t0) onView(nt0, span);
    } else if (drag.mode === "scrub") {
      onSeek(Math.max(0, timeAt(Math.max(KEYS, px))));
    } else {
      setDrag({ ...drag, x1: Math.max(KEYS, Math.min(width, px)), y1: Math.max(TOP, Math.min(height, py)) });
    }
  }

  function onPointerUp(e: React.PointerEvent<HTMLCanvasElement>) {
    const d = drag;
    setDrag(null);
    e.currentTarget.style.cursor = "";
    if (d?.mode === "pan" && !d.moved && d.hit != null) onSelect([d.hit], d.hit);
    if (d?.mode !== "box") return;
    const [bx0, bx1] = [Math.min(d.x0, d.x1), Math.max(d.x0, d.x1)];
    const [by0, by1] = [Math.min(d.y0, d.y1), Math.max(d.y0, d.y1)];
    if (bx1 - bx0 < 3 && by1 - by0 < 3) { // a click on empty space
      if (!d.add) onSelect([], null);
      return;
    }
    const inBox = visible
      .filter((n) => x(n.start) < bx1 && x(n.start + n.dur) > bx0 && y(n.pitch) < by1 && y(n.pitch) + rowH > by0)
      .sort((a, b) => a.start - b.start)
      .map((n) => n.index);
    const next = d.add ? [...new Set([...selected, ...inBox])] : inBox;
    onSelect(next, inBox[0] ?? (d.add ? primary : null));
  }

  return (
    <div ref={wrap} style={{ width: "100%" }}>
      <canvas ref={canvas} tabIndex={0} style={{ width, height, display: "block", outline: "none", touchAction: "none" }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={() => setDrag(null)}
        onAuxClick={(e) => e.preventDefault()} onContextMenu={(e) => e.preventDefault()}
        onKeyDown={(e) => { if (e.key === "Escape" && !readOnly) onSelect([], null); }}
        role="img" aria-label={t("{label}：{a}–{b} 秒，{n} 个音符，已选 {k} 个", { label, a: t0.toFixed(1), b: t1.toFixed(1), n: visible.length, k: selected.size })} />
    </div>
  );
}

import { useRef, type ReactNode } from "react";
import { t } from "../i18n";

// The graph of the 流程 page, drawn as SVG with a fixed layout: an 输入 column on the
// left (the files the user gives: the song, optionally a notes file and reference
// lyrics), then the three processing nodes 分离 → 音符 → 歌词. Sockets are inputs and
// outputs, wires the artifacts that flow between them. Nothing is draggable: the shape
// never changes, only the states do. Clicking a node selects it (the page shows its panel
// below); clicking a socket asks the page for that socket's menu (give a file, save a
// result), which the page renders inside this component's canvas at the anchor it gets.

export type NodeId = "separate" | "notes" | "lyrics";
/** Where a socket lives: a processing node, or the 输入 column. */
export type Port = NodeId | "inputs";
export type NodeState = "empty" | "stale" | "done" | "running" | "failed";
export type SocketKind = "audio" | "notes" | "data";
export type Socket = {
  id: string;
  label: string;
  on: boolean;
  kind: SocketKind;
  optional?: boolean; // an input the node can run without
  file?: string; // the file the user gave for this input (shown with the label)
  hint?: string; // 输入 column only: what to give, shown while nothing is given
};
export type NodeView = {
  id: NodeId;
  title: string;
  steps: string; // "步骤 1–3"
  state: NodeState;
  status?: string; // short text next to the state, e.g. the running step
  inputs: Socket[];
  outputs: Socket[];
};
export type Wire = { from: [Port, string]; to: [NodeId, string]; kind: SocketKind; on: boolean };
export type Side = "in" | "out";
/** Where a socket is on the canvas (CSS pixels from the canvas's top-left), for a menu under it. */
export type Anchor = { left: number; top: number; width: number };

const IN_X = 10;
const IN_W = 150;
const IN_ROW = 40;
const NODE_X0 = 190;
const NODE_STEP = 230;
const W = 200;
const HEAD = 48;
const ROW = 24;
const LABEL = 18;
const LANE = 12;

const COLOR: Record<SocketKind, string> = { audio: "var(--ok)", notes: "var(--accent)", data: "var(--muted)" };
const STATE_LABEL: Record<NodeState, string> = { empty: "", stale: "要重算", done: "已算好", running: "运行中", failed: "出错了" };
const STATE_FILL: Record<NodeState, [string, string]> = {
  empty: ["transparent", "var(--muted)"], stale: ["var(--warn-soft)", "var(--warn)"], done: ["var(--ok-soft)", "var(--ok)"],
  running: ["var(--accent-soft)", "var(--accent-text)"], failed: ["var(--warn-soft)", "var(--warn)"],
};

type Geo = { x: number; y: number; w: number; h: number; inY: (k: number) => number; outY: (k: number) => number };

function nodeGeometry(n: NodeView, top: number, column: number): Geo {
  const inTop = top + HEAD + LABEL;
  const outLabel = inTop + n.inputs.length * ROW + 6;
  const outTop = outLabel + LABEL;
  const h = outTop + n.outputs.length * ROW + 10 - top;
  return { x: NODE_X0 + column * NODE_STEP, y: top, w: W, h, inY: (k) => inTop + k * ROW + ROW / 2, outY: (k) => outTop + k * ROW + ROW / 2 };
}

function inputsGeometry(items: Socket[], top: number): Geo {
  const first = top + HEAD + 2;
  const h = HEAD + 2 + items.length * IN_ROW + 8;
  return { x: IN_X, y: top, w: IN_W, h, inY: () => 0, outY: (k) => first + k * IN_ROW + IN_ROW / 2 };
}

function wirePath(x1: number, y1: number, x2: number, y2: number, lane: number | null, yLane: number): string {
  if (lane === null) return `M${x1} ${y1} C${x1 + 16} ${y1},${x2 - 16} ${y2},${x2} ${y2}`;
  return `M${x1} ${y1} C${x1 + 14} ${y1},${x1 + 14} ${yLane},${x1 + 36} ${yLane} L${x2 - 36} ${yLane} C${x2 - 14} ${yLane},${x2 - 14} ${y2},${x2} ${y2}`;
}

/** A file name short enough for a socket label; `max` in half-width units (a CJK character is two). */
function short(name: string, max = 24): string {
  let units = 0;
  for (let i = 0; i < name.length; i++) {
    units += /[⺀-鿿가-힯＀-￯]/.test(name[i]) ? 2 : 1;
    if (units > max) return `${name.slice(0, Math.max(1, i))}…`;
  }
  return name;
}

export default function FlowGraph({ inputs, nodes, wires, selected, onSelect, onSocket, children }: {
  inputs: Socket[]; nodes: NodeView[]; wires: Wire[]; selected: NodeId;
  onSelect: (id: NodeId) => void;
  onSocket: (port: Port, side: Side, socket: string, anchor: Anchor) => void;
  children?: ReactNode; // the open socket menu, positioned by the page
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n])) as Record<NodeId, NodeView>;
  const ORDER: Port[] = ["inputs", ...nodes.map((n) => n.id)];
  const viewW = NODE_X0 + nodes.length * NODE_STEP - (NODE_STEP - W) + 10;

  // Wires that skip a node run in lanes: from the 输入 column above the nodes, between
  // nodes below them. Count the lanes first, since they decide where the nodes sit.
  const skipping = (w: Wire) => ORDER.indexOf(w.to[0]) - ORDER.indexOf(w.from[0]) > 1;
  const topLanes = wires.filter((w) => w.from[0] === "inputs" && skipping(w)).length;
  const top = 10 + (topLanes ? topLanes * LANE + 14 : 6);
  const geo = Object.fromEntries(nodes.map((n, i) => [n.id, nodeGeometry(n, top, i)])) as Record<Port, Geo>;
  geo.inputs = inputsGeometry(inputs, top);
  const bottom = Math.max(...ORDER.map((p) => geo[p].y + geo[p].h));

  let topUsed = 0;
  let bottomUsed = 0;
  const paths = wires.map((w) => {
    const ga = geo[w.from[0]], gb = geo[w.to[0]];
    const ka = w.from[0] === "inputs" ? inputs.findIndex((s) => s.id === w.from[1]) : byId[w.from[0] as NodeId].outputs.findIndex((s) => s.id === w.from[1]);
    const kb = byId[w.to[0]].inputs.findIndex((s) => s.id === w.to[1]);
    let lane: number | null = null;
    let yLane = 0;
    if (skipping(w)) {
      if (w.from[0] === "inputs") {
        lane = topUsed++;
        yLane = 10 + (topLanes - 1 - lane) * LANE;
      } else {
        lane = bottomUsed++;
        yLane = bottom + 14 + lane * LANE;
      }
    }
    return { d: wirePath(ga.x + ga.w, ga.outY(ka), gb.x, gb.inY(kb), lane, yLane), kind: w.kind, on: w.on };
  });
  const height = bottom + 14 + bottomUsed * LANE + 12;

  const socketClick = (e: React.MouseEvent | React.KeyboardEvent, port: Port, side: Side, id: string) => {
    e.stopPropagation();
    const r = (e.currentTarget as Element).querySelector("circle")!.getBoundingClientRect();
    const c = canvas.current?.getBoundingClientRect();
    if (!c) return;
    onSocket(port, side, id, { left: r.left - c.left + r.width / 2, top: r.bottom - c.top + 6, width: c.width });
  };
  const socketProps = (port: Port, side: Side, s: Socket, name: string) => ({
    className: "flow-socket", role: "button" as const, tabIndex: 0, "aria-label": name,
    onClick: (e: React.MouseEvent) => socketClick(e, port, side, s.id),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        socketClick(e, port, side, s.id);
      }
    },
  });
  const dot = (cx: number, cy: number, s: Socket, pick: boolean) => (
    <>
      <circle cx={cx} cy={cy} r={pick ? 6 : 5} fill={s.on ? COLOR[s.kind] : "var(--surface-2)"}
        stroke={COLOR[s.kind]} strokeWidth={2} strokeDasharray={pick ? "2.5 2" : undefined} />
      {pick && <path d={`M${cx - 2.5} ${cy}h5M${cx} ${cy - 2.5}v5`} stroke={COLOR[s.kind]} strokeWidth={1.5} strokeLinecap="round" />}
    </>
  );

  const gi = geo.inputs;
  return (
    <div className="flow-canvas" ref={canvas}>
      <svg className="flow-graph" width={viewW} height={height} viewBox={`0 0 ${viewW} ${height}`} role="group" aria-label={t("流程图")}>
        {paths.map((p, i) => (
          <path key={i} d={p.d} fill="none" stroke={p.on ? COLOR[p.kind] : "var(--faint)"} strokeWidth={p.on ? 2 : 1.5}
            strokeDasharray={p.on ? undefined : "4 5"} strokeLinecap="round" />
        ))}

        <g className="flow-inputs" aria-label={t("输入")}>
          <rect x={gi.x} y={gi.y} width={gi.w} height={gi.h} rx={12} fill="var(--surface-2)" stroke="var(--border)" strokeDasharray="5 4" />
          <text x={gi.x + 12} y={gi.y + 28} fontSize={14.5} fontWeight={500} fill="var(--ink)">{t("输入")}</text>
          <text x={gi.x + gi.w - 12} y={gi.y + 28} fontSize={12} textAnchor="end" fill="var(--faint)">{t("点一下选文件")}</text>
          {inputs.map((s, k) => {
            const y = gi.outY(k);
            const pick = !!s.optional && !s.on;
            const name = s.file ? `${s.label}：${s.file}` : s.label;
            return (
              <g key={s.id} {...socketProps("inputs", "out", s, name)}>
                <rect x={gi.x + 4} y={y - IN_ROW / 2} width={gi.w} height={IN_ROW} fill="transparent" />
                <text x={gi.x + 12} y={y - 3} fontSize={13.5} fontWeight={s.on ? 500 : 400} fill={s.on || !s.optional ? "var(--ink)" : "var(--muted)"}>
                  {s.label}{pick ? ` · ${t("可选")}` : ""}
                </text>
                <text x={gi.x + 12} y={y + 13} fontSize={12} fill={s.file ? "var(--accent-text)" : "var(--faint)"}>
                  {s.file ? short(s.file, 22) : s.hint ?? ""}
                </text>
                {dot(gi.x + gi.w, y, s, pick)}
              </g>
            );
          })}
        </g>

        {nodes.map((n) => {
          const g = geo[n.id];
          const on = n.id === selected;
          const [bg, fg] = STATE_FILL[n.state];
          const state = STATE_LABEL[n.state] ? t(STATE_LABEL[n.state]) : "";
          const pill = state ? state + (n.status ? ` · ${n.status}` : "") : n.status ?? "";
          const pillW = Math.max(0, pill.length * 8 + 16);
          return (
            <g key={n.id} className="flow-node" role="button" tabIndex={0} aria-pressed={on}
              aria-label={`${n.title}${pill ? `，${pill}` : ""}`}
              onClick={() => onSelect(n.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n.id); } }}>
              <rect className="flow-box" x={g.x} y={g.y} width={g.w} height={g.h} rx={12} fill="var(--surface-2)"
                stroke={on ? "var(--accent)" : n.state === "running" ? "var(--accent)" : "var(--border)"} strokeWidth={on ? 1.5 : 1} />
              <text x={g.x + 12} y={g.y + 28} fontSize={14.5} fontWeight={500} fill="var(--ink)">{n.title}</text>
              {pill ? (
                <g>
                  <rect x={g.x + g.w - 12 - pillW} y={g.y + 13} width={pillW} height={22} rx={11} fill={bg} />
                  <text x={g.x + g.w - 12 - pillW / 2} y={g.y + 28} fontSize={12} fontWeight={500} textAnchor="middle" fill={fg}>{pill}</text>
                </g>
              ) : (
                <text x={g.x + g.w - 12} y={g.y + 28} fontSize={12} textAnchor="end" fill="var(--faint)">{n.steps}</text>
              )}
              <text x={g.x + 12} y={g.y + HEAD + 11} fontSize={12} fill="var(--faint)">{t("输入")}</text>
              {n.inputs.map((s, k) => {
                const y = g.inY(k);
                const pick = !!s.optional && !s.on; // a file the user may give: drawn as an empty, dashed socket
                const text = s.file ? short(s.file) : s.label;
                return (
                  <g key={s.id} {...socketProps(n.id, "in", s, s.file ? `${s.label}：${s.file}` : s.label)}>
                    <rect x={g.x - 8} y={y - 11} width={g.w - 4} height={ROW} fill="transparent" />
                    {dot(g.x, y, s, pick)}
                    <text x={g.x + 12} y={y + 5} fontSize={13.5} fill={s.file ? "var(--accent-text)" : s.on || !s.optional ? "var(--ink)" : "var(--muted)"}>
                      {text}{pick ? ` · ${t("可选")}` : ""}
                    </text>
                  </g>
                );
              })}
              <text x={g.x + 12} y={g.inY(n.inputs.length) + 3} fontSize={12} fill="var(--faint)">{t("输出")}</text>
              {n.outputs.map((s, k) => {
                const y = g.outY(k);
                return (
                  <g key={s.id} {...socketProps(n.id, "out", s, s.label)}>
                    <rect x={g.x + 12} y={y - 11} width={g.w - 4} height={ROW} fill="transparent" />
                    <text x={g.x + g.w - 12} y={y + 5} fontSize={13.5} textAnchor="end" fill={s.on ? "var(--ink)" : "var(--muted)"}>{s.label}</text>
                    {dot(g.x + g.w, y, s, false)}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      {children}
    </div>
  );
}

// What each pipeline step does, in words a user can follow, and how to read its results.

export type StepMeta = { what: string; summary: (m: Record<string, unknown>) => string };

const n = (v: unknown) => (typeof v === "number" ? v : null);
const join = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" · ");

export const STEPS: Record<string, StepMeta> = {
  separate: {
    what: "把录音里的人声和伴奏分开",
    summary: () => "得到人声、伴奏两条音轨",
  },
  lead: {
    what: "从人声里去掉伴唱，再去混响，留下主唱干声",
    summary: () => "得到主唱、伴唱、主唱干声",
  },
  tempo: {
    what: "测出歌的速度和第一拍的位置",
    summary: (m) => join(n(m.bpm) != null && `${m.bpm} BPM`,
      m.confidence ? `可信度${({ high: "高", medium: "中", low: "低" } as Record<string, string>)[String(m.confidence)] ?? m.confidence}` : null),
  },
  transcribe: {
    what: "把主唱的旋律转成一个个音符",
    summary: (m) => join(n(m.notes) != null && `${m.notes} 个音符`, !!m.range && `音域 ${m.range}`),
  },
  splits: {
    what: "把被切成两段的同一个音合回去",
    summary: (m) => join(
      n(m.notes_in) != null && `${m.notes_in} → ${m.notes} 个音符`,
      n(m.auto_merged) ? `自动合并 ${m.auto_merged} 处` : null,
      n(m.user_decided) ? `按你的决定 ${m.user_decided} 处` : null,
    ),
  },
  dictate: {
    what: "用听写模型从录音里听出歌词",
    summary: (m) => join(n(m.phrases) != null && `${m.phrases} 个乐句`, n(m.tokens) != null && `${m.tokens} 个字词`),
  },
  lyrics: {
    what: "把听写结果和参考歌词合在一起，定下每句的字",
    summary: (m) => join(
      n(m.lines) != null && `${m.lines} 句`,
      n(m.agreed) != null && `${m.agreed} 个字两边一致`,
      n(m.alternatives) ? `${m.alternatives} 处有出入` : null,
    ),
  },
  align: {
    what: "找出每个字在录音里唱出的时间",
    summary: (m) => join(
      n(m.tokens) != null && `${m.tokens} 个字`,
      n(m.lines_conf_below_threshold) ? `${m.lines_conf_below_threshold} 句不太确定` : "各句都对得上",
    ),
  },
  assign: {
    what: "把字放到音符上，标出延音 - 和连音 +",
    summary: (m) => join(
      n(m.syllables) != null && `${m.syllables} 个字`,
      n(m.sustain) != null && `${m.sustain} 个 -`,
      n(m.cont) != null && `${m.cont} 个 +`,
    ),
  },
};

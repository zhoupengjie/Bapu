"""After a run: write `score.json`, `review.json` and `review.md` at the work dir root.

The review report lists every uncertain automatic decision with its time, note
index and line number, and every correction that no longer applies. It never
contains lyric text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bapu import corrections as corr
from bapu.stages import STAGES
from bapu.workdir import WorkDir, write_json_atomic

STRONG_ONSET = 0.5  # a "-" on a note whose start looks like a new syllable


def mmss(t: float | None) -> str:
    if t is None:
        return "—"
    m = int(t // 60)
    return f"{m:02d}:{t - m * 60:05.2f}"


def _read(wd: WorkDir, stage: str, name: str) -> Any:
    rec = wd.get(stage)
    if rec is None:
        return None
    p = wd.root / rec.dir / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def finalize(wd: WorkDir) -> dict[str, Any] | None:
    rec = wd.get("assign")
    if rec is None:
        return None
    score = json.loads((wd.root / rec.outputs["score"]).read_text(encoding="utf-8"))
    manifest = wd.load()
    score["source"] = {n: {"sha256": s["sha256"]} for n, s in manifest.get("sources", {}).items()}
    score["models"] = {
        f"{st.name}.{p}": manifest["stages"].get(st.name, {}).get("params", {}).get(p)
        for st in STAGES for p in st.model_params
    }
    harmony = _read(wd, "harmony", "harmony.json")
    score["harmony"] = harmony["notes"] if harmony else []
    write_json_atomic(wd.root / "score.json", score)

    review = build_review(wd, score)
    write_json_atomic(wd.root / "review.json", review)
    (wd.root / "review.md").write_text(render_md(review), encoding="utf-8")
    s = review["summary"]
    summary = f"待确认 {s['attention']} 条，失效修正 {s['stale_corrections']} 条"
    return {"score": wd.root / "score.json", "review_md": wd.root / "review.md", "summary": summary}


def build_review(wd: WorkDir, score: dict[str, Any]) -> dict[str, Any]:
    notes = score["notes"]
    by_orig: dict[int, dict[str, Any]] = {}
    for n in notes:
        for o in n["merged_from"]:
            by_orig[o] = n

    merges = []
    for b in _read(wd, "splits", "boundaries.json") or []:
        if not (b["merge"] or b["source"] == "user"):
            continue
        n = by_orig.get(b["first"] + 1)  # the note the boundary's first half belongs to
        status = ("待确认" if b["merge"] else "") if b["source"] == "auto" else ("已确认合并" if b["merge"] else "已拒绝合并")
        merges.append({"time": b["time"], "note": n["index"] if n else None, "line": n["line"] if n else None,
                       "score": b["score"], "status": status, "attention": b["source"] == "auto"})

    sustains = []
    prev_end = None
    for n in notes:
        if n["kind"] == "sustain":
            after_rest = "sustain_after_rest" in n["flags"]
            strong = n["conf"]["onset"] is not None and n["conf"]["onset"] >= STRONG_ONSET
            sustains.append({"time": n["start"], "note": n["index"], "line": n["line"],
                             "onset": n["conf"]["onset"], "after_rest": after_rest,
                             "fixed": "kind_fixed" in n["flags"] or "manual_line" in n["flags"],
                             "attention": (after_rest or strong) and not ("kind_fixed" in n["flags"] or "manual_line" in n["flags"])})
        prev_end = n["start"] + n["dur"]
    del prev_end

    # Possible words missing from the subtitle: two or more "-" in a row where
    # one of them starts like a new syllable. (A long run alone is common: one
    # word sung over several notes.)
    missing: dict[int, dict[str, Any]] = {}
    run: list[dict[str, Any]] = []
    for n in notes + [{"kind": "end"}]:
        if n["kind"] == "sustain" and (not run or n["line"] == run[0]["line"]):
            run.append(n)
            continue
        strong = [x for x in run if x["conf"]["onset"] is not None and x["conf"]["onset"] >= STRONG_ONSET]
        if len(run) >= 2 and strong and "manual_line" not in run[0]["flags"]:
            missing.setdefault(run[0]["line"], {"notes": [x["index"] for x in run], "time": run[0]["start"]})
        run = [n] if n["kind"] == "sustain" else []

    threshold = (wd.get("assign").params or {}).get("line_conf_threshold", 0.15)
    lines = []
    for ln in score["lines"]:
        low = ln["conf"] is not None and ln["conf"] < threshold and ln["version"] == "auto"
        short = ln["notes"] < ln["syllables"]
        miss = missing.get(ln["line"])
        lines.append({**ln, "low_conf": low, "fewer_notes": short, "maybe_missing": miss,
                      "attention": low or short or bool(miss)})

    # Corrections: which entries no longer apply, which conflict.
    entries = corr.load(wd.corrections_path) if wd.corrections_path.exists() else []
    used: set[int] = set()
    handled: set[str] = set()
    for st in STAGES:
        if st.corrections and wd.get(st.name) is not None:
            handled.update(st.corrections)
            used.update(_read(wd, st.name, "corrections_used.json") or [])
    conflicts = _read(wd, "assign", "conflicts.json") or []
    conflict_ids = {c["id"] for c in conflicts}
    stale = [{"id": e["_id"], "type": e["type"], "at": e.get("at"), "line": e.get("line")}
             for e in entries if e["type"] in handled and e["_id"] not in used]

    # What dictation changed or flagged (times and line numbers only).
    ali = _read(wd, "align", "alignment.json") or {}
    mode = ali.get("mode") or "fused"
    dictation = []
    for d in ali.get("decisions", []):
        if d["type"] == "insert":
            dictation.append({"kind": "insert", "line": d["line"], "time": d["time"], "conf": d["conf"],
                              "status": "已补上（参考歌词里没有）" if d["kept"] else "录音不支持，没补",
                              "attention": d["kept"]})
        else:
            dictation.append({"kind": "alternative", "line": d["line"], "time": d["time"],
                              "conf": d["conf_dictation"] if d["replaced"] else d["conf_reference"],
                              "status": "改用了听写的字" if d["replaced"] else "保留参考歌词的字",
                              "attention": d["replaced"]})
    for e in _read(wd, "lyrics", "fusion.json") or []:
        if e["type"] in ("differs", "extra_ignored"):
            why = {"english": "有英文，听写不可靠", "length": "字数不同", "too_long": "听写多出的字太多",
                   "locked": "这一句你修正过"}.get(e.get("reason", ""), "")
            dictation.append({"kind": e["type"], "line": e["line"], "time": e.get("time"), "conf": None,
                              "status": f"听写和参考不一致，保留参考（{why}）", "attention": e.get("reason") == "length"})
    dictation.sort(key=lambda x: (x["line"], x["time"] or 0))

    why = {"alone": "和声单独唱，歌词是占位的", "octave": "和主唱差一个八度以上，可能转错八度",
           "late_onset": "起音和主唱的字对不上", "partial_word": "从字的中间开始，补写了整个字"}
    harmony = [{"time": n["start"], "note": n["index"], "line": n["line"],
                "status": "；".join(why[f] for f in n["flags"] if f in why),
                "attention": any(f in ("alone", "octave", "late_onset") for f in n["flags"])}
               for n in score.get("harmony", []) if n["flags"]]
    if mode == "dictation_only":
        for ln in lines:
            ln["attention"] = True
            ln["dictation_only"] = True

    attention = (sum(m["attention"] for m in merges) + sum(s["attention"] for s in sustains)
                 + sum(ln["attention"] for ln in lines) + len(stale) + len(conflict_ids)
                 + sum(d["attention"] for d in dictation) + sum(h["attention"] for h in harmony))
    return {
        "format": "bapu.review/1",
        "summary": {
            "notes": len(notes), "lines": len(lines), "attention": attention,
            "auto_merges": sum(m["attention"] for m in merges), "sustain": len(sustains),
            "low_conf_lines": sum(ln["low_conf"] for ln in lines),
            "maybe_missing_lines": sum(bool(ln["maybe_missing"]) for ln in lines), "manual_lines": sum(ln["version"] == "manual" for ln in lines),
            "stale_corrections": len(stale), "conflicting_corrections": len(conflict_ids),
            "tempo_bpm": score["tempo"]["bpm"], "tempo_source": score["tempo"]["source"],
            "sequence_matches_lyrics": score["checks"]["sequence_matches_lyrics"],
            "corrections": len(entries),
            "lyrics_mode": mode,
            "dictation_changes": sum(d["attention"] for d in dictation),
            "harmony_notes": len(score.get("harmony", [])),
            "harmony_attention": sum(h["attention"] for h in harmony),
        },
        "dictation": dictation,
        "harmony": harmony,
        "merges": merges,
        "sustains": sustains,
        "lines": lines,
        "stale_corrections": stale,
        "conflicting_corrections": conflicts,
    }


def render_md(r: dict[str, Any]) -> str:
    s = r["summary"]
    out = [
        "# 审查报告",
        "",
        "只列时间、音符编号（从 1 开始）、句号和置信度，不含歌词。",
        "",
        f"- 音符 {s['notes']} 个，句子 {s['lines']} 句；拍速 {s['tempo_bpm']:g} BPM（{'你设定的' if s['tempo_source'] == 'user' else '自动估计'}）",
        f"- 分配后的字序和歌词一致：{'是' if s['sequence_matches_lyrics'] else '否'}",
        f"- **需要你确认 {s['attention']} 条**：自动合并 {s['auto_merges']}、低置信度句子 {s['low_conf_lines']}、"
        f"可能漏字的句子 {s['maybe_missing_lines']}、失效修正 {s['stale_corrections']}、冲突修正 {s['conflicting_corrections']}，以及下面标 ⚠ 的 `-`",
        f"- 修正文件里有 {s['corrections']} 条；用人工版本的句子 {s['manual_lines']} 句",
        "- 歌词来源：" + ("只有听写（没有参考歌词），每句都要确认" if s["lyrics_mode"] == "dictation_only"
                       else f"听写 + 参考歌词；听写改动了 {s['dictation_changes']} 处"),
        "",
    ]
    if r.get("harmony"):
        out += ["## 和声", "", f"和声音符 {s['harmony_notes']} 个，下面列出需要看的。", "",
                "| 时间 | 和声音符 | 句 | 原因 |", "|---|---|---|---|"]
        for h in r["harmony"]:
            out.append(f"| {mmss(h['time'])} | {h['note']} | L{h['line']:02d} | {'⚠ ' if h['attention'] else ''}{h['status']} |")
        out.append("")
    if r["dictation"]:
        out += ["## 听写和参考歌词", "", "| 时间 | 句 | 置信度 | 结果 |", "|---|---|---|---|"]
        for d in r["dictation"]:
            conf = f"{d['conf']:.2f}" if d["conf"] is not None else "—"
            out.append(f"| {mmss(d['time'])} | L{d['line']:02d} | {conf} | {'⚠ ' if d['attention'] else ''}{d['status']} |")
        out.append("")
    if r["stale_corrections"] or r["conflicting_corrections"]:
        out += ["## 修正", "", "| 条目 | 类型 | 位置 | 状态 |", "|---|---|---|---|"]
        for e in r["stale_corrections"]:
            where = mmss(e["at"]) if e["at"] is not None else f"L{e['line']:02d}"
            out.append(f"| 第 {e['id']} 条 | {e['type']} | {where} | 失效：找不到对应的音符 |")
        for c in r["conflicting_corrections"]:
            out.append(f"| 第 {c['id']} 条 | line_version | L{c['line']:02d} | 冲突：{c['reason']} |")
        out.append("")
    if r["merges"]:
        out += ["## 合并的音符", "", "| 时间 | 音符 | 句 | 起音分数 | 状态 |", "|---|---|---|---|---|"]
        for m in r["merges"]:
            out.append(f"| {mmss(m['time'])} | {m['note'] or '—'} | L{m['line']:02d} | {m['score']:.3f} | "
                       f"{'⚠ ' if m['attention'] else ''}{m['status']} |")
        out.append("")
    low = [ln for ln in r["lines"] if ln["attention"]]
    if low:
        out += ["## 需要试听的句子", "", "| 句 | 时间 | 置信度 | 音节 / 音符 | 原因 |", "|---|---|---|---|---|"]
        for ln in low:
            why = []
            if ln["low_conf"]:
                why.append("对齐置信度低")
            if ln["fewer_notes"]:
                why.append("音符比音节少，借用了相邻句的音符")
            if ln.get("dictation_only"):
                why.append("没有参考歌词，全部来自听写")
            if ln["maybe_missing"]:
                m = ln["maybe_missing"]
                why.append(f"可能字幕漏字：音符 {m['notes'][0]}–{m['notes'][-1]} 连续是 `-`，其中有明显的新起音")
            out.append(f"| L{ln['line']:02d} | {mmss(ln['t0'])}–{mmss(ln['t1'])} | {ln['conf']:.2f} | "
                       f"{ln['syllables']} / {ln['notes']} | {'；'.join(why)} |")
        out.append("")
    if r["sustains"]:
        out += ["## `-`（延续上一个字）的位置", "",
                "⚠ = 前面是休止，或这个音符开头的起音证据很强（像新的字）。", "",
                "| 时间 | 音符 | 句 | 起音分数 | 备注 |", "|---|---|---|---|---|"]
        for x in r["sustains"]:
            note = []
            if x["after_rest"]:
                note.append("休止后")
            if x["fixed"]:
                note.append("你指定的")
            onset = f"{x['onset']:.2f}" if x["onset"] is not None else "—"
            out.append(f"| {mmss(x['time'])} | {x['note']} | L{x['line']:02d} | {onset} | "
                       f"{'⚠ ' if x['attention'] else ''}{'、'.join(note)} |")
        out.append("")
    out += ["## 每一句", "", "| 句 | 字幕时间 | 对齐时间 | 置信度 | 音节 | 音符 | `-` | 音符范围 | 版本 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for ln in r["lines"]:
        rng = f"{ln['first_note']}–{ln['last_note']}" if ln["first_note"] else "—"
        conf = f"{ln['conf']:.2f}" if ln["conf"] is not None else "—"
        out.append(f"| L{ln['line']:02d} | {mmss(ln['start'])} | {mmss(ln['t0'])} | {conf} | {ln['syllables']} | "
                   f"{ln['notes']} | {ln['sustain']} | {rng} | {'人工' if ln['version'] == 'manual' else '自动'} |")
    out.append("")
    return "\n".join(out)

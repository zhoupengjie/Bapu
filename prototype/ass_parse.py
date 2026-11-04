"""Parse an ASS subtitle file into timed lines; print structure and counts only.

Usage: python -I ass_parse.py <folder> <out.json>
"""
import glob
import json
import re
import sys

folder, out = sys.argv[1:3]
path = glob.glob(folder + "/*.ass")[0]
raw = open(path, "rb").read()
for enc in ("utf-8-sig", "utf-16", "gb18030"):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue
print("encoding:", enc, "bytes:", len(raw))

CJK = "\u3400-\u9fff\uf900-\ufaff"
TOKEN = re.compile(r"[A-Za-z']+|[" + CJK + "]")
KEEP = re.compile(r"[A-Za-z'" + CJK + r"\s]")


def t2s(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


fmt, lines, styles, karaoke = None, [], set(), 0
for ln in text.splitlines():
    if ln.startswith("Format:") and fmt is None and "Text" in ln and "Start" in ln:
        fmt = [x.strip() for x in ln[7:].split(",")]
    if ln.startswith("Dialogue:"):
        parts = ln[9:].split(",", len(fmt) - 1)
        d = {k: (p if k == "Text" else p.strip()) for k, p in zip(fmt, parts)}
        if re.search(r"\\k[fo]?\d", d["Text"]):
            karaoke += 1
        body = re.sub(r"\{[^}]*\}", "", d["Text"])
        body = body.replace(r"\N", " ").replace(r"\n", " ")
        styles.add(d.get("Style"))
        lines.append({"start": t2s(d["Start"]), "end": t2s(d["End"]), "text": body.strip()})

print("dialogue lines:", len(lines), "styles:", styles, "lines with karaoke tags:", karaoke)
lines.sort(key=lambda item: item["start"])
for i, item in enumerate(lines, 1):
    tk = TOKEN.findall(item["text"])
    latin = [t for t in tk if t[0].isascii()]
    other = [c for c in item["text"] if not KEEP.match(c)]
    item.update({"tokens": tk, "cjk": len(tk) - len(latin), "latin_words": len(latin)})
    print(f"L{i:02d} {item['start']:7.2f}-{item['end']:7.2f}  cjk={item['cjk']:2d} "
          f"latin_words={len(latin)} punct/other={len(other)}")
with open(out, "w", encoding="utf8") as f:
    json.dump(lines, f, ensure_ascii=False)

"""Write a paste-ready lyric file: one token per SV note, in note order.

Usage: python -I export_paste.py <fill_plan.json> <align.json> <out.txt> <lang.json>
Each output line is one subtitle line; tokens are space-separated so SV assigns
exactly one token per selected note. Prints counts only, never the lyric text.
"""

import json
import sys


def main() -> None:
    plan_path, align_path, out_txt, lang_out = sys.argv[1:5]
    plan = {p["noteIndex"]: p["lyric"] for p in json.load(open(plan_path, encoding="utf8"))}
    assign = json.load(open(align_path, encoding="utf8"))["assign"]

    rows, total, english, cjk, prev_lang = [], 0, [], [], None
    for li, idxs in enumerate(assign, 1):
        toks = [plan[n] for n in idxs]
        rows.append(" ".join(toks))
        total += len(toks)
        for n, t in zip(idxs, toks):
            if t in ("-", "+"):
                lang = prev_lang
            else:
                lang = "english" if t[0].isascii() else "cantonese"
            (english if lang == "english" else cjk).append(n)
            prev_lang = lang
        print(f"L{li:02d} notes={len(idxs):2d} tokens={len(toks):2d} "
              f"dash={toks.count('-')} plus={toks.count('+')}")
    with open(out_txt, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(rows) + "\n")
    with open(lang_out, "w", encoding="utf8") as f:
        json.dump({"english": english, "cantonese": cjk}, f)
    print(f"total tokens={total} (notes in plan={len(plan)}) english-language notes={len(english)} "
          f"cantonese-language notes={len(cjk)}")


if __name__ == "__main__":
    main()

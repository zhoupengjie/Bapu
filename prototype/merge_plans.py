"""Merge the forced-alignment plan with the lyrics currently in SV.

Usage: python -I merge_plans.py <fa_plan.json> <sv_now.json> <ass_lines.json> <out_paste.txt> <keep_lines>
keep_lines: comma-separated 1-based subtitle line numbers that stay as they are in SV
(low alignment confidence, or fixed by hand). All other lines take the aligned version.
Switching between sources happens only where both have consumed the same number of
syllables, so no syllable can be duplicated or dropped. Prints counts only.
"""

import json
import sys


def main() -> None:
    fa_path, now_path, ass_path, out_txt, keep_arg = sys.argv[1:6]
    fa = json.load(open(fa_path, encoding="utf8"))
    fa_tok = {p["noteIndex"]: p["lyric"] for p in fa}
    fa_line = {p["noteIndex"]: p["line"] + 1 for p in fa}
    g = json.loads(open(now_path, encoding="utf8").read())["groups"][0]
    ix = {c: i for i, c in enumerate(g["notes"]["columns"])}
    now = {r[ix["noteIndex"]]: r[ix["lyrics"]] for r in g["notes"]["rows"]}
    keep = {int(x) for x in keep_arg.split(",") if x}
    notes = sorted(fa_tok)

    consumes = lambda t: t != "-"
    pre_fa = pre_now = 0
    source, merged, switched_late = "now", {}, 0
    for n in notes:
        want = "now" if fa_line[n] in keep else "fa"
        if want != source:
            if pre_fa == pre_now:
                source = want
            else:
                switched_late += 1
        merged[n] = fa_tok[n] if source == "fa" else now[n]
        pre_fa += consumes(fa_tok[n])
        pre_now += consumes(now[n])

    # Validate: merged word sequence must equal the subtitle token sequence exactly.
    lines = json.load(open(ass_path, encoding="utf8"))
    expected = [t for ln in lines for t in ln["tokens"]]
    words = [merged[n] for n in notes if merged[n] not in ("-", "+")]
    plus = sum(merged[n] == "+" for n in notes)
    print(f"merged notes={len(merged)} words={len(words)} expected words={len(expected)} "
          f"sequence identical={words == expected} plus={plus} dash={sum(merged[n] == '-' for n in notes)}")
    print(f"notes that had to wait for a safe switch point: {switched_late}")
    changed = [n for n in notes if merged[n] != now[n]]
    by_line = {}
    for n in changed:
        by_line.setdefault(fa_line[n], []).append(n)
    print(f"notes that change vs current SV: {len(changed)} in {len(by_line)} lines")
    for li in sorted(by_line):
        print(f"  L{li:02d} notes {by_line[li]}")

    rows = {}
    for n in notes:
        rows.setdefault(fa_line[n], []).append(merged[n])
    with open(out_txt, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(" ".join(rows[k]) for k in sorted(rows)) + "\n")
    print(f"paste tokens={sum(len(r) for r in rows.values())}")


if __name__ == "__main__":
    main()

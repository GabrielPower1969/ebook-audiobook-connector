#!/usr/bin/env python3
"""Cross-check a built book's text against what is actually spoken in that stretch of audio.

    scripts/verify-text.py <slug> [<slug>...] [--worst 15] [--threshold 0.55] [--report FILE]

The reader always displays the book's own words; the transcript only supplies timings. This script
uses the transcript the other way round — as an independent witness — to answer "does the text on
screen match what the narrator says here?".

For every aligned paragraph it takes the transcript words inside that paragraph's time span and
measures how much of the book's token sequence they cover (difflib's longest matching blocks over
the two token lists). Then it reports the distribution and the worst offenders.

Read the output as a screen, not a verdict. A low score means one of:
  * the book file is corrupt there (a bad PDF text layer, OCR damage) — the case worth fixing,
  * the editions differ (an American text against a British narration),
  * the narrator skipped or ad-libbed (front matter, captions, "end of part two"),
  * or whisper simply misheard.
Only reading the paragraph tells you which. A high score is much stronger evidence: text and audio
independently agree, so both are almost certainly right.
"""
from __future__ import annotations
import argparse, bisect, difflib, json, pathlib, statistics, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audiobook_connector import align, transcribe                            # noqa: E402

LIB = pathlib.Path("library")
BOOKS = pathlib.Path("books")
CACHE = pathlib.Path("cache/transcripts")


def load_words(slug: str, files: list[str]) -> list[list[dict]]:
    """Transcript words per audio file, in the same order as data.json's `files`."""
    src = BOOKS / slug
    by_name = {f.name: f for f in transcribe.audio_files(src)} if src.is_dir() else {}
    out = []
    for name in files:
        p = by_name.get(name)
        cf = CACHE / f"{transcribe._cache_key(p)}.json" if p else None
        out.append(json.load(open(cf))["words"] if cf and cf.exists() else [])
    return out


def coverage(book_toks: list[str], heard_toks: list[str]) -> float:
    """Fraction of the book's tokens that appear, in order, in what was heard."""
    if not book_toks:
        return 1.0
    sm = difflib.SequenceMatcher(None, book_toks, heard_toks, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(book_toks)


def check(slug: str, threshold: float):
    d = LIB / slug
    x = json.load(open(d / "data.json"))
    words = load_words(slug, x["files"])
    starts = [[w["s"] for w in ws] for ws in words]           # for bisect
    if not any(words):
        print(f"{slug}: 没有转写缓存，跳过")
        return None

    rows, scores = [], []
    paras = x["paras"]
    for i, p in enumerate(paras):
        t = p["t"]
        if not t:
            continue
        f = t["f"]
        ws, ss = words[f], starts[f]
        if not ws:
            continue
        end = t["e"]
        if end is None:                                       # fall back to the next paragraph's start
            nxt = next((q["t"]["s"] for q in paras[i + 1:] if q["t"] and q["t"]["f"] == f), None)
            end = nxt if nxt is not None else t["s"] + 30
        lo = bisect.bisect_left(ss, t["s"] - 0.5)
        hi = bisect.bisect_right(ss, end + 0.5)
        heard = align.tokens(" ".join(w["w"] for w in ws[lo:hi]))
        text = json.loads(json.dumps(p["html"]))
        import html as htmlmod, re
        plain = htmlmod.unescape(re.sub(r"<[^>]+>", "", text))
        btoks = align.tokens(plain)
        if len(btoks) < 8:                                    # a three-word heading proves nothing
            continue
        c = coverage(btoks, heard)
        scores.append(c)
        rows.append((c, p["id"], plain))

    if not scores:
        print(f"{slug}: 没有可核对的段落")
        return None
    rows.sort()
    bad = [r for r in rows if r[0] < threshold]
    good = sum(1 for s in scores if s >= 0.9)
    return {"slug": slug, "n": len(scores), "median": statistics.median(scores),
            "mean": sum(scores) / len(scores), "ge90": good, "bad": bad, "rows": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", nargs="*")
    ap.add_argument("--worst", type=int, default=10)
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--report", help="write a full Markdown report here")
    a = ap.parse_args()
    slugs = a.slug or sorted(p.name for p in LIB.iterdir() if (p / "data.json").exists())

    results = [r for r in (check(s, a.threshold) for s in slugs) if r]
    print(f"\n{'书':28} {'核对段落':>7} {'中位吻合':>8} {'平均':>7} {'≥90%':>8} {'<%d%%' % (a.threshold * 100):>7}")
    for r in results:
        print(f"{r['slug']:28} {r['n']:7} {r['median']:8.3f} {r['mean']:7.3f} "
              f"{100 * r['ge90'] / r['n']:7.1f}% {len(r['bad']):7}")
    tot = sum(r["n"] for r in results)
    if tot:
        allbad = sum(len(r["bad"]) for r in results)
        print(f"{'合计':28} {tot:7} {'':8} {'':7} "
              f"{100 * sum(r['ge90'] for r in results) / tot:7.1f}% {allbad:7}"
              f"   （低吻合 {100 * allbad / tot:.2f}%）")

    for r in results:
        if not r["bad"]:
            continue
        print(f"\n── {r['slug']}：最差 {min(a.worst, len(r['bad']))} 段 ──")
        for c, pid, txt in r["bad"][:a.worst]:
            print(f"  {c:.2f}  #{pid}  {txt[:110]}")

    if a.report:
        with open(a.report, "w") as fh:
            lo = f"{a.threshold:.0%}"
            fh.write("# 正文与音频吻合度核对\n\n"
                     "阅读器显示的永远是书本身的文字；这里把转写当独立证人，量「屏幕上这段话和"
                     "这段音频里念的是不是同一段话」。分数是书的词序列被听到的词覆盖的比例。\n\n"
                     f"| 书 | 核对段落 | 中位吻合 | 平均 | ≥90% | <{lo} |\n"
                     "|---|---:|---:|---:|---:|---:|\n")
            for r in results:
                fh.write(f"| {r['slug']} | {r['n']} | {r['median']:.3f} | {r['mean']:.3f} | "
                         f"{100 * r['ge90'] / r['n']:.1f}% | {len(r['bad'])} |\n")
            for r in results:
                if not r["bad"]:
                    continue
                fh.write(f"\n## {r['slug']} — 低吻合段落 {len(r['bad'])} 段\n\n")
                for c, pid, txt in r["bad"]:
                    fh.write(f"- `{c:.2f}` **#{pid}** {txt[:300]}\n")
        print(f"\n报告已写入 {a.report}")


if __name__ == "__main__":
    main()

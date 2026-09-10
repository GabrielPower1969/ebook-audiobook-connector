#!/usr/bin/env python3
"""Build a dictionary for the words this library actually uses.

    scripts/build-dict.py [--source cache/dict/ecdict.csv] [--out library/_dict/dict.json]

Source is ECDICT (https://github.com/skywind3000/ECDICT, MIT) — 66 MB of CSV covering 3.4 million
entries with phonetics, Chinese and English glosses, exam tags (中考/高考/CET/考研/TOEFL/IELTS/GRE),
a frequency rank and inflection tables. Fetch it once:

    mkdir -p cache/dict && curl -L -o cache/dict/ecdict.csv \\
      https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv

Shipping all of it to the browser is out of the question, and it is also unnecessary: a library of
thirteen books uses a few tens of thousands of distinct words. This script collects exactly those,
resolves inflections back to their base form through ECDICT's own `exchange` column so that
"mustaches" finds "mustache", and writes a few megabytes the reader can fetch once and cache.

Output shape — entries are shared, so every inflection of a word costs one integer:

    {"src": "ECDICT", "licence": "MIT", "n": 41234,
     "e": [{"p": "'mʌstɑːʃ", "t": "n. 小胡子", "d": "…", "g": "cet4 ielts", "c": 3, "f": 11842}, …],
     "w": {"mustache": 0, "mustaches": 0, …}}

The reader loads it lazily, on the first word lookup, and never before.
"""
from __future__ import annotations
import argparse, csv, gzip, html as htmlmod, json, pathlib, re, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
TAG_ORDER = ["zk", "gk", "cet4", "cet6", "ky", "toefl", "ielts", "gre"]
# ECDICT's `exchange` codes; every one of these is an inflected form of the entry's own word
FORMS = ("p", "d", "i", "3", "r", "t", "s")


def norm(w: str) -> str:
    return w.lower().replace("’", "'").strip("'-")


def library_words(lib: pathlib.Path) -> set[str]:
    """Every distinct word in every built book."""
    out: set[str] = set()
    for dj in sorted(lib.glob("*/data.json")):
        x = json.load(open(dj))
        for p in x["paras"]:
            text = htmlmod.unescape(re.sub(r"<[^>]+>", "", p["html"]))
            for m in WORD.finditer(text):
                w = norm(m.group())
                if len(w) > 1:
                    out.add(w)
    return out


def trim_translation(t: str, limit: int = 120) -> str:
    """ECDICT packs every sense into one field separated by newlines. Two are plenty on a phone."""
    parts = [s.strip() for s in t.replace("\\n", "\n").split("\n") if s.strip()]
    s = "; ".join(parts[:2])
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="cache/dict/ecdict.csv")
    ap.add_argument("--library", default="library")
    ap.add_argument("--out", default="library/_dict", help="directory for the per-letter shards")
    ap.add_argument("--english", action="store_true", help="also keep the English gloss (bigger file)")
    a = ap.parse_args()

    src, lib = pathlib.Path(a.source), pathlib.Path(a.library)
    if not src.exists():
        sys.exit(f"no dictionary source at {src}\n"
                 f"  mkdir -p {src.parent} && curl -L -o {src} \\\n"
                 f"    https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv")
    if not lib.is_dir():
        sys.exit(f"no library at {lib} — build a book first")

    want = library_words(lib)
    print(f"library: {len(want)} distinct words")

    # One pass: keep the rows we want, and remember which inflections belong to which base word so
    # a second pass can attach the words we have not matched yet.
    rows: dict[str, dict] = {}
    infl: dict[str, str] = {}
    csv.field_size_limit(1 << 24)
    with open(src, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            w = norm(r.get("word") or "")
            if not w:
                continue
            keep = w in want
            ex = r.get("exchange") or ""
            if ex:
                for part in ex.split("/"):
                    code, _, val = part.partition(":")
                    if code in FORMS and val:
                        v = norm(val)
                        if v and v != w and v in want:
                            infl.setdefault(v, w)
                            keep = True
            if keep and w not in rows:
                rows[w] = r

    # de-duplicate the entry bodies: every inflection of a word points at the same one
    entries: list[dict] = []
    index: dict[str, int] = {}
    words: dict[str, int] = {}

    def entry_id(r: dict) -> int:
        t = trim_translation(r.get("translation") or "")
        d = trim_translation(r.get("definition") or "", 140) if a.english else ""
        tags = [t_ for t_ in (r.get("tag") or "").split() if t_ in TAG_ORDER]
        tags.sort(key=TAG_ORDER.index)
        e = {"p": (r.get("phonetic") or "").strip(), "t": t}
        if d: e["d"] = d
        if tags: e["g"] = " ".join(tags)
        try:
            c = int(r.get("collins") or 0)
            if c: e["c"] = c
        except ValueError: pass
        try:
            f = int(r.get("frq") or 0)
            if f: e["f"] = f
        except ValueError: pass
        key = json.dumps(e, ensure_ascii=False, sort_keys=True)
        if key not in index:
            index[key] = len(entries); entries.append(e)
        return index[key]

    for w in sorted(want):
        r = rows.get(w) or rows.get(infl.get(w, ""))
        if not r:
            continue
        if not (r.get("translation") or r.get("definition")):
            continue
        words[w] = entry_id(r)

    # One file per first letter. A lookup then costs a hundred kilobytes, not three megabytes —
    # which matters most in the offline copy, where the browser parses it as script source.
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("*.json")) + list(out.glob("*.js")) + list(out.glob("*.gz")):
        old.unlink()

    shards: dict[str, dict[str, int]] = {}
    for w, i in words.items():
        k = w[0] if "a" <= w[0] <= "z" else "_"
        shards.setdefault(k, {})[w] = i

    total = 0
    for k, ws in sorted(shards.items()):
        used = sorted({i for i in ws.values()})
        remap = {old: new for new, old in enumerate(used)}
        payload = {"src": "ECDICT", "licence": "MIT", "k": k, "n": len(ws),
                   "e": [entries[i] for i in used], "w": {w: remap[i] for w, i in ws.items()}}
        f = out / f"{k}.json"
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        f.write_text(body, encoding="utf-8")
        with open(f, "rb") as fh, gzip.open(str(f) + ".gz", "wb", 6) as gz:
            shutil.copyfileobj(fh, gz)
        total += f.stat().st_size
    json.dump({"src": "ECDICT", "url": "https://github.com/skywind3000/ECDICT", "licence": "MIT",
               "n": len(words), "shards": sorted(shards)},
              open(out / "meta.json", "w"), ensure_ascii=False)
    print(f"matched: {len(words)} words ({100*len(words)/max(1,len(want)):.0f}% of the library), "
          f"{len(entries)} distinct entries")
    print(f"wrote:   {len(shards)} shards in {out}/  ({total//1024} KB total, "
          f"biggest {max(f.stat().st_size for f in out.glob('*.json'))//1024} KB)")


if __name__ == "__main__":
    main()

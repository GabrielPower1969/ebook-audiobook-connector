"""CLI:  python -m audiobook_connector build <dir-with-epub-and-audio> [--title T] [--slug S]
        python -m audiobook_connector serve [--port 8765]
        python -m audiobook_connector list
Builds into ./library/<slug>/ and keeps transcripts in ./cache/transcripts/."""
from __future__ import annotations
import argparse, json, os, pathlib, re, shutil, sys
from . import epub, transcribe, align as aligner, server

PKG = pathlib.Path(__file__).parent
LIB = pathlib.Path("library"); CACHE = pathlib.Path("cache/transcripts")

def slugify(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "book"

def cmd_build(a):
    src = pathlib.Path(a.source).resolve()
    epubs = sorted(src.glob("*.epub"))
    if not epubs: sys.exit(f"no .epub in {src}")
    audios = transcribe.audio_files(src)
    if not audios: sys.exit(f"no audio files in {src}")
    print(f"epub: {epubs[0].name}\naudio: {len(audios)} files")
    book = epub.parse_epub(str(epubs[0]))
    title = a.title or book.title; slug = a.slug or slugify(title)
    print(f"title: {title}  ({len(book.paras)} paragraphs)")
    print("transcribing:")
    trs = transcribe.transcribe_all(src, CACHE, model=a.model, language=a.language)
    paras = [{"id": p.id, "tag": p.tag, "text": p.text} for p in book.paras]
    al = aligner.align(paras, trs); st = al["stats"]
    print(f"aligned {st['aligned']}/{st['paragraphs']} paragraphs ({st['exact']} exact, {st['anchors']} anchors)")
    out = LIB / slug; (out / "audio").mkdir(parents=True, exist_ok=True)
    for f in audios:
        dst = out / "audio" / f.name
        if dst.is_symlink() or dst.exists(): dst.unlink()
        if a.copy_audio: shutil.copy2(f, dst)
        else: os.symlink(f, dst)
    cover = next((p for p in src.glob("cover.*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")), None)
    if cover: shutil.copy2(cover, out / ("cover" + cover.suffix.lower()))
    data = {"title": title, "author": book.author, "files": al["files"], "stats": st,
            "paras": [{"id": p.id, "tag": p.tag, "html": p.html, "t": t} for p, t in zip(book.paras, al["paras"])]}
    json.dump(data, open(out / "data.json", "w"), ensure_ascii=False)
    _write_index(); print(f"built library/{slug}  →  python -m audiobook_connector serve")

def _write_index():
    LIB.mkdir(exist_ok=True); books = []
    for d in sorted(LIB.iterdir()):
        dj = d / "data.json"
        if not dj.exists(): continue
        x = json.load(open(dj)); cov = next((c.name for c in d.glob("cover.*")), None)
        books.append({"slug": d.name, "title": x["title"], "author": x.get("author", ""), "cover": cov,
                      "files": len(x["files"]), "aligned": x["stats"]["aligned"], "paragraphs": x["stats"]["paragraphs"]})
    json.dump(books, open(LIB / "index.json", "w"), ensure_ascii=False)
    for f in ("index.html", "reader.html"): shutil.copy2(PKG / "app" / f, LIB / f)

def cmd_serve(a): _write_index(); server.serve(str(LIB), a.port)
def cmd_list(a):
    _write_index()
    for b in json.load(open(LIB / "index.json")): print(f"{b['slug']:30} {b['title']}  [{b['aligned']}/{b['paragraphs']} aligned, {b['files']} audio]")

def main():
    ap = argparse.ArgumentParser(prog="audiobook_connector"); sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("source"); b.add_argument("--title"); b.add_argument("--slug")
    b.add_argument("--model", default=transcribe.DEFAULT_MODEL); b.add_argument("--language", default=None)
    b.add_argument("--copy-audio", action="store_true", help="copy audio into library instead of symlinking")
    b.set_defaults(fn=cmd_build)
    s = sp.add_parser("serve"); s.add_argument("--port", type=int, default=8765); s.set_defaults(fn=cmd_serve)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__": main()

"""audiobook-connector CLI.

  audiobook-connector build <book-dir>     build one book into the library
  audiobook-connector serve                serve the library on the LAN
  audiobook-connector list                 list built books

Paths (override with env vars, which is how the Docker image is configured):
  AC_BOOKS    source dirs, one per book   default ./books
  AC_LIBRARY  build output                default ./library
  AC_CACHE    transcripts + whisper models default ./cache
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, shutil, socket, sys
from . import formats, transcribe, align as aligner, server

PKG = pathlib.Path(__file__).parent
BOOKS = pathlib.Path(os.environ.get("AC_BOOKS", "books"))
LIB = pathlib.Path(os.environ.get("AC_LIBRARY", "library"))
CACHE = pathlib.Path(os.environ.get("AC_CACHE", "cache"))


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "book"


def _link_audio(src_file: pathlib.Path, dst: pathlib.Path, mode: str):
    """Relative symlink by default: the whole project folder stays copyable and mountable."""
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    if mode == "copy":
        shutil.copy2(src_file, dst)
        return
    if mode == "hardlink":
        try:
            os.link(src_file, dst)
            return
        except OSError:
            pass  # cross-device, fall through to symlink
    try:
        os.symlink(os.path.relpath(src_file.resolve(), dst.parent.resolve()), dst)
    except OSError:
        shutil.copy2(src_file, dst)


def _resolve_source(arg: str) -> pathlib.Path:
    """Accept a path, or a bare book name looked up under AC_BOOKS."""
    p = pathlib.Path(arg)
    if p.is_dir():
        return p
    if (BOOKS / arg).is_dir():
        return BOOKS / arg
    sys.exit(f"no such book directory: {arg}  (looked in ./{arg} and {BOOKS}/{arg})")


def cmd_build(a):
    src = _resolve_source(a.source)
    book_file = formats.find_book(src)
    if not book_file:
        sys.exit(f"no book file found in {src} (supported: {' '.join(formats.BOOK_EXT)})")
    audios = transcribe.audio_files(src)
    if not audios:
        sys.exit(f"no audio files found in {src} (looked for {', '.join(sorted(transcribe.AUDIO_EXT))})")
    print(f"source:  {src}\nbook:    {book_file.name}\naudio:   {len(audios)} file(s)")

    book = formats.parse_book(book_file)
    title = a.title or book.title
    slug = a.slug or slugify(title)
    print(f"title:   {title}  ({len(book.paras)} paragraphs)")

    print("transcribing:")
    trs = transcribe.transcribe_all(src, CACHE / "transcripts", model=a.model, language=a.language,
                                    backend=a.backend, models_dir=CACHE / "models")

    al = aligner.align([{"id": p.id, "tag": p.tag, "text": p.text} for p in book.paras], trs)
    st = al["stats"]
    pct = 100 * st["aligned"] / max(1, st["paragraphs"])
    print(f"aligned: {st['aligned']}/{st['paragraphs']} paragraphs ({pct:.0f}%, {st['exact']} exact)")
    if pct < 20:
        print("  ⚠ low alignment — check that the epub and the audio are the same edition,"
              "\n    and that --language matches the book if auto-detection went wrong.")

    out = LIB / slug
    (out / "audio").mkdir(parents=True, exist_ok=True)
    for f in audios:
        _link_audio(f, out / "audio" / f.name, a.audio)
    broken = [f.name for f in audios if not (out / "audio" / f.name).exists()]
    if broken:
        print(f"  ⚠ broken audio links: {broken[:3]} — rebuild with --audio copy")
    cover = next((p for p in sorted(src.glob("cover.*")) if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")), None)
    if cover:
        for old in out.glob("cover.*"):
            old.unlink()
        shutil.copy2(cover, out / ("cover" + cover.suffix.lower()))

    json.dump({"title": title, "author": book.author, "files": al["files"], "stats": st,
               "paras": [{"id": p.id, "tag": p.tag, "html": p.html, "t": t}
                         for p, t in zip(book.paras, al["paras"])]},
              open(out / "data.json", "w"), ensure_ascii=False)
    _write_index()
    print(f"built:   {out}\nnext:    audiobook-connector serve")


def _write_index():
    LIB.mkdir(parents=True, exist_ok=True)
    books = []
    for d in sorted(p for p in LIB.iterdir() if p.is_dir()):
        dj = d / "data.json"
        if not dj.exists():
            continue
        x = json.load(open(dj))
        cover = next((c.name for c in sorted(d.glob("cover.*"))), None)
        books.append({"slug": d.name, "title": x["title"], "author": x.get("author", ""), "cover": cover,
                      "files": len(x["files"]), "aligned": x["stats"]["aligned"],
                      "paragraphs": x["stats"]["paragraphs"]})
    json.dump(books, open(LIB / "index.json", "w"), ensure_ascii=False)
    for stale in ("index.html", "reader.html"):          # older versions copied these here
        (LIB / stale).unlink(missing_ok=True)
    return books


def _lan_urls(port: int) -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        ips.update(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return [f"http://{ip}:{port}" for ip in sorted(ips) if not ip.startswith("127.")]


def cmd_serve(a):
    books = _write_index()
    if not books:
        print(f"library is empty ({LIB.resolve()}) — build a book first:\n"
              f"  audiobook-connector build <book-dir>")
    else:
        print(f"{len(books)} book(s): " + ", ".join(b["slug"] for b in books))
    print(f"  local:  http://localhost:{a.port}")
    if os.path.exists("/.dockerenv"):
        print(f"  LAN:    http://<ip-of-the-machine-running-docker>:{os.environ.get('AC_PORT', a.port)}")
    else:
        for u in _lan_urls(a.port):
            print(f"  LAN:    {u}")
    server.serve(str(LIB), a.port, a.host, app_dir=str(PKG / "app"))


def cmd_list(a):
    for b in _write_index():
        pct = 100 * b["aligned"] / max(1, b["paragraphs"])
        print(f"{b['slug']:28} {b['title'][:44]:46} {pct:3.0f}% aligned  {b['files']} audio")


def main():
    ap = argparse.ArgumentParser(prog="audiobook-connector", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    b = sp.add_parser("build", help="align one book and add it to the library")
    b.add_argument("source", help="directory holding one book (epub/mobi/azw3/pdf) plus its audio files, or a name under AC_BOOKS")
    b.add_argument("--title", help="override the title from the epub metadata")
    b.add_argument("--slug", help="URL name in the library (default: slugified title)")
    b.add_argument("--language", help="ISO code, e.g. en / zh. Default: whisper auto-detects")
    b.add_argument("--model", default=transcribe.DEFAULT_MODEL, help=f"whisper model (default {transcribe.DEFAULT_MODEL})")
    b.add_argument("--backend", default=os.environ.get("AC_BACKEND", "auto"), choices=["auto", "mlx", "faster"])
    b.add_argument("--audio", default="symlink", choices=["symlink", "hardlink", "copy"],
                   help="how audio enters the library (default symlink, relative so the folder stays portable)")
    b.set_defaults(fn=cmd_build)

    s = sp.add_parser("serve", help="serve the library over HTTP")
    s.add_argument("--port", type=int, default=int(os.environ.get("AC_PORT", 8765)))
    s.add_argument("--host", default=os.environ.get("AC_HOST", "0.0.0.0"), help="default 0.0.0.0 (reachable on the LAN)")
    s.set_defaults(fn=cmd_serve)

    sp.add_parser("list", help="list books in the library").set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

"""audiobook-connector CLI.

  audiobook-connector build <book-dir>     build one book into the library
  audiobook-connector transcribe <dir>...  only fill the transcript cache (run a long series in the background)
  audiobook-connector serve                serve the library on the LAN
  audiobook-connector list                 list built books

A book dir may carry a book.json with any of: title, author, series, volume, narrator, language,
chapters (list of titles, in order). Chapter titles are otherwise read from the audio file names.

Paths (override with env vars, which is how the Docker image is configured):
  AC_BOOKS    source dirs, one per book   default ./books
  AC_LIBRARY  build output                default ./library
  AC_CACHE    transcripts + whisper models default ./cache
"""
from __future__ import annotations
import argparse, gzip, json, os, pathlib, re, shutil, socket, sys
from . import auth, formats, transcribe, align as aligner, server

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


def _meta(src: pathlib.Path) -> dict:
    try:
        return json.load(open(src / "book.json"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        sys.exit(f"{src / 'book.json'}: {e}")


def cmd_transcribe(a):
    for s in a.source:
        src = _resolve_source(s)
        print(f"=== {src}")
        transcribe.transcribe_all(src, CACHE / "transcripts", model=a.model, language=a.language or _meta(src).get("language"),
                                  backend=a.backend, models_dir=CACHE / "models")


def cmd_build(a):
    src = _resolve_source(a.source)
    book_file = formats.find_book(src)
    if not book_file:
        sys.exit(f"no book file found in {src} (supported: {' '.join(formats.BOOK_EXT)})")
    audios = transcribe.audio_files(src)
    if not audios:
        sys.exit(f"no audio files found in {src} (looked for {', '.join(sorted(transcribe.AUDIO_EXT))})")
    print(f"source:  {src}\nbook:    {book_file.name}\naudio:   {len(audios)} file(s)")
    meta = _meta(src)

    book = formats.parse_book(book_file)
    title = a.title or meta.get("title") or book.title
    author = a.author or meta.get("author") or book.author
    slug = a.slug or meta.get("slug") or slugify(title)
    hints = meta.get("chapters") or formats.chapter_hints_from_names([f.name for f in audios])
    if hints:
        n = formats.mark_chapters(book.paras, hints)
        print(f"chapters: {n}/{len(hints)} headings matched to audio chapter titles" +
              ("" if n >= len(hints) - 1 else "  ⚠ check the table of contents"))
    print(f"title:   {title}  ({len(book.paras)} paragraphs)")

    print("transcribing:")
    trs = transcribe.transcribe_all(src, CACHE / "transcripts", model=a.model, language=a.language or meta.get("language"),
                                    backend=a.backend, models_dir=CACHE / "models")

    al = aligner.align([{"id": p.id, "tag": p.tag, "text": p.text} for p in book.paras], trs)
    st = al["stats"]
    pct = 100 * st["aligned"] / max(1, st["paragraphs"])
    print(f"aligned: {st['aligned']}/{st['paragraphs']} paragraphs ({pct:.0f}%, {st['exact']} exact)"
          + (f", {st['sentences']} sentences" if st.get("sentences") else ""))
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

    durations = [round(max((w["e"] for w in t["words"]), default=0), 1) for t in trs]   # ≈ last spoken word
    json.dump({"title": title, "author": author, "series": meta.get("series", ""), "volume": meta.get("volume"),
               "cover": next((c.name for c in sorted(out.glob("cover.*"))), None),
               "narrator": meta.get("narrator", ""), "language": trs[0].get("language") if trs else None,
               "files": al["files"], "durations": durations, "stats": st,
               "paras": [{"id": p.id, "tag": p.tag, "html": p.html, "t": t}
                         for p, t in zip(book.paras, al["paras"])]},
              open(out / "data.json", "w"), ensure_ascii=False)
    # A 9,000-paragraph volume is a multi-megabyte JSON. Ship a pre-compressed copy the server can
    # hand to any client that accepts gzip — it is ~5x smaller and costs nothing at request time.
    with open(out / "data.json", "rb") as fh, gzip.open(out / "data.json.gz", "wb", 6) as gz:
        shutil.copyfileobj(fh, gz)
    _write_index()
    print(f"built:   {out}\nnext:    audiobook-connector serve")


def _chapter_count(paras: list[dict]) -> int:
    """Same rule the reader uses to build its table of contents: the highest heading level that
    appears at least three times. A book whose audio is one file per chapter gets h1 from
    mark_chapters(); one split into "Part 01…Part 08" has only its own h2 headings."""
    for tag in ("h1", "h2", "h3"):
        n = sum(1 for p in paras if p["tag"] == tag)
        if n >= 3:
            return n
    return 0


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
                      "series": x.get("series", ""), "volume": x.get("volume"), "narrator": x.get("narrator", ""),
                      "files": len(x["files"]), "seconds": round(sum(x.get("durations", []))),
                      "chapters": _chapter_count(x["paras"]),
                      "aligned": x["stats"]["aligned"], "paragraphs": x["stats"]["paragraphs"]})
    books.sort(key=lambda b: (b["series"] or "~", b["volume"] or 0, b["title"]))
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
    team, aud = os.environ.get("AC_ACCESS_TEAM", "").strip(), os.environ.get("AC_ACCESS_AUD", "").strip()
    verifier = auth.AccessVerifier(team, aud, os.environ.get("AC_ACCESS_CERTS_URL") or None) if team and aud else None
    require = os.environ.get("AC_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")
    proxy_secret = os.environ.get("AC_PROXY_SECRET", "").strip() or None
    mode = ("trusted proxy only (X-Flowgt-Proxy)" if proxy_secret else
            "Cloudflare Access (" + team + ")" if verifier else "none — LAN mode")
    print(f"  auth:   {mode}{', required for every request' if require else ''}")
    server.serve(str(LIB), a.port, a.host, app_dir=str(PKG / "app"), verifier=verifier,
                 require_auth=require, proxy_secret=proxy_secret)


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
    b.add_argument("--title", help="override the title from book.json / the book metadata")
    b.add_argument("--author", help="override the author")
    b.add_argument("--slug", help="URL name in the library (default: slugified title)")
    b.add_argument("--language", help="ISO code, e.g. en / zh. Default: whisper auto-detects")
    b.add_argument("--model", default=transcribe.DEFAULT_MODEL, help=f"whisper model (default {transcribe.DEFAULT_MODEL})")
    b.add_argument("--backend", default=os.environ.get("AC_BACKEND", "auto"), choices=["auto", "mlx", "faster"])
    b.add_argument("--audio", default="symlink", choices=["symlink", "hardlink", "copy"],
                   help="how audio enters the library (default symlink, relative so the folder stays portable)")
    b.set_defaults(fn=cmd_build)

    t = sp.add_parser("transcribe", help="fill the transcript cache for one or more book dirs, without building")
    t.add_argument("source", nargs="+")
    t.add_argument("--language"); t.add_argument("--model", default=transcribe.DEFAULT_MODEL)
    t.add_argument("--backend", default=os.environ.get("AC_BACKEND", "auto"), choices=["auto", "mlx", "faster"])
    t.set_defaults(fn=cmd_transcribe)

    s = sp.add_parser("serve", help="serve the library over HTTP")
    s.add_argument("--port", type=int, default=int(os.environ.get("AC_PORT", 8765)))
    s.add_argument("--host", default=os.environ.get("AC_HOST", "0.0.0.0"), help="default 0.0.0.0 (reachable on the LAN)")
    s.set_defaults(fn=cmd_serve)

    sp.add_parser("list", help="list books in the library").set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

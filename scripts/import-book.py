#!/usr/bin/env python3
"""Lay one standalone title out under books/, from a folder holding the book and its audio.

    scripts/import-book.py SRC [SRC...] [--slug S] [--title T] [--author A] [--narrator N]
                           [--language en] [--exclude GLOB]...

SRC is a directory containing one book file (epub/pdf/mobi/azw3) and its audio, either loose or
under an `audio/` (or `disc*/`, `cd*/`) sub-directory. `--exclude` drops audio by glob — use it
for the sample tracks and whole-book concatenations that sit beside the real ones and would
otherwise be transcribed twice and align the book against itself.

Audio and book are hard-linked, never copied: no extra disk, and name+size+mtime stay identical,
which is exactly the transcript cache key. Covers come from a cover.* file beside the source, else from the book itself (an EPUB's
declared cover image, or page 1 of a PDF).

For a multi-volume set use import-series.py instead.
"""
from __future__ import annotations
import argparse, fnmatch, json, os, pathlib, re, shutil, sys

BOOK_EXT = [".epub", ".azw3", ".azw", ".mobi", ".pdf"]        # preference order
AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav"}
COVER_EXT = (".jpg", ".jpeg", ".png", ".webp")


def natural(p: pathlib.Path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", p.name)]


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "book"


def link(src: pathlib.Path, dst: pathlib.Path):
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)          # copy2 keeps mtime, so the transcript cache still matches


def book_cover(book: pathlib.Path) -> tuple[bytes, str] | None:
    """Cover art out of the book itself: the declared cover image of an EPUB, or the first image
    on page 1 of a PDF (which needs Pillow — `pip install 'pypdf[image]'`)."""
    if book.suffix.lower() == ".epub":
        import sys as _sys
        _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from audiobook_connector.epub import cover_bytes
        return cover_bytes(str(book))
    if book.suffix.lower() != ".pdf":
        return None
    try:
        from pypdf import PdfReader
        img = next(iter(PdfReader(str(book)).pages[0].images), None)
        return (img.data, ".jpg") if img else None
    except Exception:
        return None                     # Pillow missing, encrypted, or no image on page 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="+")
    ap.add_argument("--slug"); ap.add_argument("--title"); ap.add_argument("--author", default="")
    ap.add_argument("--narrator", default=""); ap.add_argument("--language", default="")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="drop audio whose file name matches (repeatable), e.g. --exclude '*试听*'")
    ap.add_argument("--books", default="books")
    ap.add_argument("--prefer", choices=[e.lstrip(".") for e in BOOK_EXT],
                    help="pick this format when the folder has several")
    a = ap.parse_args()
    if len(a.src) > 1 and (a.slug or a.title):
        sys.exit("--slug/--title only make sense with a single source")

    for s in a.src:
        src = pathlib.Path(s)
        if not src.is_dir():
            sys.exit(f"not a directory: {src}")
        order = [f".{a.prefer}"] + BOOK_EXT if a.prefer else BOOK_EXT
        book = next((f for ext in order for f in sorted(src.rglob(f"*{ext}")) if f.is_file()), None)
        if not book:
            sys.exit(f"no book file in {src} ({' '.join(BOOK_EXT)})")
        audio = sorted((f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXT
                        and not any(fnmatch.fnmatch(f.name, g) for g in a.exclude)), key=natural)
        dropped = sum(1 for f in src.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXT
                      and any(fnmatch.fnmatch(f.name, g) for g in a.exclude))
        if not audio:
            sys.exit(f"no audio in {src}")
        title = a.title or book.stem
        slug = a.slug or slugify(title)
        out = pathlib.Path(a.books) / slug
        out.mkdir(parents=True, exist_ok=True)
        for f in audio:
            link(f, out / f.name)
        link(book, out / f"{slug}{book.suffix.lower()}")
        cov = next((c for c in sorted(src.rglob("cover.*")) if c.suffix.lower() in COVER_EXT), None)
        if cov:
            shutil.copy2(cov, out / ("cover" + cov.suffix.lower()))
        else:
            got = book_cover(book)
            if got:
                for old in out.glob("cover.*"):
                    old.unlink()
                (out / ("cover" + got[1])).write_bytes(got[0])
        meta = {"title": title, "author": a.author, "slug": slug}
        if a.narrator: meta["narrator"] = a.narrator
        if a.language: meta["language"] = a.language
        json.dump(meta, open(out / "book.json", "w"), ensure_ascii=False, indent=1)
        print(f"{out}: {len(audio)} audio" + (f" ({dropped} excluded)" if dropped else "") +
              f", {book.name}, cover={'yes' if list(out.glob('cover.*')) else 'no'}")


if __name__ == "__main__":
    main()

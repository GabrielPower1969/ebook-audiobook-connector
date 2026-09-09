#!/usr/bin/env python3
"""Lay a multi-volume series out under books/, one directory per volume.

    scripts/import-series.py AUDIO_ROOT BOOK_ROOT --series "Harry Potter" --prefix hp \
        [--titles titles.txt] [--author "J.K. Rowling"] [--narrator "Stephen Fry"]

AUDIO_ROOT holds one sub-directory of audio files per volume; BOOK_ROOT holds one book file
(epub/pdf/mobi/azw3) per volume. Both are sorted naturally and paired in order — check the plan
it prints before answering the confirmation.

Audio and book files are **hard-linked**, never copied: no extra disk, and name+size+mtime are
identical, which is exactly the transcript cache key. A cross-device link falls back to copy2,
which preserves mtime for the same reason. Covers come from the book itself — an EPUB's declared cover
image, or page 1 of a PDF (which needs Pillow) — else from a cover.* file next to it.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, shutil, sys

BOOK_EXT = (".epub", ".azw3", ".azw", ".mobi", ".pdf")
AUDIO_EXT = (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav")


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
    except Exception as e:                                  # Pillow missing, encrypted, no image
        print(f"  (no cover from {book.name}: {e})")
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio_root"); ap.add_argument("book_root")
    ap.add_argument("--series", required=True)
    ap.add_argument("--prefix", help="slug prefix, default: slugified series name")
    ap.add_argument("--author", default=""); ap.add_argument("--narrator", default="")
    ap.add_argument("--language", default="")
    ap.add_argument("--titles", help="one volume title per line, in order; default: the book file stem")
    ap.add_argument("--books", default="books", help="destination, default ./books")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    a = ap.parse_args()

    audio_root, book_root = pathlib.Path(a.audio_root), pathlib.Path(a.book_root)
    vols = sorted((d for d in audio_root.iterdir()
                   if d.is_dir() and any(f.suffix.lower() in AUDIO_EXT for f in d.iterdir())), key=natural)
    bookf = sorted((f for f in book_root.iterdir() if f.suffix.lower() in BOOK_EXT), key=natural)
    if not vols or not bookf:
        sys.exit(f"nothing to pair: {len(vols)} audio dir(s), {len(bookf)} book file(s)")
    if len(vols) != len(bookf):
        sys.exit(f"{len(vols)} audio dirs but {len(bookf)} book files — pair them by hand")
    titles = [l.strip() for l in open(a.titles) if l.strip()] if a.titles else [f.stem for f in bookf]
    if len(titles) != len(vols):
        sys.exit(f"--titles has {len(titles)} lines, expected {len(vols)}")

    prefix = a.prefix or slugify(a.series)
    dest = pathlib.Path(a.books)
    plan = []
    for i, (ad, bf, t) in enumerate(zip(vols, bookf, titles), 1):
        n = len([f for f in ad.iterdir() if f.suffix.lower() in AUDIO_EXT])
        plan.append((i, ad, bf, t, n, dest / f"{prefix}{i}-{slugify(t)[:48]}"))
    print(f"{a.series}: {len(plan)} volume(s) → {dest}/")
    for i, ad, bf, t, n, out in plan:
        print(f"  {i}. {t[:44]:46} {n:3} audio  ←  {ad.name[:34]:36} + {bf.name[:34]}")
    if not a.yes and input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("cancelled")

    for i, ad, bf, t, n, out in plan:
        out.mkdir(parents=True, exist_ok=True)
        for f in sorted((f for f in ad.iterdir() if f.suffix.lower() in AUDIO_EXT), key=natural):
            link(f, out / f.name)
        link(bf, out / f"{prefix}{i}{bf.suffix.lower()}")
        got = book_cover(bf)
        if got:
            for old in out.glob("cover.*"):
                old.unlink()
            (out / ("cover" + got[1])).write_bytes(got[0])
        else:
            src_cov = next((c for c in sorted(bf.parent.glob("cover.*"))), None)
            if src_cov:
                shutil.copy2(src_cov, out / ("cover" + src_cov.suffix.lower()))
        meta = {"title": t, "author": a.author, "series": a.series, "volume": i}
        if a.narrator: meta["narrator"] = a.narrator
        if a.language: meta["language"] = a.language
        json.dump(meta, open(out / "book.json", "w"), ensure_ascii=False, indent=1)
        print(f"  {out}: {n} audio, cover={'yes' if list(out.glob('cover.*')) else 'no'}")
    print(f"\nnext:\n  audiobook-connector transcribe {' '.join(str(p[5]) for p in plan)}\n"
          f"  audiobook-connector build <name>   # once per volume, seconds off the cache")


if __name__ == "__main__":
    main()

"""Book file → Book, dispatched on extension.

  .epub               built in (stdlib only)
  .mobi .azw .azw3    needs `mobi`   — unpacks to EPUB/HTML, then reuses the EPUB walker
  .pdf                needs `pypdf`  — text extraction + paragraph reassembly heuristics
Install the optional formats with:  pip install 'audiobook-connector[formats]'
"""
from __future__ import annotations
import html as htmlmod, pathlib, re, shutil
from collections import Counter
from .epub import Book, Para, parse_epub, paras_from_html

BOOK_EXT = [".epub", ".azw3", ".azw", ".mobi", ".pdf"]   # preference order when a folder has several


def find_book(src: pathlib.Path) -> pathlib.Path | None:
    for ext in BOOK_EXT:
        hits = sorted(p for p in src.iterdir() if p.suffix.lower() == ext)
        if hits:
            return hits[0]
    return None


def parse_book(path: pathlib.Path) -> Book:
    ext = path.suffix.lower()
    if ext == ".epub":
        return parse_epub(str(path))
    if ext in (".mobi", ".azw", ".azw3"):
        return _parse_mobi(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    raise SystemExit(f"unsupported book format: {path.name} (supported: {' '.join(BOOK_EXT)})")


def _need(module: str):
    try:
        return __import__(module)
    except ImportError:
        raise SystemExit(f"{module} is not installed — run:  pip install 'audiobook-connector[formats]'\n"
                         f"(the Docker `cli` image already has it: docker compose run --rm cli build <name>)")


# ---------------------------------------------------------------- MOBI / AZW
def _parse_mobi(path: pathlib.Path) -> Book:
    mobi = _need("mobi")
    tmp, _ = mobi.extract(str(path))
    try:
        tmp = pathlib.Path(tmp)
        epub = next(iter(sorted(tmp.rglob("*.epub"))), None)     # KF8 books unpack to a real EPUB
        if epub:
            return parse_epub(str(epub))
        html_files = sorted(tmp.rglob("*.html")) + sorted(tmp.rglob("*.htm"))
        if not html_files:
            pdf = next(iter(sorted(tmp.rglob("*.pdf"))), None)
            if pdf:
                return _parse_pdf(pdf)
            raise SystemExit(f"could not find any text inside {path.name}")
        paras: list[Para] = []
        chapter = ""
        for f in html_files:
            chapter = paras_from_html(f.read_text("utf-8", "replace"), f.name, paras, chapter)
        return Book(path.stem, "", paras)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- PDF
_END = re.compile(r"[.!?:;…]['\"”’)\]]*$")
_PAGE_NO = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,6})\s*$", re.I)


def _parse_pdf(path: pathlib.Path) -> Book:
    """Text PDFs only (not scans). Uses pypdf's layout mode, where vertical white space shows up as
    blank lines: the most common blank-run length is ordinary line spacing, anything longer is a
    paragraph gap. Also drops running headers/footers and page numbers, undoes hyphenation, and
    treats a lone short un-punctuated block as a heading. Falls back to a sentence-end heuristic
    for PDFs that expose no gaps at all."""
    pypdf = _need("pypdf")
    reader = pypdf.PdfReader(str(path))
    meta = reader.metadata or {}
    title = (meta.get("/Title") or path.stem).strip()
    author = (meta.get("/Author") or "").strip()
    pages = []
    for pg in reader.pages:
        try:
            txt = pg.extract_text(extraction_mode="layout")
        except TypeError:                       # very old pypdf
            txt = pg.extract_text()
        pages.append([ln.rstrip() for ln in (txt or "").splitlines()])

    # running headers / footers: short lines that repeat on many pages
    freq = Counter(ln.strip().lower() for lines in pages for ln in set(lines) if 0 < len(ln.strip()) < 40)
    repeated = {k for k, n in freq.items() if n >= max(3, 0.3 * len(pages))}

    # blank-run statistics decide what a paragraph gap looks like
    runs: list[int] = []
    for lines in pages:
        run, seen = 0, False
        for ln in lines:
            if ln.strip():
                if seen: runs.append(run)
                run, seen = 0, True
            else:
                run += 1
    spacing = Counter(runs).most_common(1)[0][0] if runs else 0
    has_gaps = any(r > spacing for r in runs)

    paras: list[Para] = []
    buf: list[str] = []
    chapter = ""

    def flush(tag: str = "p"):
        nonlocal chapter
        if not buf:
            return
        text = " ".join(buf).strip(); buf.clear()
        if not text:
            return
        if tag == "p" and len(text) < 80 and not _END.search(text) and (text.isupper() or text.istitle()):
            tag = "h2"
        if tag != "p":
            chapter = text
        paras.append(Para(len(paras), path.name, chapter, tag, htmlmod.escape(text), text))

    prev_full = 60.0
    for lines in pages:
        widths = [len(l.strip()) for l in lines if l.strip()]
        full = 0.85 * max(widths) if widths else 60
        blank, seen_text = 0, False
        for raw in lines:
            s_ = raw.strip()
            if not s_:
                blank += 1
                continue
            if s_.lower() in repeated or _PAGE_NO.match(s_):
                continue                     # headers/footers neither count as text nor reset the gap
            if has_gaps and seen_text and blank > spacing:
                flush()                      # a gap between two text lines on the same page
            elif not seen_text and buf and len(buf[-1]) < prev_full and (
                    _END.search(buf[-1]) or buf[-1].isupper() or buf[-1].istitle()):
                flush()                      # page break: previous page ended on a short closed line or a heading
            blank, seen_text = 0, True
            if buf and buf[-1].endswith("-") and not buf[-1].endswith(" -"):
                buf[-1] = buf[-1][:-1] + s_
            else:
                buf.append(s_)
            if not has_gaps and len(s_) < full and _END.search(s_):
                flush()
        prev_full = full
    flush()
    return Book(title, author, paras)

"""Book file → Book, dispatched on extension.

  .epub               built in (stdlib only)
  .mobi .azw .azw3    needs `mobi`   — unpacks to EPUB/HTML, then reuses the EPUB walker
  .pdf                needs `pypdf`  — text extraction + paragraph reassembly heuristics
Install the optional formats with:  pip install 'audiobook-connector[formats]'
"""
from __future__ import annotations
import difflib, html as htmlmod, pathlib, re, shutil
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


# ---------------------------------------------------------------- chapter hints
_WORD = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _WORD.sub(" ", s.lower().replace("’", "'")).strip()


def mark_chapters(paras: list[Para], titles: list[str], tag: str = "h1") -> int:
    """Promote the headings that match the expected chapter titles, in order, to `tag` (h1).
    `titles` usually come from the audio file names ("Chapter 03 - The Knight Bus.mp3"), so one
    audio file ↔ one chapter and the reader's table of contents lists exactly the real chapters —
    not every shouted line, letter signature or newspaper headline a PDF heuristic picks up.
    Matching is fuzzy (typos in either source are common) and monotonic; a missing heading is
    skipped rather than searched for elsewhere. Returns the number of chapters marked."""
    want = [_norm(t) for t in titles]
    j, marked = 0, []
    for p in paras:
        if j >= len(want) or p.tag == "p":
            continue
        cand = _norm(p.text)
        if not cand or len(cand) > 120:
            continue
        best, score = -1, 0.0
        for k in range(j, min(j + 3, len(want))):                 # tolerate up to 2 undetected headings
            w = want[k]
            r = difflib.SequenceMatcher(None, cand, w).ratio()
            # Containment only counts when the two are of comparable length: without that guard a
            # generic heading ("HOGWARTS") swallows a later chapter ("The Battle of Hogwarts") and
            # every chapter after it shifts by one.
            if len(w) >= 8 and (w in cand or cand in w) and min(len(w), len(cand)) >= 0.6 * max(len(w), len(cand)):
                r = max(r, 0.9)
            if r > score:
                best, score = k, r
        if score >= 0.78:
            marked.append(p); j = best + 1
    # All-or-nothing: a handful of coincidental matches means these were not chapter titles at all
    # (e.g. audio split into "Part01…Part04"), and half a table of contents is worse than none.
    if len(marked) < 0.6 * len(want):
        return 0
    for p in marked:
        p.tag = tag
    hits = len(marked)
    if hits:                                                       # running chapter title follows the real chapters
        chapter = ""
        for p in paras:
            if p.tag == tag:
                chapter = p.text
            p.chapter = chapter
    return hits


def chapter_hints_from_names(names: list[str]) -> list[str]:
    """'Chapter 03 - The Knight Bus.mp3' → 'The Knight Bus'. Returns [] unless most names carry a title."""
    out = []
    for n in names:
        s = pathlib.Path(n).stem
        s = re.sub(r"^\s*\d+[\s._-]+", "", s)                         # leading track number
        s = re.sub(r"^\s*(chapter|ch\.?|part|track)\s*\d+\s*[-–—.:]*\s*", "", s, flags=re.I)
        s = s.strip(" -–—_.")
        out.append(s)
    good = [s for s in out if s and not s.isdigit() and len(s) > 2]
    shapes = {re.sub(r"\d+", "#", s) for s in good}                  # "Part01…Part04" are not chapter titles
    return out if len(good) >= max(3, 0.6 * len(names)) and len(shapes) > 1 else []


# ---------------------------------------------------------------- PDF
_END = re.compile(r"[.!?:;…]['\"”’)\]]*$")
_PAGE_NO = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,6})\s*$", re.I)
_HDR = re.compile(r"[\s\d]+")


def _hdr_key(line: str) -> str:
    """Running-header identity: digits and all whitespace dropped, so "Page | 33 …" and the
    letter-spaced "P a g e | 2 …" that layout mode emits on some pages collapse to one key."""
    return _HDR.sub("", line.lower())


def _standalone_headings(lines: list[str]) -> set[int]:
    """Indices of lines that are visually a heading: a short ALL-CAPS line sitting alone between
    blank lines. Every chapter title in the PDFs we have looks like this, and recognising it by
    shape — rather than by the paragraph-gap statistics — is what keeps chapter detection working
    in books whose body text is set with the same line spacing as the gap around a title."""
    out = set()
    for i, ln in enumerate(lines):
        t = ln.strip()
        if not (2 < len(t) < 80) or _END.search(t) or _PAGE_NO.match(t):
            continue
        letters = [c for c in t if c.isalpha()]
        if len(letters) < 3 or any(c.islower() for c in letters):
            continue
        if (i == 0 or not lines[i - 1].strip()) and (i + 1 >= len(lines) or not lines[i + 1].strip()):
            out.add(i)
    return out


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

    # running headers / footers: lines that repeat on many pages once digits are masked
    # ("Page | 33  Harry Potter and the Philosopher's Stone – J.K. Rowling")
    freq = Counter(_hdr_key(ln) for lines in pages for ln in set(lines) if 0 < len(ln.strip()) < 100)
    repeated = {k for k, n in freq.items() if n >= max(3, 0.2 * len(pages))}

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
        if tag == "p" and len(text) < 80 and not _END.search(text) and text.isupper():
            tag = "h2"                       # ALL-CAPS short line = heading; Title Case is left to mark_chapters()
        if tag != "p":
            chapter = text
        paras.append(Para(len(paras), path.name, chapter, tag, htmlmod.escape(text), text))

    prev_full = 60.0
    for lines in pages:
        widths = [len(l.strip()) for l in lines if l.strip()]
        full = 0.85 * max(widths) if widths else 60
        heads = _standalone_headings(lines)
        blank, seen_text = 0, False
        for idx, raw in enumerate(lines):
            s_ = raw.strip()
            if not s_:
                blank += 1
                continue
            if _hdr_key(s_) in repeated or _PAGE_NO.match(s_):
                continue                     # headers/footers neither count as text nor reset the gap
            if idx in heads:                 # a heading closes the previous block and is its own para
                flush()
                buf.append(s_)
                flush("h2")
                blank, seen_text = 0, True
                continue
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

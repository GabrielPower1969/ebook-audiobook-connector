# audiobook-connector — guide for AI agents and new contributors

Read this first. It is the map; `README.md` is the user manual.

## What this is
Aligns an EPUB with its audiobook so a reader can click any paragraph and hear it.
Pipeline: `formats.py` (epub/mobi/azw3/pdf → paragraphs) → `transcribe.py` (whisper, cached) → `align.py` (n-gram anchors) → `library/<slug>/data.json` → `server.py` + `app/*.html`.

## Commands
```bash
.venv/bin/pip install -e '.[mlx]'            # Apple Silicon dev install ([cpu] elsewhere)
.venv/bin/audiobook-connector build <name>   # name = folder under books/
.venv/bin/audiobook-connector transcribe <name>...   # only fill the cache; run a long series in the background
.venv/bin/audiobook-connector serve          # http://localhost:8765 + LAN URLs
docker compose up -d                         # reader container (target: server)
docker compose --profile tools build cli     # whisper container; NOT built by plain `compose build`
docker compose run --rm cli build <name>     # build inside Docker (Linux path)
docker compose --profile public up -d        # + cloudflared tunnel; needs TUNNEL_TOKEN/AC_ACCESS_* in .env
```
Before committing hours to transcription, check the book parses: a DRM-stripped EPUB can be a
392-word stub while the PDF beside it holds the whole text, and that costs nothing to find out.

```bash
.venv/bin/python -c "
import pathlib,sys; sys.path.insert(0,'.')
from audiobook_connector import formats
b=formats.parse_book(formats.find_book(pathlib.Path('books/<name>')))
ls=sorted(len(x.text.split()) for x in b.paras)
print(len(b.paras),'paras', sum(ls),'words, median', ls[len(ls)//2])"
```
Under ~5000 words means the file is a stub; a median over ~120 words per paragraph means the
paragraph splitting failed and click granularity will be coarse.

There is no test suite yet. auth + API were verified end-to-end with openssl-generated keys (see git history of this line for the script idea: JWKS file via AC_ACCESS_CERTS_URL=file://…, curl the 15 cases). Verify a change by rebuilding `zero-to-one` (transcripts are cached, ~5 s) and checking the printed `aligned:` line stays at 628/1256, then click a paragraph in the browser.

## Layout
```
audiobook_connector/   the package. Core is pure stdlib — keep it that way.
  __main__.py          CLI + paths (AC_BOOKS/AC_LIBRARY/AC_CACHE env vars) + library index
  formats.py           find_book()/parse_book(): dispatch on extension. mobi/azw3 via `mobi` (unpack → EPUB/HTML), pdf via `pypdf` layout mode + gap heuristics. Both lazy, extra [formats]
                       mark_chapters(): fuzzy, monotonic match of detected headings against the chapter titles taken from the audio file names, so the TOC lists real chapters only
  epub.py              .epub → [Para]; regex over spine HTML, no lxml. paras_from_html() is shared with formats.py
  transcribe.py        backends: mlx (Apple) / faster (anywhere); cache key = name|size|mtime
  align.py             pure function align(paras, transcripts) → {files, paras:[{f,s,e,d,sent}|None], stats}
                       sentences()/tokens_pos() split a paragraph and map each sentence to a time;
                       `sent` is [[charStart, charEnd, s, e], …] over the paragraph's PLAIN text
  server.py            static HTTP with Range support, pre-compressed .gz siblings + JSON API (/api/me, /api/progress[/<slug>], /api/marks[/<slug>]); per-user progress + saved passages in library/_progress/
  auth.py              identify() → email | "local" | denied. Two sources: Cloudflare Access JWT (RS256 via pow(), stdlib only) or a trusted proxy (AC_PROXY_SECRET + X-Flowgt-User, used by flowgt.co.nz/read/*)
  app/index.html       bookshelf: series grouping, continue-reading, search   (served straight from the package; library/ is data only)
  app/reader.html      reader: sentence-level play/highlight, practice mode (repeat + shadowing
                       pause), word concordance + vocabulary list, TOC, search, saved passages,
                       four themes (auto/light/dark/e-ink), type controls, sleep timer, Media Session
  app/flowgt*.svg      the FlowGT mark, light and reverse; served by the package, never fetched
scripts/               import-book.py  one title      import-series.py  a multi-volume set
                       cache-status.py how far transcription got   build-ready.py  build what is ready
                       verify-text.py  cross-check the shown text against the audio
                       build-dict.py   library-scoped dictionary from ECDICT → library/_dict/
                       package.sh      a folder another machine can run with no install
                       backup.sh       library + transcripts + dictionary, verified
books/<name>/          INPUT: one .epub + audio files (+cover.jpg, +book.json). git-ignored.
                       book.json: title, author, series, volume, narrator, language, chapters[]
library/<slug>/        OUTPUT: data.json, cover, audio/ (relative symlinks into books/). git-ignored.
cache/transcripts/     whisper output per audio file; the expensive artifact. git-ignored, back it up.
cache/models/          downloaded whisper weights (HF_HOME in Docker). Re-downloadable.
cache/dict/ecdict.csv  the dictionary source, 66 MB, fetched once. git-ignored, re-downloadable.
library/_dict/         dict.json(+.gz): only the words this library uses. Fetched by the reader
                       lazily, on the first lookup.
```

## Invariants — do not break
- **Zero runtime dependencies in the core.** whisper backends are optional extras and are imported lazily inside `transcribe.py` only.
- **Audio symlinks are relative** (`../../../books/...`). `books/` and `library/` must sit under the same parent, on host and in the container (`/app/books`, `/app/library`). This is what makes the folder copy-and-run.
- **Transcript cache is keyed on file name+size+mtime.** Copy books with `cp -p` / `rsync -a`, or the cache misses and hours of transcription rerun.
- **The server must honour `Range`.** `<audio>` seeking silently breaks without it (root cause of the first bug we hit).
- A paragraph is "aligned" only if it contains an exact anchor; headings may be within 3 tokens. Loosening this re-introduces false hits on the copyright page and index.
- `align.align()` stays pure and framework-free so it can be unit-tested and reused.
- Reader must run from a single HTML file with no build step and no external requests. Its own
  assets (the FlowGT mark, the favicon) are served from the package alongside it, same origin.
- **The dictionary is built for this library, never shipped whole.** ECDICT (MIT) is 66 MB of CSV;
  `build-dict.py` keeps only the words the built books actually use — 29 k of them here, 2.9 MB /
  1.0 MB gzipped — resolving inflections back to their base form through ECDICT's own `exchange`
  column. The reader fetches it on the first word lookup and never before, so a reader who only
  listens never pays for it. Text selection is still never hijacked: long-press must keep reaching
  the device's own dictionary, and the concordance (how often a word occurs in *this book*, and
  every sentence it occurs in) is the part no dictionary can give you.
- **Sentence spans are wrapped lazily, driven by scroll position** — not by an IntersectionObserver
  rooted on the scroller, which never fires while the tab is hidden or the pane is collapsed and
  leaves the sentence layer silently missing. Wrapping all 93k sentences up front would add tens of
  thousands of elements to a big volume. It has to open on a Kindle experimental browser and a Boox e-ink tablet: system fonts only (a webfont fetch is a blank page), no CSS `:has()`, and the e-ink theme is pure black/white with every transition disabled.
- Chapter titles come from the audio file names, not from the book's own headings: one audio file is one chapter, so the TOC and the player agree. `mark_chapters()` commits all-or-nothing (a <60% match rate means those were never chapter titles) and guards containment matches by length ratio (without it "HOGWARTS" swallows "The Battle of Hogwarts" and every later chapter shifts by one).
- **Auth is never home-grown.** Identity is either a verified Cloudflare Access JWT email, or `X-Flowgt-User` from a proxy that proved itself with `AC_PROXY_SECRET` (constant-time compare; when the secret is set, every request without it is refused, LAN included). Requests that carry `Cf-Ray`/`Cf-Connecting-Ip` but no valid token are refused (fail closed). Anonymous "local" users never get server-side storage.
- `library/_progress/` is per-user data: back it up, never serve it, never commit it.
- **The transcript never reaches the reader.** `data.json` paragraphs carry `id/tag/html/t` only —
  `html` is the book's own text and `t` is `{f,s,e,d}`. whisper output is a ruler for timings and
  nothing else. Anything that would put transcript text on screen breaks the core promise.
- `library/` holds data only (data.json, covers, audio links, index.json). Never copy code into it — an older container image would overwrite a newer host copy, or vice versa.

## Conventions
- Python ≥ 3.10, `from __future__ import annotations`, small modules, docstring at top of each file saying what it does and why.
- English in code, comments, README, commit messages; a short 中文速览 section at the end of README.
- User data (`books/ library/ cache/`) is never committed. `.gitkeep` files keep the empty dirs so a fresh clone runs.
- Change architecture → update this file in the same commit.

## Known limits / next steps
- Only tested on one English book. CJK tokenization exists in `align.py` but is unverified.
- PDF input is heuristic (tested on a synthetic reportlab fixture only); MOBI/AZW3 path is untested until a real file arrives. Scanned PDFs (no text layer) are not supported.
- CPU transcription (Docker) runs at about real time: measured 60 s of audio → 65 s, large-v3-turbo int8, 4 threads, Docker on an M-series Mac. mlx on the same Mac: ~14× real time. Recommend `--model small` on CPU or building once on Apple Silicon.
- No auth on the server — it is meant for a trusted LAN only.

## Status 2026-09-10 — library

13 books, 62 h of them outside Harry Potter. Alignment and the independent text check:

| slug | aligned | text↔audio median | ≥0.90 |
|---|---|---|---|
| hp1..hp7 | 98-99 % | 1.000 | 91-96 % |
| e-myth-revisited | 94 % | 1.000 | 96.9 % |
| franklin-autobiography | 87 % | 0.953 | 89.0 % |
| on-writing | 83 % | 0.988 | 97.4 % |
| start-with-why | 80 % | 0.975 | 94.7 % |
| thank-you-economy | 75 % | 0.995 | 97.4 % |
| zero-to-one | 50 % | 1.000 | 98.4 % |

Sentence spans: 93,438 across the thirteen books. data.json totals 14 MB, 4 MB gzipped.

zero-to-one's 50 % is its EPUB, not the aligner: the file puts `h1` on its own contents page, so
`chapterOf()` maps most of the book to the wrong chapter and half the paragraphs never anchor.

`just-for-fun` was built and then dropped: its PDF is a scan whose OCR is damaged — 29 % of
paragraphs have words run together ("theUnited", "LinusTort/aids") against 2 % for a clean EPUB,
and the reader shows the book's own text, so that damage would be what you read. Its transcripts
are still cached, so a clean copy of the book is cheap to build.

## Text verification

`scripts/verify-text.py` turns the transcript around and uses it as an independent witness: for
each aligned paragraph it measures how much of the book's token sequence appears in the audio over
that paragraph's span. Across 32291 paragraphs in eight books the median coverage is 1.000 and
93.6 % reach 0.90; 16 paragraphs (0.05 %) fall under 0.55 and every one of them is dialect
("Yeh all righ', Harry?"), a mouth-full mumble, or a number the book spells out and whisper writes
as digits — the aligner's `NUM` map only covers 0-10, a known gap. Read a low score as a prompt to
look, not as a verdict: it can equally mean whisper misheard.

Regenerate with `scripts/verify-text.py --report reports/text-verification.md` (reports/ is
git-ignored).

## Status 2026-09-09 — Harry Potter series

`books/hp1..hp7` (Stephen Fry, 124 h of audio, 199 chapters) built from the seven PDFs plus one
mp3 per chapter. Chapter detection **199/199**, no header text left in the prose, and
**37587/38193 paragraphs aligned (98 %)** across the series — an American-edition PDF against a
British narration, so the n-gram anchors survive a fair number of word-level differences.
Transcription took **9.0 h** with mlx on Apple Silicon (~14x real time); cache 47 MB, library 13 MB.

```bash
scripts/import-series.py <audio-root> <book-root> --series "…" --prefix hp --narrator "…"
.venv/bin/audiobook-connector transcribe books/hp1-* books/hp2-* …    # hours, run in background
.venv/bin/python scripts/build-ready.py --watch 300 books/hp*         # builds each as it lands
.venv/bin/python scripts/cache-status.py books/hp*                    # how far along it is
```
The transcript cache makes an interrupted run free to resume — rerun the same command.

## Status 2026-09-08 — handing off to another Mac

Done and verified on this machine: LAN reader (Docker), mlx transcription, Docker cli
transcription, multi-format parse, Cloudflare Access mode, trusted-proxy mode,
flowgt-website `/read/*` proxy (commit c418834 there, **not pushed**).

Remaining to go live, in order:
1. Cloudflare Zero Trust: finish "Activate Zero Trust Free" (needs card consent — human only),
   create a Tunnel, public hostname `reader-tunnel.flowgt.co.nz` → `http://reader:8765`.
2. `.env` here: `TUNNEL_TOKEN`, `AC_PROXY_SECRET=<long random>`; then
   `docker compose --profile public up -d`.
3. flowgt-website: `wrangler pages secret put READER_UPSTREAM` (= https://reader-tunnel.flowgt.co.nz)
   and `READER_SECRET` (= same random string); push c418834 so Pages redeploys with the secrets.
4. In the flowgt admin page add a resource: kind `ebook`, url `/read/`.
Assumptions to revisit: `/read/` is gated to plan=runway|admin (`canRead` in the Function);
login bounces to resources.html, not back into /read/ (safeNext only takes `x.html`).

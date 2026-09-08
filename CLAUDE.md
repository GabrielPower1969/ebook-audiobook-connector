# audiobook-connector — guide for AI agents and new contributors

Read this first. It is the map; `README.md` is the user manual.

## What this is
Aligns an EPUB with its audiobook so a reader can click any paragraph and hear it.
Pipeline: `formats.py` (epub/mobi/azw3/pdf → paragraphs) → `transcribe.py` (whisper, cached) → `align.py` (n-gram anchors) → `library/<slug>/data.json` → `server.py` + `app/*.html`.

## Commands
```bash
.venv/bin/pip install -e '.[mlx]'            # Apple Silicon dev install ([cpu] elsewhere)
.venv/bin/audiobook-connector build <name>   # name = folder under books/
.venv/bin/audiobook-connector serve          # http://localhost:8765 + LAN URLs
docker compose up -d                         # reader container (target: server)
docker compose --profile tools build cli     # whisper container; NOT built by plain `compose build`
docker compose run --rm cli build <name>     # build inside Docker (Linux path)
docker compose --profile public up -d        # + cloudflared tunnel; needs TUNNEL_TOKEN/AC_ACCESS_* in .env
```
There is no test suite yet. auth + API were verified end-to-end with openssl-generated keys (see git history of this line for the script idea: JWKS file via AC_ACCESS_CERTS_URL=file://…, curl the 15 cases). Verify a change by rebuilding `zero-to-one` (transcripts are cached, ~5 s) and checking the printed `aligned:` line stays at 628/1256, then click a paragraph in the browser.

## Layout
```
audiobook_connector/   the package. Core is pure stdlib — keep it that way.
  __main__.py          CLI + paths (AC_BOOKS/AC_LIBRARY/AC_CACHE env vars) + library index
  formats.py           find_book()/parse_book(): dispatch on extension. mobi/azw3 via `mobi` (unpack → EPUB/HTML), pdf via `pypdf` layout mode + gap heuristics. Both lazy, extra [formats]
  epub.py              .epub → [Para]; regex over spine HTML, no lxml. paras_from_html() is shared with formats.py
  transcribe.py        backends: mlx (Apple) / faster (anywhere); cache key = name|size|mtime
  align.py             pure function align(paras, transcripts) → {files, paras:[{f,s,e,d}|None], stats}
  server.py            static HTTP with Range support + JSON API (/api/me, /api/progress[/<slug>]); per-user progress in library/_progress/
  auth.py              Cloudflare Access JWT verification (RS256 via pow(), stdlib only); identify() → email | "local" | denied
  app/index.html       bookshelf   app/reader.html  reader   (served straight from the package; library/ is data only)
books/<name>/          INPUT: one .epub + audio files (+cover.jpg). git-ignored.
library/<slug>/        OUTPUT: data.json, cover, audio/ (relative symlinks into books/). git-ignored.
cache/transcripts/     whisper output per audio file; the expensive artifact. git-ignored, back it up.
cache/models/          downloaded whisper weights (HF_HOME in Docker). Re-downloadable.
```

## Invariants — do not break
- **Zero runtime dependencies in the core.** whisper backends are optional extras and are imported lazily inside `transcribe.py` only.
- **Audio symlinks are relative** (`../../../books/...`). `books/` and `library/` must sit under the same parent, on host and in the container (`/app/books`, `/app/library`). This is what makes the folder copy-and-run.
- **Transcript cache is keyed on file name+size+mtime.** Copy books with `cp -p` / `rsync -a`, or the cache misses and hours of transcription rerun.
- **The server must honour `Range`.** `<audio>` seeking silently breaks without it (root cause of the first bug we hit).
- A paragraph is "aligned" only if it contains an exact anchor; headings may be within 3 tokens. Loosening this re-introduces false hits on the copyright page and index.
- `align.align()` stays pure and framework-free so it can be unit-tested and reused.
- Reader must run from a single HTML file with no build step and no external requests.
- **Auth is Cloudflare Access, never home-grown.** Identity = verified `Cf-Access-Jwt-Assertion` email. Requests that carry `Cf-Ray`/`Cf-Connecting-Ip` but no valid token are refused (fail closed). Anonymous "local" users never get server-side storage.
- `library/_progress/` is per-user data: back it up, never serve it, never commit it.
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

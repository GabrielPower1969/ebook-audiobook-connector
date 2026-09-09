# Audiobook Connector

Read a book while its audiobook plays. **Tap any paragraph to hear it.** The paragraph being read is highlighted and the page scrolls along.

Runs on any Linux or Mac box on your home network — a NAS, a mini PC, an old laptop. Books and audio never leave it.

Input: one **EPUB / MOBI / AZW3 / PDF** + the audiobook's **MP3 / M4A / M4B / …** files. Output: a web page your phone can open.

```
books/zero-to-one/                  library/zero-to-one/                 http://<server-ip>:8765
├── Zero to One.epub        ──►     ├── data.json  (paragraphs + times)   ──►   any phone / tablet / laptop
├── Part01.mp3 … Part04.mp3         └── audio/ → ../../books/…                  on the same Wi-Fi
└── cover.jpg
```

## Run it on the server (Docker, Linux or Mac)

```bash
git clone https://github.com/<you>/audiobook-connector && cd audiobook-connector
mkdir -p books/my-book          # copy ONE book file + its audio files (+ optional cover.jpg) inside
docker compose run --rm cli build my-book      # transcribe + align   (first run downloads the ~1.5 GB whisper model)
docker compose up -d                           # reader is live on http://<this-machine's-ip>:8765
```

Open that address on any device on the same network. Add more books by dropping a folder into `books/` and running the `build` line again — no restart needed.

- `docker compose logs reader` shows startup; the LAN address is this machine's IP on port 8765.
- The reader image (~230 MB) has no ML dependencies. Only the `cli` image carries whisper.
- Linux: containers run as uid/gid 1000 so files in `library/` stay yours. If `id -u` prints something else, run once: `printf 'AC_UID=%s\nAC_GID=%s\n' $(id -u) $(id -g) > .env`

## Transcribe fast on a Mac, then move the result to the server

CPU transcription in Docker runs at about real time (a 5-hour book ≈ 5 hours). An Apple Silicon Mac does it ~14× faster with the native install:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[mlx,formats]'
.venv/bin/audiobook-connector build my-book          # ~5 min per hour of audio
scripts/backup.sh                                    # → backups/audiobook-connector-<date>.tgz
```

Copy that archive to the server, then there:

```bash
git clone https://github.com/<you>/audiobook-connector && cd audiobook-connector
tar xzf /path/to/audiobook-connector-<date>.tgz      # restores books/ library/ cache/transcripts/
docker compose up -d
```

Nothing is re-transcribed: the archive carries the transcripts, and the audio links inside `library/` are relative, so the folder works wherever it lands.

## Public access with Google login (Cloudflare)

Expose the reader on your own domain with sign-in, without opening a port or writing an account system: a **Cloudflare Tunnel** carries traffic to the container, and **Cloudflare Access** puts Google login in front of it. The reader verifies the token Access attaches to every request (RS256, standard library only) and stores each signed-in reader's position server-side, so they resume on any device. Anonymous LAN readers keep their position in the browser as before.

One-time setup in the Cloudflare dashboard (your domain must be on Cloudflare):

1. **Zero Trust → Settings → Authentication → Login methods → Add Google.** Cloudflare shows the redirect URL; create an OAuth client in Google Cloud Console with it and paste the client ID/secret back.
2. **Zero Trust → Networks → Tunnels → Create a tunnel** (Cloudflared). Copy the token. Under *Public hostname* add `read.<your-domain>` → service `http://reader:8765`.
3. **Zero Trust → Access → Applications → Add → Self-hosted.** Domain `read.<your-domain>`, identity provider Google, a policy allowing the emails (or the Google Workspace domain) you want. On the app's *Overview* copy the **Application Audience (AUD) tag**.
4. On the server, put the three values in `.env`:

```bash
TUNNEL_TOKEN=eyJ...          AC_ACCESS_TEAM=<team name>          AC_ACCESS_AUD=<aud tag>
```

```bash
docker compose --profile public up -d
```

Visiting `https://read.<your-domain>` now shows Google's login first. Requests that arrive through Cloudflare without a valid token are refused (fail closed); requests from the LAN without one stay anonymous, unless you set `AC_REQUIRE_AUTH=1`.

Reading positions live in `library/_progress/<hash>.json`, one file per email — included in `scripts/backup.sh`.

## Behind an existing website login instead (trusted proxy)

If you already run a site with its own accounts, let it front the reader on a path of the same origin (e.g. `example.com/read/*`): a server-side proxy checks the site's session, then forwards the request with two headers — `X-Flowgt-User: <email>` and `X-Flowgt-Proxy: <shared secret>`. Set the same secret on the reader:

```bash
AC_PROXY_SECRET=<long random string>       # in .env; the reader now refuses every request without it, LAN included
```

Identity comes from the header, positions are stored per email, and the tunnel hostname is useless to anyone but the proxy. The reference implementation is `functions/read/[[path]].js` in the flowgt-website repo (Cloudflare Pages Function, ~60 lines). Both modes cannot be active at once; `AC_PROXY_SECRET` wins.

## Copy it to another machine

```bash
scripts/package.sh                          # → dist/audiobook-connector-portable/
scripts/package.sh /Volumes/Drive --only hp1-philosophers-stone,zero-to-one
```

The folder holds the package, the library, the audio, the dictionary, and a launcher for each platform — double-click `开始阅读.command` on macOS, `run.sh` on Linux, `run.bat` on Windows. **The other machine needs Python 3.10 and nothing else**: serving is pure standard library, so there is no install step and no network access required. The script sizes the audio before copying and stops rather than filling a disk, then checks that every audio symlink resolves and every `data.json` arrived.

## Backup & restore


```bash
scripts/backup.sh /path/to/backups      # library/ + cache/transcripts/, ~16 MB, verified
scripts/backup.sh /path/to/backups --with-books   # add books/, for a standalone archive
```

By default the archive holds `library/` (built books, covers, and `library/_progress/` — reading positions and saved passages, which exist nowhere else) and `cache/transcripts/` (hours of whisper compute, the one thing that is expensive to recreate). `books/` is left out because its files are usually hard links into a library you already keep; pass `--with-books` when the archive has to stand on its own. Model weights are always skipped — they re-download.

The script reads the archive back and checks two expected members before reporting success. An archive nobody opened is not a backup.

Restore on any Linux or Mac: clone, `tar xzf` inside the clone, `docker compose up -d`.

## How it works

| step | module | what happens |
|---|---|---|
| parse | `formats.py` `epub.py` | EPUB read directly (no lxml); MOBI/AZW3 unpacked to EPUB/HTML; PDF via `pypdf` layout mode with paragraph-reassembly heuristics |
| transcribe | `transcribe.py` | whisper with word timestamps — `mlx-whisper` on Apple Silicon, `faster-whisper` anywhere else; cached by file name+size+mtime |
| align | `align.py` | n-grams that occur exactly once in both book and transcript (n = 6→4→3→2, refined inside gaps) → longest monotonic chain → interpolation |
| serve | `server.py` | static HTTP with `Range` support (needed for `<audio>` seeking; `python -m http.server` lacks it) |
| read | `app/` | bookshelf + reader, single HTML files, no build step, no external requests |

Chapter titles come from the audio file names — `Chapter 03 - The Knight Bus.mp3` — matched fuzzily and in order against the headings found in the book. One audio file is one chapter, so the table of contents and the player never disagree, and a heading heuristic that also picks up letter signatures and newspaper headlines cannot pollute the chapter list. If fewer than 60 % of the titles match, none are used.

A paragraph is clickable only if it contains an exactly-anchored word; front matter, index and skipped captions show greyed out.

**The transcript never reaches the page.** Every word you read comes from the book file. whisper's output is a ruler used to find timings and is then discarded — a paragraph in `data.json` carries the book's own text plus `{file, start, end}`, nothing else. Anchors must be n-grams that occur exactly once on *both* sides, an ambiguous phrase is dropped rather than guessed, and a paragraph with no anchor gets no timestamp and renders greyed rather than a wrong one.

`scripts/verify-text.py` checks that from the other direction: for each aligned paragraph it measures how much of the book's token sequence actually appears in the audio over that span. On the eight books built here — 32291 paragraphs — the median coverage is 1.000 and 93.6 % reach 0.90.

Measured on *Zero to One* (4 h 45 min): 519 of 528 body paragraphs anchored exactly, the 9 misses all figure captions. PDF input on a synthetic 17-page fixture: 101 of 106 paragraphs recovered exactly.

## Reader

Chapter list, full-text search and saved passages share the left rail. Select any sentence and a bubble offers **收藏** (save it) or **朗读此处** (play from there); saved passages live in the browser and, for a signed-in reader, on the server too.

| key / control | action |
|---|---|
| tap a sentence | play from there |
| double-click a word | its concordance: every sentence it appears in, playable |
| select text | save the passage, look the word up, or play from it |
| `space` | play / pause |
| `←` `→` | −15 s / +15 s |
| `j` `k` | next / previous sentence |
| `r` | replay this sentence |
| `d` | practice mode |
| `m` | save the current paragraph |
| `w` | look up the selected word |
| `/` | search the whole book |
| `t` | chapter list |

**Built for practice.** The sentence being read is highlighted inside the paragraph being read, and practice mode repeats a sentence or a paragraph 1–5 times or forever, with an optional pause after each sentence — 1–3 seconds, or as long as the sentence itself took. That is the shadowing loop: hear it, pause, say it back. Speed goes down to 0.5x.

**Double-click any word** for its pronunciation, Chinese and English glosses, the exam lists it belongs to (中考/高考/CET-4/CET-6/考研/TOEFL/IELTS/GRE) and its frequency rank — then, underneath, every sentence in *this book* that uses it, each one playable. That last part is what no dictionary can give you, and it is what makes a word stick. Words you keep go to a vocabulary list that exports as TSV for Anki, gloss and example sentence included.

The dictionary is built for your library, not shipped whole. [ECDICT](https://github.com/skywind3000/ECDICT) (MIT) is 66 MB of CSV; `scripts/build-dict.py` keeps only the words your books actually use — 29 000 of them across thirteen books, 1 MB gzipped — and the reader fetches it on the first lookup, never before. Long-press still reaches your device's own dictionary: the reader does not hijack text selection.

```bash
mkdir -p cache/dict && curl -L -o cache/dict/ecdict.csv \
  https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv
scripts/build-dict.py                       # → library/_dict/dict.json
```

**Four themes**: follow-system, day, night, and **e-ink** — pure black on white, every transition and shadow removed, larger type. The page loads no webfont and no script from anywhere, so it opens on a Kindle experimental browser or a Boox tablet as it does on a laptop. Type size, line height, column width and paragraph indent are adjustable and remembered.

The bottom bar carries a scrubber, ±15 s, chapter skip, speed, and a **sleep timer** (15/30/60 min, or "end of this chapter"). On a phone the lock screen and headphone buttons control playback through the Media Session API. Reading position is remembered per device, and per account when signed in.

## CLI

```
audiobook-connector build <dir|name> [--title T] [--author A] [--slug S] [--language en]
                                     [--model large-v3-turbo] [--backend auto|mlx|faster]
                                     [--audio symlink|hardlink|copy]
audiobook-connector transcribe <dir|name>...        # fill the cache only, nothing else

scripts/import-book.py   <src-dir>...     one title: a folder with a book and its audio
scripts/import-series.py <audio> <books>  a multi-volume set, paired in order
scripts/cache-status.py  <book-dir>...    how much of each book is transcribed
scripts/build-ready.py   <book-dir>...    build every book whose audio is fully transcribed
scripts/verify-text.py   [<slug>...]      cross-check the displayed text against the audio
scripts/backup.sh        [dest] [--with-books]
audiobook-connector serve [--port 8765] [--host 0.0.0.0]
audiobook-connector list
```

Environment overrides: `AC_BOOKS` `AC_LIBRARY` `AC_CACHE` `AC_PORT` `AC_HOST` `AC_BACKEND`, plus `AC_ACCESS_TEAM` `AC_ACCESS_AUD` `AC_REQUIRE_AUTH` for Cloudflare Access or `AC_PROXY_SECRET` for a trusted proxy (this is how the Docker image is wired).

A book directory may carry a `book.json` — `title`, `author`, `series`, `volume`, `narrator`, `language`, `chapters` (titles, in order). `series` and `volume` group a set on the shelf; `chapters` overrides the titles otherwise taken from the audio file names.

`transcribe` exists because a long series is measured in hours: transcribe it in the background once, then `build` finishes in seconds off the cache. A seven-volume, 125-hour set runs about 10.5 h at ~12x real time with `mlx-whisper` on Apple Silicon.

Optional extras: `[cpu]` faster-whisper · `[mlx]` mlx-whisper · `[formats]` pypdf + mobi. The core is pure standard library.

## Limits

- Alignment needs the same edition on both sides; an abridged audiobook against an unabridged text aligns poorly. `build` warns below 20 %.
- PDF must have a text layer (no scans). Paragraph detection is heuristic; a running header that equals a chapter title is dropped with the header.
- MOBI/AZW3 support is implemented but has not yet been exercised on a real file.
- Whisper auto-detects the language; pass `--language zh` etc. to pin it. CJK is tokenized per character in the aligner, untested so far.
- Without Cloudflare Access there is no authentication — LAN mode is for a trusted home network only.
- `books/`, `library/`, `cache/`, `backups/` are git-ignored: your books stay yours.

## 中文速览

把一本书（epub / mobi / azw3 / pdf）和它的有声书音频放进 `books/<名字>/`，在服务器上：

```bash
docker compose run --rm cli build <名字>     # 转写 + 对齐
docker compose up -d                         # 局域网里任何设备打开 http://<服务器IP>:8765
```

Apple 芯片的 Mac 转写快 14 倍：本机 `pip install -e '.[mlx,formats]'` 后 `build`，再 `scripts/backup.sh` 打包，把 tgz 拷到服务器解开，`docker compose up -d` 即可，不会重新转写。同一个 tgz 就是灾备。

阅读器是**按 ESL 学习设计**的：正在朗读的**那一句**会在段落里高亮，点任意一句从那句开始播放；练习模式可以整句或整段复读 1–5 遍或一直重复，每句后可停 1–3 秒或与该句等长——听一句、停、自己说一遍，就是跟读循环，语速可以降到 0.5 倍。**双击任意单词**能看到它在全书出现过几次、每一处的原句，点一句就跳过去听；生词本可导出成 TSV 喂给 Anki。**双击任意单词**给出音标、中英释义、考纲标签（中考/高考/四六级/考研/托福/雅思/GRE）和词频，下面接着是这个词在**本书**里的每一处原句，点一句就跳过去听——这是词典给不了的部分。词典按你的书库生成：ECDICT（MIT）原始 66 MB，`scripts/build-dict.py` 只留你的书真正用到的词（十三本书 2.9 万个，gz 后 1 MB），阅读器在你第一次查词时才去取。长按依然能调系统词典，本页从不劫持文本选择。

**拷到另一台电脑**：`scripts/package.sh` 生成一个文件夹，里面有程序、书库、音频、词典和三个平台的启动脚本。对方只需要 Python 3.10，**不装任何依赖、不联网**——服务端是纯标准库。macOS 双击「开始阅读.command」即可。

点任意段落即从该处播放，当前段高亮并跟随滚动；选中一句话可以**收藏**或从这句开始朗读；左栏是目录、全文搜索、收藏三个页签。四种主题（跟随系统 / 日间 / 夜间 / **墨水屏**），字号、行距、栏宽、段首缩进都能调。底栏有进度条、±15 秒、上下章、语速和**睡眠定时**。整页不加载任何外部字体和脚本，所以 Kindle 实验浏览器和文石 Boox 上一样能开。

章节名取自音频文件名（`Chapter 03 - The Knight Bus.mp3`），按顺序模糊匹配到书里认出的标题上——一个音频文件就是一章，目录和播放器因此永远一致。

一整套书先跑 `transcribe` 把转写缓存填满（Apple 芯片约 12 倍速，125 小时音频约 10.5 小时），再 `build` 就只要几秒。

要开放到公网并用 Google 登录：域名托管在 Cloudflare，建 Tunnel + Access 应用（上面英文一节的 4 步），`.env` 填三个值，`docker compose --profile public up -d`。登录用户的阅读位置存在服务器上，换设备继续读。

## License

MIT

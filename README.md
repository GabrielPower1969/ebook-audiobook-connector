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

## Backup & restore

The same archive is the disaster-recovery plan. Three folders matter — `books/` (your files), `library/` (built output), `cache/transcripts/` (hours of whisper work); code lives in git.

```bash
scripts/backup.sh /path/to/backups      # model weights are skipped, they re-download
```

Restore on any Linux or Mac: clone, `tar xzf` inside the clone, `docker compose up -d`.

## How it works

| step | module | what happens |
|---|---|---|
| parse | `formats.py` `epub.py` | EPUB read directly (no lxml); MOBI/AZW3 unpacked to EPUB/HTML; PDF via `pypdf` layout mode with paragraph-reassembly heuristics |
| transcribe | `transcribe.py` | whisper with word timestamps — `mlx-whisper` on Apple Silicon, `faster-whisper` anywhere else; cached by file name+size+mtime |
| align | `align.py` | n-grams that occur exactly once in both book and transcript (n = 6→4→3→2, refined inside gaps) → longest monotonic chain → interpolation |
| serve | `server.py` | static HTTP with `Range` support (needed for `<audio>` seeking; `python -m http.server` lacks it) |
| read | `app/` | bookshelf + reader, single HTML files, no build step, no external requests |

A paragraph is clickable only if it contains an exactly-anchored word; front matter, index and skipped captions show greyed out.

Measured on *Zero to One* (4 h 45 min): 519 of 528 body paragraphs anchored exactly, the 9 misses all figure captions. PDF input on a synthetic 17-page fixture: 101 of 106 paragraphs recovered exactly.

## Reader

| key / control | action |
|---|---|
| tap a paragraph | play from there |
| `space` | play / pause |
| `←` `→` | −10 s / +10 s |
| `j` `k` | next / previous paragraph |
| ☰ (phones) | chapter list |

Speed and a *follow* (auto-scroll) toggle sit in the bottom bar. Progress is remembered per device.

## CLI

```
audiobook-connector build <dir|name> [--title T] [--slug S] [--language en] [--model large-v3-turbo]
                                     [--backend auto|mlx|faster] [--audio symlink|hardlink|copy]
audiobook-connector serve [--port 8765] [--host 0.0.0.0]
audiobook-connector list
```

Environment overrides: `AC_BOOKS` `AC_LIBRARY` `AC_CACHE` `AC_PORT` `AC_HOST` `AC_BACKEND`, plus `AC_ACCESS_TEAM` `AC_ACCESS_AUD` `AC_REQUIRE_AUTH` for Cloudflare Access (this is how the Docker image is wired).

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

阅读器里点任意段落即从该处播放，当前段高亮并跟随滚动；手机上 ☰ 打开目录。

要开放到公网并用 Google 登录：域名托管在 Cloudflare，建 Tunnel + Access 应用（上面英文一节的 4 步），`.env` 填三个值，`docker compose --profile public up -d`。登录用户的阅读位置存在服务器上，换设备继续读。

## License

MIT

#!/usr/bin/env bash
# Build a folder you can copy to another machine and run — no install, no network, no Python
# packages. Serving is pure standard library, so `python3 -m audiobook_connector serve` is the
# whole runtime; the launchers just call it.
#
#   scripts/package.sh [DEST] [--only slug,slug] [--no-audio] [--with-source] [--tar]
#
#   DEST           where to build, default ./dist
#   --only         package just these books (default: everything in library/)
#   --no-audio     library and dictionary only — the reader will have nothing to play
#   --with-source  also copy the epub/pdf, so the other machine can rebuild
#   --tar          also write a .tar next to the folder (no gzip: it is nearly all mp3)
#
# The result:
#   audiobook-connector-portable/
#     audiobook_connector/   the package (serving needs nothing else)
#     library/               data.json, covers, audio symlinks, _dict/
#     books/<slug>/          the audio those symlinks point at
#     开始阅读.command        macOS: double-click
#     run.sh  run.bat        Linux / Windows
#     README.txt
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$PWD"

DEST="dist"; ONLY=""; AUDIO=1; SOURCE=0; TAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --no-audio) AUDIO=0; shift ;;
    --with-source) SOURCE=1; shift ;;
    --tar) TAR=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) DEST="$1"; shift ;;
  esac
done

[ -f library/index.json ] || { echo "nothing built yet — run: audiobook-connector build <name>" >&2; exit 1; }

SLUGS=$(ls -1 library | grep -v '^_' | grep -v '\.json$')
if [ -n "$ONLY" ]; then
  WANT=$(printf '%s' "$ONLY" | tr ',' '\n')
  SLUGS=$(printf '%s\n' "$SLUGS" | grep -Fx -f <(printf '%s\n' "$WANT") || true)
  [ -n "$SLUGS" ] || { echo "--only matched no book. available:"; ls -1 library | grep -v '^_'; exit 1; }
fi

OUT="$DEST/audiobook-connector-portable"

# Audio is the whole weight of this thing, and a laptop that has been transcribing audiobooks is
# usually the one with no room left. Measure before copying rather than half-filling the disk.
NEED=0
for s in $SLUGS; do
  [ -d "books/$s" ] || continue
  [ "$AUDIO" = 1 ] && NEED=$((NEED + $(du -sk "books/$s" 2>/dev/null | cut -f1)))
done
NEED=$((NEED + $(du -sk library 2>/dev/null | cut -f1) + 4096))
mkdir -p "$DEST"
FREE=$(df -k "$DEST" | tail -1 | awk '{print $4}')
printf 'needs %s MB, %s MB free at %s\n' "$((NEED/1024))" "$((FREE/1024))" "$DEST"
if [ "$NEED" -gt "$((FREE - 1048576))" ]; then
  echo "not enough room (keeping 1 GB spare). Options:" >&2
  echo "  scripts/package.sh /Volumes/<external-drive>       # build straight onto another disk" >&2
  echo "  scripts/package.sh --only <slug>,<slug>            # just the books you want" >&2
  echo "  scripts/package.sh --no-audio                      # text and timings only" >&2
  exit 1
fi

rm -rf "$OUT"; mkdir -p "$OUT/library" "$OUT/books"
echo "packaging into $OUT"

# ---------------------------------------------------------------- code
cp -R audiobook_connector "$OUT/"
find "$OUT/audiobook_connector" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
cp README.md LICENSE "$OUT/" 2>/dev/null || true

# ---------------------------------------------------------------- data
[ -d library/_dict ] && cp -R library/_dict "$OUT/library/"
N=0
for s in $SLUGS; do
  [ -f "library/$s/data.json" ] || continue
  mkdir -p "$OUT/library/$s"
  # -R without -L keeps library/<slug>/audio/* as the relative symlinks they are; books/ is
  # copied alongside, so they resolve on the other machine exactly as they do here.
  cp -R "library/$s/." "$OUT/library/$s/"
  if [ "$AUDIO" = 1 ] && [ -d "books/$s" ]; then
    mkdir -p "$OUT/books/$s"
    # copy the audio the symlinks point at; -p keeps mtime, which is the transcript cache key
    find "books/$s" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.m4b' \
      -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.flac' -o -iname '*.wav' \) \
      -exec cp -p {} "$OUT/books/$s/" \;
    [ "$SOURCE" = 1 ] && find "books/$s" -maxdepth 1 -type f \
      \( -iname '*.epub' -o -iname '*.pdf' -o -iname '*.mobi' -o -iname '*.azw3' -o -iname 'book.json' \
         -o -iname 'cover.*' \) -exec cp -p {} "$OUT/books/$s/" \;
  fi
  N=$((N+1))
done
# index.json must list only what we packaged
python3 - "$SRC/library/index.json" "$OUT/library/index.json" "$SLUGS" <<'PYEND'
import json, sys
src, dst, slugs = sys.argv[1], sys.argv[2], set(sys.argv[3].split())
keep = [b for b in json.load(open(src)) if b["slug"] in slugs]
json.dump(keep, open(dst, "w"), ensure_ascii=False)
print(f"  index.json: {len(keep)} book(s)")
PYEND

# ---------------------------------------------------------------- launchers
cat > "$OUT/run.sh" <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3 is not installed. macOS: xcode-select --install"; exit 1; }
echo "Starting the reader…  (Ctrl-C to stop)"
exec python3 -m audiobook_connector serve
EOF
cp "$OUT/run.sh" "$OUT/开始阅读.command"
chmod +x "$OUT/run.sh" "$OUT/开始阅读.command"
cat > "$OUT/run.bat" <<'EOF'
@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m audiobook_connector serve) || (python -m audiobook_connector serve)
pause
EOF

cat > "$OUT/README.txt" <<EOF
audiobook-connector — 便携版 / portable

双击「开始阅读.command」(macOS)，或运行 run.sh (Linux) / run.bat (Windows)，
然后打开 http://localhost:8765 。同一个 Wi-Fi 下的手机、平板用终端里打印的 LAN 地址。

Double-click 开始阅读.command (macOS), or run run.sh (Linux) / run.bat (Windows),
then open http://localhost:8765 . Phones and tablets on the same Wi-Fi use the LAN
address the terminal prints.

只需要 Python 3.10 或更新版本，不装任何依赖 —— 阅读器本身是纯标准库。
Needs only Python 3.10+, with no packages installed: the reader is pure standard library.

不要把 books/ 和 library/ 分开放：library/<书>/audio/ 里是指向 books/ 的相对符号链接。
Keep books/ and library/ side by side: library/<book>/audio/ holds relative symlinks into books/.

书目 / books in this copy: $N
生成时间 / built: $(date '+%Y-%m-%d %H:%M')
EOF

# ---------------------------------------------------------------- verify
echo "verifying …"
BROKEN=$(find "$OUT/library" -type l ! -exec test -e {} \; -print | head -5)
[ -z "$BROKEN" ] || { echo "FAILED: broken audio links, e.g.:"; echo "$BROKEN"; exit 1; }
LINKS=$(find "$OUT/library" -type l | wc -l | tr -d ' ')
AUD=$(find "$OUT/books" -type f | wc -l | tr -d ' ')
python3 - "$OUT" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
idx = json.load(open(out / "library" / "index.json"))
assert idx, "index.json is empty"
for b in idx:
    assert (out / "library" / b["slug"] / "data.json").exists(), b["slug"]
print(f"  index lists {len(idx)} book(s), every data.json present")
PY
echo "  $LINKS audio link(s), $AUD audio file(s), all resolve"
echo "wrote $OUT ($(du -sh "$OUT" | cut -f1))"

if [ "$TAR" = 1 ]; then
  T="$DEST/audiobook-connector-portable-$(date +%Y%m%d).tar"
  ( cd "$DEST" && tar -cf "$(basename "$T")" audiobook-connector-portable )
  echo "wrote $T ($(du -h "$T" | cut -f1))   # not gzipped: it is nearly all mp3"
fi
echo
echo "copy that folder to the other machine and double-click 开始阅读.command"

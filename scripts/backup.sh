#!/usr/bin/env bash
# Back up what is expensive or irreplaceable, and verify the archive before claiming success.
#
#   scripts/backup.sh [dest-dir] [--with-books]
#
# Default set — small enough that you will actually run it:
#   cache/transcripts   hours of whisper compute; the one thing you cannot cheaply recreate
#   library/            built books, covers, audio links, and library/_progress (reading
#                       positions and saved passages — per-user data, irreplaceable)
#
# books/ is NOT included by default. Its files are hard links to your own ebook/audio library, so
# they already exist elsewhere on this machine and would multiply the archive by twenty. Pass
# --with-books when the archive has to stand alone — moving to another machine, or when the source
# library is not itself backed up.
#
# Model weights (cache/models) are always skipped: they re-download.
#
# Restore:
#   tar xzf audiobook-connector-<date>.tgz          # into the project directory
#   audiobook-connector serve                       # or: docker compose up -d
# With the default set the audio symlinks under library/<slug>/audio/ point at books/, so restore
# books/ too (from the tgz, or by re-running scripts/import-*.py against your ebook library).
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="backups"; WITH_BOOKS=0
for arg in "$@"; do
  case "$arg" in
    --with-books) WITH_BOOKS=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) DEST="$arg" ;;
  esac
done
mkdir -p "$DEST"

TARGETS=(library cache/transcripts)
[ "$WITH_BOOKS" = 1 ] && TARGETS+=(books)
for t in "${TARGETS[@]}"; do
  [ -e "$t" ] || { echo "nothing at ./$t — run a build first" >&2; exit 1; }
done

OUT="$DEST/audiobook-connector-$(date +%Y%m%d-%H%M).tgz"
echo "backing up: ${TARGETS[*]}"
tar czf "$OUT" --exclude='.DS_Store' "${TARGETS[@]}"

# A backup nobody opened is not a backup: read the archive back and count what came out.
echo "verifying …"
N=$(tar tzf "$OUT" | wc -l | tr -d ' ')
tar tzf "$OUT" | grep -q '^library/index.json$' || { echo "FAILED: library/index.json missing from $OUT" >&2; exit 1; }
tar tzf "$OUT" | grep -q '^cache/transcripts/' || { echo "FAILED: no transcripts in $OUT" >&2; exit 1; }
echo "wrote $OUT ($(du -h "$OUT" | cut -f1), $N entries, archive reads back clean)"
echo "keep a copy off this machine — an archive on the same disk survives a mistake, not a failure."

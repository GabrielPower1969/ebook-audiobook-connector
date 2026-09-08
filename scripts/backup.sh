#!/usr/bin/env bash
# Back up everything that is expensive or irreplaceable: your books, the built library,
# and the whisper transcripts (hours of compute). Model weights are skipped — they re-download.
#
#   scripts/backup.sh [dest-dir]        default dest: ./backups
#   restore:  tar xzf audiobook-connector-<date>.tgz && docker compose up -d
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:-backups}"; mkdir -p "$DEST"
OUT="$DEST/audiobook-connector-$(date +%Y%m%d-%H%M).tgz"
tar czf "$OUT" --exclude='cache/models' --exclude='.DS_Store' books library cache
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"

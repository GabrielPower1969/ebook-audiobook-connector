#!/usr/bin/env bash
# Transcribe the given book dirs, restarting if the process is killed.
#
# The transcript cache is keyed on file name+size+mtime, so a restart costs nothing: everything
# already done is skipped in seconds. That makes a supervisor loop the cheapest possible insurance
# against a laptop sleeping, a session ending, or an OOM in the middle of a ten-hour run.
#
#   scripts/transcribe-until-done.sh books/a books/b …
set -uo pipefail
cd "$(dirname "$0")/.."
[ $# -gt 0 ] || { echo "usage: $0 <book-dir>..." >&2; exit 2; }

done_yet() {
  .venv/bin/python scripts/cache-status.py "$@" |
    awk '{split($2,a,"/"); s+=a[1]; t+=$3} END{exit (s==t && t>0) ? 0 : 1}'
}

for attempt in $(seq 1 40); do
  if done_yet "$@"; then
    echo "=== all transcribed (after $((attempt-1)) restart(s))"
    exit 0
  fi
  echo "=== attempt $attempt  $(date '+%H:%M:%S')"
  caffeinate -i .venv/bin/audiobook-connector transcribe "$@" || echo "=== transcribe exited $?, retrying"
  sleep 5
done
echo "=== gave up after 40 attempts" >&2
exit 1

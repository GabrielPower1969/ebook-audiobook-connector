"""Build every book whose audio is fully transcribed, then wait and look again.

Made for a long series: transcription runs for hours in one process while this one turns each
volume into a readable book the moment its last chapter lands, so the shelf fills up as you go.

    scripts/build-ready.py books/hp*            # one pass, then exit when all are built
    scripts/build-ready.py --watch 300 books/*  # keep checking every 5 minutes
"""
from __future__ import annotations
import argparse, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audiobook_connector import transcribe                                   # noqa: E402

CACHE = ROOT / "cache" / "transcripts"


def ready(src: pathlib.Path) -> bool:
    files = transcribe.audio_files(src)
    return bool(files) and all((CACHE / f"{transcribe._cache_key(f)}.json").exists() for f in files)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="+")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="keep polling instead of exiting")
    a = ap.parse_args()
    todo = [pathlib.Path(s) for s in a.source if pathlib.Path(s).is_dir()]
    while todo:
        for src in list(todo):
            if not ready(src):
                continue
            print(f"[{time.strftime('%H:%M:%S')}] building {src.name}", flush=True)
            r = subprocess.run([str(ROOT / ".venv/bin/audiobook-connector"), "build", str(src)],
                               capture_output=True, text=True)
            line = next((l for l in r.stdout.splitlines() if l.startswith("aligned:")), r.stdout[-200:])
            print(f"  {line.strip()}" if r.returncode == 0 else f"  FAILED: {r.stderr[-400:]}", flush=True)
            todo.remove(src)
        if not todo or not a.watch:
            break
        time.sleep(a.watch)
    print(f"[{time.strftime('%H:%M:%S')}] " +
          ("all built" if not todo else "still waiting on: " + ", ".join(p.name for p in todo)), flush=True)


if __name__ == "__main__":
    main()

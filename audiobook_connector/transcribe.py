"""Word-level transcription with mlx-whisper, cached per audio file (by size+mtime)."""
from __future__ import annotations
import hashlib, json, pathlib, re, time

AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav"}
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

def natural_key(p: pathlib.Path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", p.name)]

def audio_files(src: pathlib.Path) -> list[pathlib.Path]:
    return sorted((p for p in src.iterdir() if p.suffix.lower() in AUDIO_EXT), key=natural_key)

def _cache_key(p: pathlib.Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]

def transcribe_all(src: pathlib.Path, cache: pathlib.Path, model=DEFAULT_MODEL, language=None, log=print) -> list[dict]:
    """Returns [{file, words:[{w,s,e}]}] in playback order."""
    cache.mkdir(parents=True, exist_ok=True); out = []
    for f in audio_files(src):
        cf = cache / f"{_cache_key(f)}.json"
        if cf.exists():
            log(f"  cached   {f.name}"); out.append(json.load(open(cf))); continue
        import mlx_whisper  # lazy: only needed when something is not cached
        t0 = time.time(); log(f"  whisper  {f.name} ...")
        r = mlx_whisper.transcribe(str(f), path_or_hf_repo=model, language=language,
                                   word_timestamps=True, condition_on_previous_text=False)
        words = [{"w": w["word"], "s": round(w["start"], 3), "e": round(w["end"], 3)}
                 for seg in r["segments"] for w in seg.get("words", [])]
        d = {"file": f.name, "language": r.get("language"), "words": words}
        json.dump(d, open(cf, "w"), ensure_ascii=False)
        log(f"  done     {f.name}: {len(words)} words in {time.time()-t0:.0f}s"); out.append(d)
    return out

"""Word-level transcription, cached per audio file.

Two interchangeable backends, picked automatically:
  mlx     — mlx-whisper, fast on Apple Silicon (host Macs)
  faster  — faster-whisper (CTranslate2), runs anywhere incl. Docker on CPU

Results are cached by file name + size + mtime, so a rebuild never re-transcribes
and a library built on a Mac needs no whisper at all to be served.
"""
from __future__ import annotations
import hashlib, json, os, pathlib, platform, re, time

AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav", ".wma"}
DEFAULT_MODEL = "large-v3-turbo"          # backend-neutral name, resolved per backend
MODEL_REPO = {"mlx": "mlx-community/whisper-{m}", "faster": "{m}"}


def natural_key(p: pathlib.Path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", p.name)]


def audio_files(src: pathlib.Path) -> list[pathlib.Path]:
    return sorted((p for p in src.iterdir() if p.suffix.lower() in AUDIO_EXT), key=natural_key)


def _cache_key(p: pathlib.Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _importable(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def pick_backend(requested: str | None = None) -> str:
    """'auto' → mlx on Apple Silicon, else faster-whisper."""
    if requested and requested != "auto":
        return requested
    if platform.system() == "Darwin" and platform.machine() == "arm64" and _importable("mlx_whisper"):
        return "mlx"
    if _importable("faster_whisper"):
        return "faster"
    raise SystemExit(
        "No transcription backend installed.\n"
        "  Apple Silicon:  pip install 'audiobook-connector[mlx]'\n"
        "  anywhere else:  pip install 'audiobook-connector[cpu]'\n"
        "  or just use Docker:  docker compose run --rm build books/<dir>"
    )


class _Backend:
    """Loads the model once, transcribes many files."""

    def __init__(self, kind: str, model: str, models_dir: pathlib.Path | None):
        self.kind, self.model = kind, MODEL_REPO[kind].format(m=model)
        self.models_dir, self._m = models_dir, None

    def words(self, path: pathlib.Path, language: str | None) -> tuple[list[dict], str | None]:
        if self.kind == "mlx":
            import mlx_whisper
            r = mlx_whisper.transcribe(str(path), path_or_hf_repo=self.model, language=language,
                                       word_timestamps=True, condition_on_previous_text=False)
            words = [{"w": w["word"], "s": round(w["start"], 3), "e": round(w["end"], 3)}
                     for seg in r["segments"] for w in seg.get("words", [])]
            return words, r.get("language")
        from faster_whisper import WhisperModel
        if self._m is None:
            self._m = WhisperModel(self.model, device="auto", compute_type="int8",
                                   download_root=str(self.models_dir) if self.models_dir else None)
        segs, info = self._m.transcribe(str(path), language=language, word_timestamps=True,
                                        condition_on_previous_text=False, vad_filter=False)
        words = [{"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3)}
                 for seg in segs for w in (seg.words or [])]
        return words, info.language


def transcribe_all(src: pathlib.Path, cache: pathlib.Path, model: str = DEFAULT_MODEL,
                   language: str | None = None, backend: str | None = None,
                   models_dir: pathlib.Path | None = None, log=None) -> list[dict]:
    """Returns [{file, language, words:[{w,s,e}]}] in playback order."""
    log = log or (lambda m: print(m, flush=True))
    cache.mkdir(parents=True, exist_ok=True)
    files = audio_files(src)
    todo = [f for f in files if not (cache / f"{_cache_key(f)}.json").exists()]
    be = None
    if todo:
        kind = pick_backend(backend)
        log(f"  backend: {kind} ({MODEL_REPO[kind].format(m=model)}), {len(todo)} file(s) to transcribe")
        be = _Backend(kind, model, models_dir)
    out = []
    for f in files:
        cf = cache / f"{_cache_key(f)}.json"
        if cf.exists():
            log(f"  cached   {f.name}")
            out.append(json.load(open(cf)))
            continue
        t0 = time.time()
        log(f"  whisper  {f.name} ...")
        words, lang = be.words(f, language)
        d = {"file": f.name, "language": lang, "words": words}
        json.dump(d, open(cf, "w"), ensure_ascii=False)
        log(f"  done     {f.name}: {len(words)} words in {time.time() - t0:.0f}s")
        out.append(d)
    return out

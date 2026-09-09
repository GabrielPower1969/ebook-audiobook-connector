"""How much of a book dir is already transcribed. Cheap: stats only, no transcript is read."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from audiobook_connector import transcribe
cache = pathlib.Path("cache/transcripts")
for d in sys.argv[1:]:
    src = pathlib.Path(d)
    files = transcribe.audio_files(src)
    have = [f for f in files if (cache / f"{transcribe._cache_key(f)}.json").exists()]
    print(f"{src.name:30} {len(have):3}/{len(files):3}")

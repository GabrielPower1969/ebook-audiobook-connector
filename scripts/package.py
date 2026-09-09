#!/usr/bin/env python3
"""Build a folder — or a zip — that plays anywhere: unzip, double-click index.html, read.

    scripts/package.py [--dest dist] [--only slug,slug] [--zip] [--no-audio] [--name NAME]

Two things make this work without a server:

  * `fetch()` is blocked on `file://` by every browser, but a `<script>` tag is not. So each
    data file is written twice — `data.json` for the served mode and `data.js` (the same content
    assigned to a global) for the double-click mode. The pages pick by `location.protocol`.
  * The library's audio links are relative symlinks into `books/`. A zip cannot carry those onto
    Windows, so here the audio is copied in for real and `books/` disappears.

The result also still runs as a server (`局域网模式` launchers) for phones and tablets on the
same Wi-Fi, and for reading position shared between devices. Serving is pure standard library, so
the other machine needs Python 3.10 and nothing else.
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav"}
TEXTY = {".html", ".json", ".js", ".svg", ".ico", ".txt", ".py", ".md", ".command", ".sh", ".bat"}

RUN_SH = """#!/usr/bin/env bash
# 让同一个 Wi-Fi 下的手机 / 平板也能读，并在设备之间同步阅读进度。
# Serve the library so phones and tablets on the same Wi-Fi can read it too.
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "需要 Python 3 / Python 3 required. macOS: xcode-select --install"; read -r _; exit 1; }
python3 -m audiobook_connector serve
"""

RUN_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m audiobook_connector serve) || (python -m audiobook_connector serve)
pause
"""


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def write_pair(dst_json: pathlib.Path, data, global_name: str):
    """Write data.json (served mode) and data.js (double-click mode) side by side."""
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    dst_json.write_text(body, encoding="utf-8")
    dst_json.with_suffix(".js").write_text(f"window.{global_name}={body};\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default="dist")
    ap.add_argument("--only", help="comma-separated slugs; default every built book")
    ap.add_argument("--name", default="FlowGT 书房", help="the folder the reader ends up with")
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--no-audio", action="store_true", help="text and timings only — nothing to play")
    a = ap.parse_args()

    lib = ROOT / "library"
    idx_file = lib / "index.json"
    if not idx_file.exists():
        sys.exit("nothing built yet — run: audiobook-connector build <name>")
    index = json.load(open(idx_file))
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        index = [b for b in index if b["slug"] in want]
        if not index:
            sys.exit("--only matched no book. available: " + ", ".join(b["slug"] for b in json.load(open(idx_file))))

    # ---- size check first: this laptop is usually the one with no room left
    need = sum(f.stat().st_size for b in index
               for f in (lib / b["slug"]).rglob("*") if f.is_file() or f.is_symlink())
    if a.no_audio:
        need = sum(f.stat().st_size for b in index
                   for f in (lib / b["slug"]).rglob("*")
                   if f.is_file() and f.suffix.lower() not in AUDIO_EXT)
    need += sum(f.stat().st_size for f in (lib / "_dict").glob("*")) if (lib / "_dict").exists() else 0
    need = int(need * (2.1 if a.zip else 1.05)) + (8 << 20)
    free = shutil.disk_usage(pathlib.Path(a.dest).parent if not pathlib.Path(a.dest).exists()
                             else a.dest).free
    print(f"needs about {human(need)}, {human(free)} free")
    if need > free - (1 << 30):
        sys.exit("not enough room (keeping 1 GB spare). Try --dest /Volumes/<drive>, "
                 "--only <slug>, or --no-audio")

    dest = pathlib.Path(a.dest) / a.name
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "library" / "_dict").mkdir(parents=True)
    print(f"building {dest}")

    # ---- the two pages and their assets, at the top where a person will find them
    app = ROOT / "audiobook_connector" / "app"
    for f in app.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    # ---- the package too, so the server mode works from the same folder
    shutil.copytree(ROOT / "audiobook_connector", dest / "audiobook_connector",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("LICENSE", "README.md"):
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, dest / f)

    # ---- data
    write_pair(dest / "library" / "index.json", index, "__AC_INDEX")
    if (lib / "_dict" / "dict.json").exists():
        write_pair(dest / "library" / "_dict" / "dict.json",
                   json.load(open(lib / "_dict" / "dict.json")), "__AC_DICT")
    n_audio = 0
    for b in index:
        slug = b["slug"]
        out = dest / "library" / slug
        out.mkdir(parents=True, exist_ok=True)
        write_pair(out / "data.json", json.load(open(lib / slug / "data.json")), "__AC_BOOK")
        for c in (lib / slug).glob("cover.*"):
            shutil.copy2(c, out / c.name)
        if a.no_audio:
            continue
        adir = lib / slug / "audio"
        if not adir.is_dir():
            continue
        (out / "audio").mkdir(exist_ok=True)
        for f in sorted(adir.iterdir()):
            real = f.resolve()                       # follow the relative symlink into books/
            if not real.is_file():
                sys.exit(f"missing audio: {f} → {real}")
            shutil.copy2(real, out / "audio" / f.name)
            n_audio += 1
        print(f"  {slug}: {len(list((out / 'audio').iterdir()))} audio")

    # ---- launchers for the optional server mode
    for name in ("局域网模式（手机也能读）.command", "局域网模式.sh"):
        p = dest / name
        p.write_text(RUN_SH, encoding="utf-8")
        p.chmod(0o755)
    (dest / "局域网模式.bat").write_text(RUN_BAT, encoding="utf-8")
    (dest / "使用说明.txt").write_text(f"""FlowGT 书房 — 听读同步阅读器

怎么开始
  双击 index.html —— 就这样，用你的浏览器打开，不用装任何东西。
  推荐 Chrome / Edge / Safari。

怎么用
  · 点书里任意一句，就从那一句开始朗读，正在读的那句会高亮。
  · 底部「练」是练习模式：整句或整段复读、每句后停几秒给你跟读。
  · 双击任意单词：音标、中文释义、考纲标签，还有它在这本书里的每一处原句。
  · 左栏四个页签：目录、全文搜索、收藏、生词本。生词本可以导出成 Anki 用的 TSV。
  · 右下角齿轮：字号、行距、栏宽，以及日间 / 夜间 / 墨水屏四种主题。

想在手机、平板上读？
  双击「局域网模式（手机也能读）.command」（macOS）
  或运行 局域网模式.sh（Linux）/ 局域网模式.bat（Windows），
  终端会打印一个 http://192.168.x.x:8765 的地址，手机连同一个 Wi-Fi 打开它。
  这个模式还会把阅读进度存在电脑上，换设备接着读。
  只需要 Python 3.10 或更新版本，不装任何依赖。

阅读进度和生词本
  双击打开时存在浏览器本地，换浏览器不共享。局域网模式下存在电脑上。

一共 {len(index)} 本书，{n_audio} 个音频文件。
词典数据来自 ECDICT（MIT 许可）https://github.com/skywind3000/ECDICT
""", encoding="utf-8")

    # ---- verify
    bad = [b["slug"] for b in index if not (dest / "library" / b["slug"] / "data.js").exists()]
    if bad:
        sys.exit(f"missing data.js for: {bad}")
    if not a.no_audio:
        empty = [b["slug"] for b in index
                 if not any((dest / "library" / b["slug"] / "audio").glob("*"))]
        if empty:
            sys.exit(f"no audio copied for: {empty}")
    links = [p for p in dest.rglob("*") if p.is_symlink()]
    if links:
        sys.exit(f"symlinks left in the package (a zip cannot carry these): {links[:3]}")
    total = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"wrote {dest}  ({human(total)}, {len(index)} book(s), {n_audio} audio file(s), no symlinks)")

    if a.zip:
        z = pathlib.Path(a.dest) / (a.name.replace(" ", "-") + ".zip")
        print(f"zipping → {z}   (audio is stored, not compressed)")
        with zipfile.ZipFile(z, "w", allowZip64=True) as zf:
            for f in sorted(dest.rglob("*")):
                if not f.is_file():
                    continue
                arc = pathlib.Path(a.name) / f.relative_to(dest)
                comp = zipfile.ZIP_DEFLATED if f.suffix.lower() in TEXTY else zipfile.ZIP_STORED
                zi = zipfile.ZipInfo.from_file(f, str(arc))
                zi.compress_type = comp
                if f.suffix in (".command", ".sh"):
                    zi.external_attr = (0o100755 << 16)          # keep them double-clickable
                with open(f, "rb") as src, zf.open(zi, "w") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        print(f"wrote {z}  ({human(z.stat().st_size)})")
    print("\n解压后双击 index.html 即可。")


if __name__ == "__main__":
    main()

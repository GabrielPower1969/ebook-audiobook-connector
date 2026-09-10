#!/usr/bin/env python3
"""Pack the whole project for a move to another Mac — data only; the code comes from git.

    scripts/handoff.py [--dest DIR] [--tar] [--with-dict-source]

What travels, and why:

    books/              the audio, and the epub/pdf beside it. 2.4 GB, and unavoidable: the
                        library's audio links point here.
    library/            built books, covers, the dictionary, and _progress/ — the reading
                        positions and saved words, which exist nowhere else.
    cache/transcripts/  hours of whisper compute. Losing this costs a day, not a download.
    NOTES.local.md      the machine notes that are deliberately not in git.

What does not: the code (`git clone`, so the new machine tracks the repo), `.venv` (rebuilt in
place — a virtualenv hard-codes its own path), `cache/models` and `cache/dict/ecdict.csv` (both
re-downloadable), logs and pid files.

The archive carries a `restore.command` that does the whole other side: clone, put the data back,
build the venv, verify the alignment is bit-identical, and offer to install the always-on service.
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, subprocess, sys, tarfile, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".DS_Store"}


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def tree_size(p: pathlib.Path) -> int:
    """Bytes actually on disk. lstat, not stat: library/<slug>/audio/* are symlinks into books/,
    and following them would count the same 2.4 GB of audio twice."""
    if not p.exists():
        return 0
    return sum(f.lstat().st_size for f in p.rglob("*") if not f.is_dir())


def copy_tree(src: pathlib.Path, dst: pathlib.Path, follow_links: bool = False) -> int:
    """Copy, keeping mtimes (the transcript cache key) and keeping symlinks as symlinks."""
    n = 0
    for s in sorted(src.rglob("*")):
        if any(part in SKIP_DIRS for part in s.parts):
            continue
        rel = s.relative_to(src)
        d = dst / rel
        if s.is_symlink() and not follow_links:
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.exists() or d.is_symlink():
                d.unlink()
            os.symlink(os.readlink(s), d)
            n += 1
        elif s.is_dir():
            d.mkdir(parents=True, exist_ok=True)
        elif s.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            n += 1
    return n


RESTORE = r'''#!/usr/bin/env bash
# 在新 Mac 上跑这一个脚本就行 / Run this one script on the new Mac.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
HERE="$PWD"
REPO="https://github.com/GabrielPower1969/ebook-audiobook-connector.git"
DEFAULT="$HOME/audiobook-connector"

echo "📦 audiobook-connector — 迁移到这台机器 / restore onto this machine"
echo

read -r -p "装到哪里 / install where? [$DEFAULT] " DEST
DEST="${DEST:-$DEFAULT}"
DEST="${DEST/#\~/$HOME}"

case "$DEST/" in
  "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*)
    echo
    echo "⚠️  macOS 不让 launchd 起的进程读这个目录，放这里就做不成开机自启。"
    echo "   macOS blocks launchd from reading that folder, so the always-on service"
    echo "   cannot be installed there. 建议 / suggested: $DEFAULT"
    read -r -p "   仍然装在这里？/ continue anyway? [y/N] " yn
    [ "$yn" = "y" ] || [ "$yn" = "Y" ] || exit 1 ;;
esac

command -v git >/dev/null || { echo "❌ 需要 git / git required: xcode-select --install"; exit 1; }
PY3="$(command -v python3 || true)"
[ -n "$PY3" ] || { echo "❌ 需要 Python 3.10+ / Python 3.10+ required"; exit 1; }

# 1. 代码 / code
if [ -d "$DEST/.git" ]; then
  echo "→ 已有仓库，拉取最新 / repo exists, pulling"
  git -C "$DEST" pull --ff-only || true
else
  echo "→ 克隆代码 / cloning $REPO"
  git clone --depth 1 "$REPO" "$DEST" || { echo "❌ 克隆失败 / clone failed"; exit 1; }
fi

# 2. 数据 / data
echo "→ 放回数据 / restoring data (this is the slow part)"
mkdir -p "$DEST/cache"
for d in books library cache/transcripts; do
  [ -d "$HERE/$d" ] || continue
  mkdir -p "$DEST/$d"
  # -a keeps mtimes, which is exactly the transcript cache key, and keeps symlinks as symlinks
  rsync -a --delete "$HERE/$d/" "$DEST/$d/" 2>/dev/null || cp -Rp "$HERE/$d/." "$DEST/$d/"
  echo "   $d"
done
[ -f "$HERE/NOTES.local.md" ] && cp -p "$HERE/NOTES.local.md" "$DEST/"

# 3. 环境 / python environment
cd "$DEST" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "→ 建虚拟环境 / creating .venv"
  "$PY3" -m venv .venv || { echo "❌ venv 建不起来"; exit 1; }
fi
echo "→ 安装依赖 / installing (mlx on Apple Silicon, cpu elsewhere)"
if [ "$(uname -m)" = "arm64" ]; then EXTRA='.[mlx,formats]'; else EXTRA='.[cpu,formats]'; fi
.venv/bin/pip install -q --upgrade pip >/dev/null 2>&1
.venv/bin/pip install -q -e "$EXTRA" || { echo "⚠️  可选依赖装失败，纯阅读不受影响 / optional extras failed; serving still works"; }

# 4. 验收 / verify
echo "→ 验收 / verifying"
BROKEN=$(find library -type l ! -exec test -e {} \; -print 2>/dev/null | head -3)
if [ -n "$BROKEN" ]; then
  echo "❌ 音频链接断了 / broken audio links:"; echo "$BROKEN"; exit 1
fi
N=$(.venv/bin/python -c "import json;print(len(json.load(open('library/index.json'))))" 2>/dev/null || echo 0)
A=$(find library -type l | wc -l | tr -d ' ')
T=$(ls cache/transcripts 2>/dev/null | wc -l | tr -d ' ')
echo "   $N 本书 / books · $A 个音频链接全部可解析 / audio links, all resolve · $T 份转写缓存 / transcripts"
if [ -d books/zero-to-one ]; then
  R=$(.venv/bin/audiobook-connector build zero-to-one 2>/dev/null | grep -oE '[0-9]+/[0-9]+ paragraphs' | head -1)
  echo "   回归 / regression: $R  （应为 628/1256 / expected 628/1256）"
fi

# 5. 常开服务 / always-on
echo
read -r -p "装成开机自启的服务吗？/ install the always-on service? [Y/n] " yn
if [ "$yn" != "n" ] && [ "$yn" != "N" ]; then
  scripts/install-service.command || echo "   （可以稍后再跑 scripts/install-service.command）"
else
  echo "   手动启动 / start by hand: scripts/start.command"
fi

echo
echo "✅ 完成 / done:  $DEST"
echo "   下一步（方案 A，公网访问）/ next, for access from anywhere:"
echo "     1. Cloudflare Zero Trust 建 Tunnel，公开主机名指向 http://localhost:8765"
echo "     2. cp .env.example .env 并填 TUNNEL_TOKEN"
echo "     3. docker compose --profile public up -d"
'''

README = """audiobook-connector — 迁移包 / handoff archive
生成于 / built {when}  来自 / from {host}

一句话：在新 Mac 上双击 restore.command，按提示走完即可。
One line: on the new Mac, double-click restore.command and follow it.

它会做这些事 / it will:
  1. 从 GitHub 克隆代码（所以新机器之后能直接 git pull 拿更新）
  2. 把这个包里的 books/ library/ cache/transcripts/ 放回去
  3. 建 .venv 并装依赖（Apple 芯片装 mlx，其他装 cpu）
  4. 验收：音频链接是否都解析、回归对齐是否仍是 628/1256
  5. 问你要不要装成开机自启的服务

⚠️ 位置很重要 / where you put it matters
   不要放在 ~/Documents、~/Desktop、~/Downloads —— macOS 不让 launchd 起的进程读这些目录，
   放进去就永远做不成开机自启（实测：LaunchAgent 报 Operation not permitted / EX_CONFIG 78）。
   推荐 ~/audiobook-connector。
   Do not put it under ~/Documents, ~/Desktop or ~/Downloads: macOS blocks launchd-spawned
   processes from reading those, so the always-on service cannot work there.

包里有什么 / what is inside
{contents}

没带的东西（新机器上会自己生成或重新下载）/ deliberately not included:
  代码本身      从 GitHub 克隆 / cloned from git
  .venv         虚拟环境写死了自己的路径，必须在新机器上重建
  cache/models  whisper 权重，第一次转写时自动下载（约 1.5 GB）
  cache/dict/   词典源文件 66 MB，需要时重新下载：
                curl -L -o cache/dict/ecdict.csv \\
                  https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv

阅读进度 / reading positions
  library/_progress/ 里是每台设备各自的阅读位置和生词本，**这份数据别处没有**。
  搬过去之后，原来那些设备用同一个 cookie 连上新机器就还认得。
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default="dist")
    ap.add_argument("--tar", action="store_true", help="also write a single .tar (mp3 does not compress)")
    ap.add_argument("--with-dict-source", action="store_true", help="include the 66 MB ECDICT csv")
    a = ap.parse_args()

    parts = [("books", ROOT / "books"), ("library", ROOT / "library"),
             ("cache/transcripts", ROOT / "cache" / "transcripts")]
    if a.with_dict_source and (ROOT / "cache" / "dict").exists():
        parts.append(("cache/dict", ROOT / "cache" / "dict"))
    if not (ROOT / "library" / "index.json").exists():
        sys.exit("nothing built yet — run: audiobook-connector build <name>")

    need = sum(tree_size(p) for _, p in parts)
    dest_root = pathlib.Path(a.dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest_root).free
    want = int(need * (2.05 if a.tar else 1.05))
    print(f"needs about {human(want)}, {human(free)} free at {dest_root}")
    if want > free - (1 << 30):
        sys.exit("not enough room (keeping 1 GB spare). Try --dest /Volumes/<drive>, or plug a disk in.")

    out = dest_root / f"audiobook-connector-handoff-{time.strftime('%Y%m%d')}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    print(f"packing into {out}")

    lines = []
    for name, src in parts:
        if not src.exists():
            continue
        n = copy_tree(src, out / name)
        sz = tree_size(out / name)
        print(f"  {name:20} {n:5} files  {human(sz)}")
        lines.append(f"  {name+'/':22}{human(sz):>10}   {n} 个文件")
    if (ROOT / "NOTES.local.md").exists():
        shutil.copy2(ROOT / "NOTES.local.md", out / "NOTES.local.md")
        lines.append("  NOTES.local.md            本机笔记（不在 git 里）")

    (out / "restore.command").write_text(RESTORE, encoding="utf-8")
    (out / "restore.command").chmod(0o755)
    host = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True,
                          text=True).stdout.strip() or os.uname().nodename
    (out / "先读我 READ-ME-FIRST.txt").write_text(
        README.format(when=time.strftime("%Y-%m-%d %H:%M"), host=host, contents="\n".join(lines)),
        encoding="utf-8")

    # ---- verify what we just wrote, before anyone carries it anywhere
    print("verifying …")
    broken = [str(p) for p in (out / "library").rglob("*") if p.is_symlink() and not p.exists()]
    if broken:
        sys.exit(f"FAILED: {len(broken)} broken audio link(s), e.g. {broken[:2]}")
    idx = json.load(open(out / "library" / "index.json"))
    for b in idx:
        if not (out / "library" / b["slug"] / "data.json").exists():
            sys.exit(f"FAILED: {b['slug']}/data.json missing")
    links = sum(1 for p in (out / "library").rglob("*") if p.is_symlink())
    trs = len(list((out / "cache" / "transcripts").glob("*.json"))) if (out / "cache" / "transcripts").exists() else 0
    prog = len(list((out / "library" / "_progress").glob("*.json"))) if (out / "library" / "_progress").exists() else 0
    print(f"  {len(idx)} book(s), {links} audio link(s) all resolve, {trs} transcript(s), "
          f"{prog} device profile(s)")
    print(f"wrote {out}  ({human(tree_size(out))})")

    if a.tar:
        t = dest_root / (out.name + ".tar")
        print(f"tarring → {t}  (no gzip: it is nearly all mp3)")
        with tarfile.open(t, "w") as tf:
            tf.add(out, arcname=out.name)
        print(f"wrote {t}  ({human(t.stat().st_size)})")

    print("\n把这个文件夹拷到新 Mac，双击里面的 restore.command。")
    print("Copy that folder to the new Mac and double-click restore.command inside it.")


if __name__ == "__main__":
    main()

# Audiobook Connector — 边看边听的读书器

把任意 EPUB 和它的有声书按段落对齐：点哪一段就从哪一段开始播，播放时当前段高亮并跟随滚动。
多本书放进一个书架，阅读进度自动记忆。转写全程在本机跑，音频不上传。

## 安装

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

## 用法

```bash
# 1. 每本书一个目录：一个 .epub + 若干音频（mp3/m4a/m4b/…，按文件名自然排序作为播放顺序），可选 cover.jpg
ls source/zero-to-one
#   Zero to One.epub  Zero to One-Part01.mp3 ... Part04.mp3  cover.jpg

# 2. 构建（首次转写约 5 分钟 / 小时音频，之后走缓存）
.venv/bin/python -m audiobook_connector build source/zero-to-one --slug zero-to-one

# 3. 起服务，打开 http://localhost:8765
.venv/bin/python -m audiobook_connector serve
```

`build` 可加 `--title`、`--language zh`、`--model <hf repo>`、`--copy-audio`（默认软链）。`list` 列出书架。

## 目录

```
audiobook_connector/
  epub.py        .epub → 有序段落（纯标准库，EPUB2/3，div 排版兜底）
  transcribe.py  mlx-whisper 词级时间戳，按文件 size+mtime 缓存到 cache/transcripts/
  align.py       唯一 n-gram 锚点(6→4→3→2) + 最长递增子序列 + 段内插值；支持 CJK 逐字
  server.py      支持 HTTP Range 的静态服务（音频拖动定位必需）
  app/           index.html 书架，reader.html 阅读器
library/<slug>/  构建产物：data.json + audio/ 软链 + 封面
```

## 阅读器

空格 播放/暂停 · ←/→ 后退/快进 10 秒 · j/k 上一段/下一段 · 可调速、可关跟随滚动。
音频里没读的内容（版权页、目录、索引、图注）显示为灰色，不可点击。

## 对齐质量（Zero to One 实测）

正文 528 段中 519 段精确锚定，未对齐的 9 段全是图注和图表标签。

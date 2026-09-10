#!/usr/bin/env bash
# 一键启动书房 / Start the reader on this machine and on the LAN.
# 双击即可（macOS）。也可以拖到 Dock 上。 Double-click on macOS, or drag it to the Dock.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PORT="${AC_PORT:-8765}"
PIDFILE=".serve.pid"
LOG="cache/serve.log"

urls() {
  local host ip
  host="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null)"
  echo ""
  echo "  📖  本机 / this Mac    http://localhost:$PORT"
  [ -n "$host" ] && echo "  📱  手机平板 / phone   http://$host.local:$PORT      ← 换 Wi-Fi 也不变，推荐"
  for ip in $(ipconfig getifaddr en0 2>/dev/null) $(ipconfig getifaddr en1 2>/dev/null) \
            $(hostname -I 2>/dev/null); do
    echo "  🌐  局域网 / LAN       http://$ip:$PORT"
  done
  echo ""
  echo "  每台设备各自记住每本书读到哪 —— 第一次打开就会拿到一个设备标识，不用登录。"
  echo "  Each device keeps its own place in every book. No sign-in: it is given an id on"
  echo "  first visit and keeps it."
  echo ""
  echo "  停止服务：双击 scripts/stop.command   /   Stop: double-click scripts/stop.command"
}

# 已经在跑就别重复起 / already listening: just show where it is
if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "✅ 书房已经在运行 / already running on port $PORT"
  urls
  exit 0
fi

PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "❌ 需要 Python 3 / Python 3 required. macOS: xcode-select --install"; exit 1; }
[ -f library/index.json ] || { echo "❌ 书架是空的 / nothing built yet: audiobook-connector build <name>"; exit 1; }

mkdir -p cache
echo "🚀 正在启动 / starting …"
nohup "$PY" -m audiobook_connector serve --port "$PORT" >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"

for _ in $(seq 1 40); do
  lsof -ti "tcp:$PORT" >/dev/null 2>&1 && break
  sleep 0.25
done

if ! lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "❌ 没起来 / failed to start. 最后几行日志 / last lines of $LOG:"
  tail -12 "$LOG"
  rm -f "$PIDFILE"
  exit 1
fi

BOOKS=$("$PY" -c "import json;print(len(json.load(open('library/index.json'))))" 2>/dev/null || echo "?")
echo "✅ 书房已启动，$BOOKS 本书 / running, $BOOKS book(s)"
urls
command -v open >/dev/null && open "http://localhost:$PORT" >/dev/null 2>&1

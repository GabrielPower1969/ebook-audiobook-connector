#!/usr/bin/env bash
# 装成开机自启的服务 / Install as a LaunchAgent so the reader is always up.
#
#   scripts/install-service.command            装上并立即启动 / install and start
#   scripts/install-service.command --remove   卸掉 / remove
#
# 为什么要挑位置：macOS 不让 launchd 拉起的进程读 ~/Documents、~/Desktop、~/Downloads
# （TCC 保护）。实验证据：一个只跑 `ls` 的 LaunchAgent 在那里会打印 Operation not permitted，
# 服务本身则以 EX_CONFIG 78 退出。所以本脚本先检查位置，不合适就直接拒绝并告诉你该放哪。
#
# macOS refuses launchd-spawned processes access to ~/Documents, ~/Desktop and ~/Downloads.
# A LaunchAgent there dies with EX_CONFIG 78, so this script checks the location first.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
DIR="$PWD"
LABEL="co.flowgt.audiobook-reader"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${AC_PORT:-8765}"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
  rm -f "$PLIST"
  echo "✅ 已卸载开机自启 / service removed. 书和进度都还在 / your library is untouched."
  exit 0
fi

case "$DIR/" in
  "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*|"$HOME"/Movies/*|"$HOME"/Pictures/*)
    echo "❌ 这个位置装不了开机自启 / cannot install from here:"
    echo "     $DIR"
    echo
    echo "   macOS 不允许 launchd 起的进程读这些目录（TCC 保护）。"
    echo "   macOS blocks launchd-spawned processes from reading this folder."
    echo
    echo "   把整个项目移到不受保护的位置再运行本脚本，例如："
    echo "     mv \"$DIR\" ~/audiobook-connector"
    echo "     cd ~/audiobook-connector && scripts/install-service.command"
    echo
    echo "   （现在仍然可以用 scripts/start.command 手动启动，只是重启后要再点一次。）"
    exit 1 ;;
esac

PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "❌ 需要 Python 3 / Python 3 required"; exit 1; }
[ -f "$DIR/library/index.json" ] || { echo "❌ 书架是空的 / nothing built yet"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/cache"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>-m</string><string>audiobook_connector</string>
    <string>serve</string><string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/cache/serve.log</string>
  <key>StandardErrorPath</key><string>$DIR/cache/serve.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
"$DIR/scripts/stop.command" >/dev/null 2>&1
if ! launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null; then
  echo "❌ launchctl bootstrap 失败 / failed. 日志 / log: $DIR/cache/serve.log"
  exit 1
fi

for _ in $(seq 1 40); do
  lsof -ti "tcp:$PORT" >/dev/null 2>&1 && break
  sleep 0.25
done

if ! lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "❌ 装上了但没起来 / installed but not listening. 最后几行 / last lines:"
  tail -12 "$DIR/cache/serve.log" 2>/dev/null
  launchctl print "gui/$UID/$LABEL" 2>/dev/null | grep -E 'last exit|state' | head -2
  exit 1
fi

HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
echo "✅ 已装成服务，开机自启、崩溃自动重来 / installed: starts at login, restarts if it dies"
echo
echo "  📱  http://$HOST.local:$PORT"
echo "  📖  http://localhost:$PORT"
echo
echo "  卸载 / remove:  scripts/install-service.command --remove"
echo "  看日志 / logs:  tail -f cache/serve.log"

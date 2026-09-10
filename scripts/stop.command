#!/usr/bin/env bash
# 一键彻底停止书房 / Stop the reader, thoroughly.
#
# 四道关，每道都查一遍：PID 文件、进程名、Docker 容器、还占着端口的任何东西。
# Four passes, each verified: the pid file, the process name, the container, and anything at all
# still holding the port. Exits non-zero if the port is somehow still busy.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PORT="${AC_PORT:-8765}"
PIDFILE=".serve.pid"
killed=0

say() { printf '  %s\n' "$1"; }

# 1) 我们自己记下的 pid / the pid we wrote at start
if [ -f "$PIDFILE" ]; then
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && say "停止 pid $pid / stopped pid $pid" && killed=1
  fi
  rm -f "$PIDFILE"
fi

# 2) 任何叫这个名字的进程，包括别的窗口里手动起的
for pid in $(pgrep -f 'audiobook_connector serve' 2>/dev/null; pgrep -f 'audiobook-connector serve' 2>/dev/null); do
  kill "$pid" 2>/dev/null && say "停止进程 $pid / stopped process $pid" && killed=1
done

# 3) Docker：容器跑着的话一起关，否则它会继续占端口
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if [ -n "$(docker ps -q -f name=audiobook-reader 2>/dev/null)" ]; then
    docker compose down --remove-orphans >/dev/null 2>&1 \
      || docker rm -f audiobook-reader >/dev/null 2>&1
    say "停止 Docker 容器 / stopped the container"
    killed=1
  fi
fi

# 4) 给它一秒收尾，然后对还占着端口的东西下重手
sleep 1
for _ in 1 2 3; do
  left="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
  [ -z "$left" ] && break
  for pid in $left; do
    say "端口 $PORT 仍被 pid $pid 占用，强制结束 / port still held by $pid, forcing"
    kill -9 "$pid" 2>/dev/null && killed=1
  done
  sleep 1
done

if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "❌ 端口 $PORT 仍被占用，可能是别的程序 / port $PORT is still busy — something else has it:"
  lsof -i "tcp:$PORT" | sed 's/^/    /'
  exit 1
fi

if [ "$killed" = 1 ]; then
  echo "✅ 已彻底停止，端口 $PORT 已释放 / stopped; port $PORT is free"
else
  echo "✅ 本来就没在运行，端口 $PORT 是空的 / was not running; port $PORT is free"
fi
say "阅读进度都在 library/_progress/ 里，不会丢 / reading positions are safe in library/_progress/"

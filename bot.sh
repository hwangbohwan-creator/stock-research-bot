#!/bin/bash
# 텔레그램 봇 관리 스크립트

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$BOT_DIR/bot.pid"
LOG_FILE="$BOT_DIR/bot.log"
PYTHON="python3"
SCRIPT="$BOT_DIR/research.py"

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "✅ 봇이 이미 실행 중입니다 (PID: $(cat "$PID_FILE"))"
        return
    fi
    cd "$BOT_DIR"
    nohup $PYTHON "$SCRIPT" --bot >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "🚀 봇 시작됨 (PID: $(cat "$PID_FILE"))"
        echo "📋 로그: $LOG_FILE"
    else
        echo "❌ 봇 시작 실패 — 로그 확인: $LOG_FILE"
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "⚠️  PID 파일 없음. 봇이 실행 중이지 않습니다."
        return
    fi
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        rm -f "$PID_FILE"
        echo "🛑 봇 종료됨 (PID: $PID)"
    else
        echo "⚠️  PID $PID 프로세스 없음. PID 파일 삭제."
        rm -f "$PID_FILE"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "🟢 봇 실행 중 (PID: $(cat "$PID_FILE"))"
        echo "📋 최근 로그:"
        tail -20 "$LOG_FILE" 2>/dev/null || echo "(로그 없음)"
    else
        echo "🔴 봇 실행 중이지 않음"
    fi
}

restart() {
    stop
    sleep 1
    start
}

logs() {
    tail -f "$LOG_FILE"
}

case "$1" in
    start)   start   ;;
    stop)    stop    ;;
    restart) restart ;;
    status)  status  ;;
    logs)    logs    ;;
    *)
        echo "사용법: $0 {start|stop|restart|status|logs}"
        ;;
esac

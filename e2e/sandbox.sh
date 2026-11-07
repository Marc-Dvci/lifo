#!/usr/bin/env bash
# Start or stop a local NEAR sandbox (nearcore 2.14.0, Linux or WSL) for the end-to-end gas test.
#   bash e2e/sandbox.sh start     RPC on 127.0.0.1:3030, root account test.near, key in $HOME_DIR/validator_key.json
#   bash e2e/sandbox.sh stop      stops the process whose PID was written at start, and only that one
# SANDBOX_DIR: the folder holding Linux-x86_64/near-sandbox. GLIBC_DIR: an extracted glibc 2.39 tree, needed on
# distributions older than glibc 2.39 (Ubuntu 22.04). SANDBOX_HOME: the chain's data folder, kept between runs.
set -euo pipefail
DIR="${SANDBOX_DIR:-$HOME/near-sandbox}"
BIN="$DIR/Linux-x86_64/near-sandbox"
HOME_DIR="${SANDBOX_HOME:-/tmp/lifo-sandbox}"
PID_FILE="$HOME_DIR.pid"
CMD=("$BIN")
if [ -n "${GLIBC_DIR:-}" ]; then
  CMD=("$GLIBC_DIR/ld-linux-x86-64.so.2" --library-path "$GLIBC_DIR:/lib/x86_64-linux-gnu" "$BIN")
fi
case "${1:-start}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "sandbox already running, pid $(cat "$PID_FILE")"
      exit 0
    fi
    [ -f "$HOME_DIR/config.json" ] || "${CMD[@]}" --home "$HOME_DIR" init >/dev/null 2>&1
    setsid nohup "${CMD[@]}" --home "$HOME_DIR" run --rpc-addr 0.0.0.0:3030 >"$HOME_DIR.log" 2>&1 < /dev/null &
    echo $! >"$PID_FILE"
    echo "sandbox started, pid $(cat "$PID_FILE"), log $HOME_DIR.log"
    ;;
  stop)
    kill "$(cat "$PID_FILE")"
    echo "sandbox stopped, pid $(cat "$PID_FILE")"
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 64
    ;;
esac

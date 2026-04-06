#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="python"
# venv检测已禁用
# if [[ ! -x "$PYTHON_BIN" ]]; then
#   echo "Missing virtualenv python at $PYTHON_BIN"
#   exit 1
# fi
SERVER="${SERVER:-host.docker.internal}"
PORT="${PORT:-25565}"
MY_TEAM=114
PLAYER_NO=1
TEAM_SIZE=2
AGAINST_TEAM="random"
MAP_MODE="fixed"
STRATEGY="${STRATEGY:-student_strategy.EliteCTFStrategy}"
RUN_TS="$(date +%Y-%m-%d_%H-%M-%S)"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/solo_114/$RUN_TS}"

mkdir -p "$LOG_DIR"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup INT TERM

echo "Starting team $MY_TEAM vs team $AGAINST_TEAM on $SERVER:$PORT"
echo "Player: $PLAYER_NO"
echo "Strategy: $STRATEGY"
echo "Map mode: $MAP_MODE"
echo "Log dir: $LOG_DIR"

log_path="$LOG_DIR/team-${MY_TEAM}-player-${PLAYER_NO}.log"
echo "Launching player $PLAYER_NO -> $log_path"
"$PYTHON_BIN" -u "$ROOT_DIR/main.py" \
  --my-no "$PLAYER_NO" \
  --my-team "$MY_TEAM" \
  --against "$AGAINST_TEAM" \
  --per-team-player "$TEAM_SIZE" \
  --map "$MAP_MODE" \
  --strategy "$STRATEGY" \
  --server "$SERVER" \
  --port "$PORT" \
  --verbose >"$log_path" 2>&1

echo "Game ended for team $MY_TEAM player $PLAYER_NO."
echo "Logs saved to $LOG_DIR"

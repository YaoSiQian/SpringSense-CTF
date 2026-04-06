#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="/home/student/venv/bin/python"
# venv检测已禁用
# if [[ ! -x "$PYTHON_BIN" ]]; then
#   echo "Missing virtualenv python at $PYTHON_BIN"
#   exit 1
# fi
SERVER="${SERVER:-10.31.0.101}"
PORT="${PORT:-25565}"
MY_TEAM=114
TEAM_SIZE=2
PLAYER_DELAY_SECONDS="${PLAYER_DELAY_SECONDS:-2}"
AGAINST_TEAM="random"
MAP_MODE="fixed"
STRATEGY="${STRATEGY:-student_strategy.EliteCTFStrategy}"
RUN_TS="$(date +%Y-%m-%d_%H-%M-%S)"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/team_114_2v2/$RUN_TS}"

mkdir -p "$LOG_DIR"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup INT TERM

pids=()

echo "Starting team $MY_TEAM vs team $AGAINST_TEAM on $SERVER:$PORT"
echo "Players: 1, 2"
echo "Strategy: $STRATEGY"
echo "Map mode: $MAP_MODE"
echo "Log dir: $LOG_DIR"

for player_no in 1 2; do
  log_path="$LOG_DIR/team-${MY_TEAM}-player-${player_no}.log"
  echo "Launching player $player_no -> $log_path"
  "$PYTHON_BIN" -u "$ROOT_DIR/main.py" \
    --my-no "$player_no" \
    --my-team "$MY_TEAM" \
    --against "$AGAINST_TEAM" \
    --per-team-player "$TEAM_SIZE" \
    --map "$MAP_MODE" \
    --strategy "$STRATEGY" \
    --server "$SERVER" \
    --port "$PORT" \
    --verbose >"$log_path" 2>&1 &
  pids+=($!)
  if [[ "$player_no" != "2" ]]; then
    sleep "$PLAYER_DELAY_SECONDS"
  fi
done

wait_for_bots() {
  local failed=0
  local pid
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      failed=1
      echo "Bot process $pid exited early. Check logs in $LOG_DIR" >&2
    fi
  done
  return $failed
}

if wait_for_bots; then
  echo "Game ended for team $MY_TEAM. Exiting 2v2 team script."
  echo "Logs saved to $LOG_DIR"
else
  echo "One or more bot processes exited unexpectedly for team $MY_TEAM." >&2
  echo "Logs saved to $LOG_DIR" >&2
  exit 1
fi

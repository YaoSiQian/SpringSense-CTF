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
TEAM_A="${TEAM_A:-37}"
TEAM_B="${TEAM_B:-38}"
TEAM_SIZE="${TEAM_SIZE:-3}"
STRATEGY_A="${STRATEGY_A:-}"
STRATEGY_B="${STRATEGY_B:-}"
PLAYER_DELAY_SECONDS="${PLAYER_DELAY_SECONDS:-2}"
TEAM_SWITCH_DELAY_SECONDS="${TEAM_SWITCH_DELAY_SECONDS:-6}"
MAP_MODE="${MAP_MODE:-fixed}"


cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup INT TERM

pids=()

read -rp "请输入队伍 A 的 team number (默认 ${TEAM_A}): " TEAM_A_INPUT
read -rp "请输入队伍 B 的 team number (默认 ${TEAM_B}): " TEAM_B_INPUT
read -rp "请输入地图模式 (fixed/random，默认 ${MAP_MODE}): " MAP_MODE_INPUT

TEAM_A="${TEAM_A_INPUT:-$TEAM_A}"
TEAM_B="${TEAM_B_INPUT:-$TEAM_B}"
MAP_MODE="${MAP_MODE_INPUT:-$MAP_MODE}"

if [[ ! "$TEAM_A" =~ ^[0-9]+$ ]] || (( TEAM_A <= 0 )); then
  echo "Invalid team A number: $TEAM_A" >&2
  exit 1
fi

if [[ ! "$TEAM_B" =~ ^[0-9]+$ ]] || (( TEAM_B <= 0 )); then
  echo "Invalid team B number: $TEAM_B" >&2
  exit 1
fi

if [[ "$MAP_MODE" != "fixed" && "$MAP_MODE" != "random" ]]; then
  echo "Invalid map mode: $MAP_MODE" >&2
  exit 1
fi

print_strategy_menu() {
  echo "请选择策略："
  echo "  1) EliteCTFStrategy (推荐)"
  echo "  2) AdaptiveCTFStrategy"
  echo "  3) RandomWalkStrategy"
  echo "  4) PickClosestFlagAndBackStrategy"
}

resolve_strategy_choice() {
  local choice="$1"
  case "$choice" in
    1) echo "student_strategy.EliteCTFStrategy" ;;
    2) echo "adaptive_strategy.AdaptiveCTFStrategy" ;;
    3) echo "student_strategy.RandomWalkStrategy" ;;
    4) echo "default_strategy.PickClosestFlagAndBackStrategy" ;;
    *)
      echo "Invalid strategy choice: $choice" >&2
      exit 1
      ;;
  esac
}

if [[ -z "$STRATEGY_A" ]]; then
  print_strategy_menu
  read -rp "请输入队伍 A 的策略编号 (1/2/3，默认 1): " STRATEGY_A_CHOICE
  STRATEGY_A_CHOICE="${STRATEGY_A_CHOICE:-1}"
  STRATEGY_A="$(resolve_strategy_choice "$STRATEGY_A_CHOICE")"
fi

if [[ -z "$STRATEGY_B" ]]; then
  print_strategy_menu
  read -rp "请输入队伍 B 的策略编号 (1/2/3，默认跟 A 一样): " STRATEGY_B_CHOICE
  STRATEGY_B_CHOICE="${STRATEGY_B_CHOICE:-same}"
  if [[ "$STRATEGY_B_CHOICE" == "same" ]]; then
    STRATEGY_B="$STRATEGY_A"
  else
    STRATEGY_B="$(resolve_strategy_choice "$STRATEGY_B_CHOICE")"
  fi
fi

launch_team() {
  local team="$1"
  local against="$2"
  local strategy="$3"
  local player_no
  for ((player_no = 1; player_no <= TEAM_SIZE; player_no++)); do
    local log_path="$LOG_DIR/team-${team}-player-${player_no}.log"
    echo "Launching team $team player $player_no against $against with $strategy -> $log_path"
    "$PYTHON_BIN" -u "$ROOT_DIR/main.py" \
      --my-no "$player_no" \
      --my-team "$team" \
      --against "$against" \
      --per-team-player "$TEAM_SIZE" \
      --map "$MAP_MODE" \
      --strategy "$strategy" \
      --server "$SERVER" \
      --port "$PORT" \
      --verbose >"$log_path" 2>&1 &
    pids+=($!)
    if (( player_no < TEAM_SIZE )); then
      sleep "$PLAYER_DELAY_SECONDS"
    fi
  done
}

RUN_TS="$(date +%Y-%m-%d_%H-%M-%S)"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/self_3v3/$RUN_TS}"
mkdir -p "$LOG_DIR"

echo "Starting self 3v3: team $TEAM_A vs team $TEAM_B on $SERVER:$PORT"
echo "Team size: $TEAM_SIZE"
echo "Team A strategy: $STRATEGY_A"
echo "Team B strategy: $STRATEGY_B"
echo "Map mode: $MAP_MODE"
echo "Player launch delay: ${PLAYER_DELAY_SECONDS}s"
echo "Team switch delay: ${TEAM_SWITCH_DELAY_SECONDS}s"
echo "Log dir: $LOG_DIR"

launch_team "$TEAM_A" "$TEAM_B" "$STRATEGY_A"
sleep "$TEAM_SWITCH_DELAY_SECONDS"
launch_team "$TEAM_B" "$TEAM_A" "$STRATEGY_B"

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
  echo "Game ended for self 3v3 match."
  echo "Logs saved to $LOG_DIR"
else
  echo "One or more bot processes exited unexpectedly." >&2
  echo "Logs saved to $LOG_DIR" >&2
  exit 1
fi

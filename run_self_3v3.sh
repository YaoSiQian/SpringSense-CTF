set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="/home/student/venv/bin/python"
SERVER="${SERVER:-10.31.0.101}"
PORT="${PORT:-25565}"
TEAM_A="${TEAM_A:-37}"
TEAM_B="${TEAM_B:-38}"
TEAM_SIZE="${TEAM_SIZE:-3}"
STRATEGY_A="${STRATEGY_A:-}"
STRATEGY_B="${STRATEGY_B:-}"
PLAYER_DELAY_SECONDS="${PLAYER_DELAY_SECONDS:-2}"
TEAM_SWITCH_DELAY_SECONDS="${TEAM_SWITCH_DELAY_SECONDS:-6}"


cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

read -rp "请输入队伍 A 的 team number (默认 ${TEAM_A}): " TEAM_A_INPUT
read -rp "请输入队伍 B 的 team number (默认 ${TEAM_B}): " TEAM_B_INPUT

TEAM_A="${TEAM_A_INPUT:-$TEAM_A}"
TEAM_B="${TEAM_B_INPUT:-$TEAM_B}"

if [[ ! "$TEAM_A" =~ ^[0-9]+$ ]] || (( TEAM_A <= 0 )); then
  echo "Invalid team A number: $TEAM_A" >&2
  exit 1
fi

if [[ ! "$TEAM_B" =~ ^[0-9]+$ ]] || (( TEAM_B <= 0 )); then
  echo "Invalid team B number: $TEAM_B" >&2
  exit 1
fi

print_strategy_menu() {
  echo "请选择策略："
  echo "  1) AdaptiveCTFStrategy"
  echo "  2) RandomWalkStrategy"
  echo "  3) PickClosestFlagAndBackStrategy"
}

resolve_strategy_choice() {
  local choice="$1"
  case "$choice" in
    1) echo "student_strategy.AdaptiveCTFStrategy" ;;
    2) echo "student_strategy.RandomWalkStrategy" ;;
    3) echo "default_strategy.PickClosestFlagAndBackStrategy" ;;
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
    echo "Launching team $team player $player_no against $against with $strategy"
    "$PYTHON_BIN" "$ROOT_DIR/main.py" \
      --my-no "$player_no" \
      --my-team "$team" \
      --against "$against" \
      --per-team-player "$TEAM_SIZE" \
      --strategy "$strategy" \
      --server "$SERVER" \
      --port "$PORT" \
      --verbose &
    if (( player_no < TEAM_SIZE )); then
      sleep "$PLAYER_DELAY_SECONDS"
    fi
  done
}

echo "Starting self 3v3: team $TEAM_A vs team $TEAM_B on $SERVER:$PORT"
echo "Team size: $TEAM_SIZE"
echo "Team A strategy: $STRATEGY_A"
echo "Team B strategy: $STRATEGY_B"
echo "Player launch delay: ${PLAYER_DELAY_SECONDS}s"
echo "Team switch delay: ${TEAM_SWITCH_DELAY_SECONDS}s"

launch_team "$TEAM_A" "$TEAM_B" "$STRATEGY_A"
sleep "$TEAM_SWITCH_DELAY_SECONDS"
launch_team "$TEAM_B" "$TEAM_A" "$STRATEGY_B"

wait

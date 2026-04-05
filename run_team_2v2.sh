set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="/home/student/venv/bin/python"
SERVER="${SERVER:-10.31.0.101}"
PORT="${PORT:-25565}"
TEAM_SIZE=2
PLAYER_DELAY_SECONDS="${PLAYER_DELAY_SECONDS:-2}"
STRATEGY="${STRATEGY:-student_strategy.AdaptiveCTFStrategy}"
RUN_TS="$(date +%Y-%m-%d_%H-%M-%S)"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/team_2v2/$RUN_TS}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing virtualenv python at $PYTHON_BIN"
  exit 1
fi

mkdir -p "$LOG_DIR"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

read -rp "?请输入自己的 team number: " MY_TEAM
read -rp "?请输入对方的 team number: " AGAINST_TEAM
read -rp "?请输入地图模式 (fixed/random，默认 fixed): " MAP_MODE
echo "请选择策略："
echo "  1) EliteCTFStrategy (推荐)"
echo "  2) AdaptiveCTFStrategy"
echo "  3) RandomWalkStrategy"
echo "  4) PickClosestFlagAndBackStrategy"
read -rp "?请输入策略编号 (1/2/3/4，默认 1): " STRATEGY_CHOICE

MAP_MODE="${MAP_MODE:-fixed}"
STRATEGY_CHOICE="${STRATEGY_CHOICE:-1}"

if [[ ! "$MY_TEAM" =~ ^[0-9]+$ ]] || (( MY_TEAM <= 0 )); then
  echo "Invalid own team number: $MY_TEAM"
  exit 1
fi

if [[ ! "$AGAINST_TEAM" =~ ^[0-9]+$ ]] || (( AGAINST_TEAM <= 0 )); then
  echo "Invalid opponent team number: $AGAINST_TEAM"
  exit 1
fi

if [[ "$MAP_MODE" != "fixed" && "$MAP_MODE" != "random" ]]; then
  echo "Invalid map mode: $MAP_MODE"
  exit 1
fi

case "$STRATEGY_CHOICE" in
  1)
    STRATEGY="student_strategy.EliteCTFStrategy"
    ;;
  2)
    STRATEGY="student_strategy.AdaptiveCTFStrategy"
    ;;
  3)
    STRATEGY="student_strategy.RandomWalkStrategy"
    ;;
  4)
    STRATEGY="default_strategy.PickClosestFlagAndBackStrategy"
    ;;
  *)
    echo "Invalid strategy choice: $STRATEGY_CHOICE"
    exit 1
    ;;
esac

echo "Starting team $MY_TEAM vs team $AGAINST_TEAM on $SERVER:$PORT"
echo "Players: 1, 2"
echo "Strategy: $STRATEGY"
echo "Map mode: $MAP_MODE"
echo "Log dir: $LOG_DIR"

for player_no in 1 2; do
  log_path="$LOG_DIR/team-${MY_TEAM}-player-${player_no}.log"
  echo "Launching player $player_no -> $log_path"
  "$PYTHON_BIN" "$ROOT_DIR/main.py" \
    --my-no "$player_no" \
    --my-team "$MY_TEAM" \
    --against "$AGAINST_TEAM" \
    --per-team-player "$TEAM_SIZE" \
    --map "$MAP_MODE" \
    --strategy "$STRATEGY" \
    --server "$SERVER" \
    --port "$PORT" \
    --verbose >"$log_path" 2>&1 &
  if [[ "$player_no" != "2" ]]; then
    sleep "$PLAYER_DELAY_SECONDS"
  fi
done

wait

echo "Team $MY_TEAM match finished"
echo "Logs saved to $LOG_DIR"

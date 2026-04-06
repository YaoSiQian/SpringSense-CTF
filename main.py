from __future__ import annotations

import argparse
import importlib
import json
from datetime import datetime
from typing import Any

from lib.world import (
    DEFAULT_PORT,
    DEFAULT_SERVER,
    JavaScriptBridge,
    World,
    build_final_shot_path,
    build_multi_log_path,
)

DEFAULT_STRATEGY = "student_strategy.RandomWalkStrategy"
JS_PRELOAD_MODULES = (
    "mineflayer",
    "mineflayer-pathfinder",
    "vec3",
    "minecraft-data",
    "node:vm",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Connect a Minecraft CTF bot and inspect the world.")
    parser.add_argument(
        "--my-no",
        "--player-num",
        dest="player_num",
        required=True,
        type=_parse_positive_int,
        help="The player number used in the bot name.",
    )
    parser.add_argument(
        "--my-team",
        "--team-num",
        dest="team_num",
        required=True,
        type=_parse_positive_int,
        help="The team number used in the bot name.",
    )
    parser.add_argument(
        "--against",
        default=None,
        type=_parse_against_team,
        help="The opposing team number, 'none' for debugging mode, or 'random' for random opponent.",
    )
    parser.add_argument(
        "--per-team-player",
        dest="per_team_player",
        default=1,
        type=_parse_positive_int,
        help="Total number of players per team in the match intent.",
    )
    parser.add_argument(
        "--map",
        dest="map_mode",
        default="fixed",
        type=str.lower,
        choices=("fixed", "random"),
        help="Map mode announced during initialization.",
    )
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--action-tick", type=float, default=0.1)
    parser.add_argument("--snapshot-tick", type=float, default=1.0)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose World runtime logs.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    js_runtime: Any | None = None
    world: World | None = None
    run_time = datetime.now()
    multi_log_path = build_multi_log_path(
        team_num=args.team_num,
        player_num=args.player_num,
        when=run_time,
    )
    final_shot_path = build_final_shot_path(
        team_num=args.team_num,
        player_num=args.player_num,
        when=run_time,
    )

    max_reconnect_attempts = 10
    reconnect_delay_seconds = 5
    reconnect_count = 0

    try:
        js_runtime, js_bridge = _initialize_js_bridge()

        while reconnect_count <= max_reconnect_attempts:
            world = None
            try:
                world = World(
                    js_bridge=js_bridge,
                    team_num=args.team_num,
                    player_num=args.player_num,
                    against_team=args.against,
                    total_player_per_team=args.per_team_player,
                    map_mode=args.map_mode,
                    server=args.server,
                    port=args.port,
                    verbose=args.verbose,
                )
                strategy = _load_strategy(args.strategy)
                world.run_with_logging(
                    strategy,
                    action_tick_seconds=args.action_tick,
                    snapshot_tick_seconds=args.snapshot_tick,
                    log_path=multi_log_path,
                )

                # 正常结束游戏（非断开连接）
                print(f"[Main] Game ended normally.")
                print(f"Appended snapshots to {multi_log_path}")
                final_observation = world.observe()
                final_shot_path.parent.mkdir(parents=True, exist_ok=True)
                final_shot_path.write_text(
                    json.dumps(final_observation.to_dict(), indent=2),
                    encoding="utf-8",
                )
                print(f"Wrote final observation to {final_shot_path}")
                break  # 正常退出循环

            except RuntimeError as e:
                error_msg = str(e).lower()
                # 检测到断开连接或游戏结束相关的错误
                if any(keyword in error_msg for keyword in [
                    "game ended", "disconnected", "connection", "kicked",
                    "end", "closed", "timeout", "error"
                ]):
                    reconnect_count += 1
                    if reconnect_count <= max_reconnect_attempts:
                        print(f"[Main] Connection lost or game ended unexpectedly: {e}")
                        print(f"[Main] Attempting to reconnect ({reconnect_count}/{max_reconnect_attempts}) in {reconnect_delay_seconds}s...")
                        if world is not None:
                            try:
                                world.close()
                            except Exception:
                                pass
                        import time
                        time.sleep(reconnect_delay_seconds)
                        continue
                    else:
                        print(f"[Main] Max reconnection attempts reached. Giving up.")
                        raise
                else:
                    raise

            except Exception as e:
                # 其他异常，尝试重连
                reconnect_count += 1
                if reconnect_count <= max_reconnect_attempts:
                    print(f"[Main] Unexpected error: {e}")
                    print(f"[Main] Attempting to reconnect ({reconnect_count}/{max_reconnect_attempts}) in {reconnect_delay_seconds}s...")
                    if world is not None:
                        try:
                            world.close()
                        except Exception:
                            pass
                    import time
                    time.sleep(reconnect_delay_seconds)
                    continue
                else:
                    print(f"[Main] Max reconnection attempts reached. Giving up.")
                    raise

            finally:
                if world is not None:
                    try:
                        world.close()
                    except Exception:
                        pass

    finally:
        if js_runtime is not None:
            try:
                js_runtime.terminate()
            except Exception:
                pass

    return 0


def _initialize_js_bridge() -> tuple[Any, JavaScriptBridge]:
    try:
        import javascript
        from javascript import On, off, require, once
    except Exception as exc:
        raise RuntimeError(
            "Unable to initialize the Python JavaScript bridge. "
            "Install/configure the `javascript` package and Mineflayer dependencies first. "
            f"Root cause: {type(exc).__name__}: {exc}"
        ) from exc

    # In a fresh CLI process, import-time init is usually enough.
    # Only force a reset if the bridge probe fails.
    try:
        require("node:vm")
    except Exception:
        try:
            javascript.terminate()
        except Exception:
            pass
        try:
            javascript.init()
            from javascript import On, off, require, once
        except Exception as exc:
            raise RuntimeError(
                "Unable to initialize the Python JavaScript bridge. "
                "Install/configure the `javascript` package and Mineflayer dependencies first. "
                f"Root cause: {type(exc).__name__}: {exc}"
            ) from exc

    for module_name in JS_PRELOAD_MODULES:
        require(module_name)

    return javascript, JavaScriptBridge(require=require, once=once, On=On, off=off)


def _load_strategy(qualified_name: str):
    module_name, _, attribute_name = qualified_name.rpartition(".")
    if not module_name or not attribute_name:
        raise ValueError(
            f"Invalid strategy {qualified_name!r}. Expected format module_name.ClassName."
        )
    module = importlib.import_module(module_name)
    strategy_cls = getattr(module, attribute_name)
    return strategy_cls()


def _parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return parsed


def _parse_against_team(value: str) -> int | str | None:
    normalized = value.strip().lower()
    if normalized == "none":
        return None
    if normalized == "random":
        return "random"
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a positive integer, 'none', or 'random'.") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer, 'none', or 'random'.")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())

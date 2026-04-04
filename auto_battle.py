#!/usr/bin/env python3
"""
自动对战脚本：7426队 vs 随机队伍
- 7426使用PickClosestFlagAndBackStrategy策略
- 对手使用RandomWalkStrategy策略
- 检测到胜利后立即结束并开始下一循环

使用方法:
    python auto_battle.py

按 Ctrl+C 停止脚本
"""

from __future__ import annotations

import random
import threading
import time
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 配置
MAIN_TEAM = 7426
MAIN_PLAYER = 1
OPPONENT_PLAYER = 1
PER_TEAM_PLAYER = 1
MAP_MODE = "fixed"
SERVER = "10.31.0.101"
PORT = 25565
ACTION_TICK = 0.1
SNAPSHOT_TICK = 1.0

JS_PRELOAD_MODULES = (
    "mineflayer",
    "mineflayer-pathfinder",
    "vec3",
    "minecraft-data",
    "node:vm",
)


def get_random_opponent_team() -> int:
    """生成随机对手队伍号（1~99999）"""
    return random.randint(1, 99999)


def initialize_js_bridge():
    """初始化JavaScript桥接"""
    try:
        import javascript
        from javascript import On, off, require, once
    except Exception as exc:
        raise RuntimeError(
            "Unable to initialize the Python JavaScript bridge. "
            "Install/configure the `javascript` package and Mineflayer dependencies first. "
            f"Root cause: {type(exc).__name__}: {exc}"
        ) from exc

    # 尝试初始化
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

    from lib.world import JavaScriptBridge as JSBridgeClass
    return javascript, JSBridgeClass(require=require, once=once, On=On, off=off)


def load_strategy(qualified_name: str):
    """加载策略类"""
    import importlib
    module_name, _, attribute_name = qualified_name.rpartition(".")
    if not module_name or not attribute_name:
        raise ValueError(
            f"Invalid strategy {qualified_name!r}. Expected format module_name.ClassName."
        )
    module = importlib.import_module(module_name)
    strategy_cls = getattr(module, attribute_name)
    return strategy_cls()


class BotController:
    """Bot控制器 - 包装World类并添加胜利检测"""
    
    def __init__(self, team_num: int, player_num: int, against_team: int | None,
                 strategy_name: str, name: str):
        self.team_num = team_num
        self.player_num = player_num
        self.against_team = against_team
        self.strategy_name = strategy_name
        self.name = name
        
        self.world: Any = None
        self.victory_detected = False
        self.victory_team: str | None = None
        self.game_ended = False
        self.error: Exception | None = None
        self.thread: threading.Thread | None = None
        
    def _run(self, js_bridge):
        """在独立线程中运行bot"""
        from lib.world import (
            World,
            build_multi_log_path,
            build_final_shot_path,
        )
        
        # 创建自定义World类来检测胜利
        class VictoryDetectingWorld(World):
            def __init__(inner_self, **kwargs):
                super().__init__(**kwargs)
                inner_self._outer = self
            
            def _handle_incoming_message(inner_self, maybe_sender, maybe_message, *args):
                # 调用父类处理
                super()._handle_incoming_message(maybe_sender, maybe_message, *args)
                
                # 检测胜利
                sender_str = str(maybe_sender) if maybe_sender else ""
                message_str = str(maybe_message) if maybe_message else ""
                full_text = sender_str + " " + message_str
                
                # 检测7426胜利
                if "7426" in full_text:
                    victory_keywords = ["win", "wins", "victory", "winner", "title", "恭喜"]
                    if any(kw in full_text.lower() for kw in victory_keywords):
                        inner_self._outer.victory_detected = True
                        inner_self._outer.victory_team = "7426"
                        print(f"🎉 [{self.name}] VICTORY for 7426 detected! Msg: {full_text[:80]}")
        
        run_time = datetime.now()
        multi_log_path = build_multi_log_path(
            team_num=self.team_num,
            player_num=self.player_num,
            when=run_time,
        )
        final_shot_path = build_final_shot_path(
            team_num=self.team_num,
            player_num=self.player_num,
            when=run_time,
        )
        
        try:
            # 加载策略
            strategy = load_strategy(self.strategy_name)
            
            # 创建World
            self.world = VictoryDetectingWorld(
                js_bridge=js_bridge,
                team_num=self.team_num,
                player_num=self.player_num,
                against_team=self.against_team,
                total_player_per_team=PER_TEAM_PLAYER,
                map_mode=MAP_MODE,
                server=SERVER,
                port=PORT,
                verbose=True,
            )
            
            # 运行游戏循环
            self.world.run_with_logging(
                strategy,
                action_tick_seconds=ACTION_TICK,
                snapshot_tick_seconds=SNAPSHOT_TICK,
                log_path=multi_log_path,
            )
            
            self.game_ended = True
            print(f"✅ [{self.name}] Game finished normally")
            
            # 保存最终状态
            try:
                final_obs = self.world.observe()
                final_shot_path.parent.mkdir(parents=True, exist_ok=True)
                final_shot_path.write_text(
                    json.dumps(final_obs.to_dict(), indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                print(f"⚠️ [{self.name}] Failed to save final observation: {e}")
                
        except Exception as e:
            self.error = e
            print(f"❌ [{self.name}] Error: {e}")
        finally:
            if self.world:
                try:
                    self.world.close()
                except Exception:
                    pass
    
    def start(self, js_bridge):
        """启动bot线程"""
        self.thread = threading.Thread(target=self._run, args=(js_bridge,))
        self.thread.start()
    
    def stop(self):
        """停止bot"""
        if self.world:
            try:
                self.world.close()
            except Exception:
                pass
    
    def join(self, timeout=None):
        """等待线程结束"""
        if self.thread:
            self.thread.join(timeout)
    
    def is_alive(self):
        """检查线程是否存活"""
        return self.thread.is_alive() if self.thread else False


def run_match(match_number: int) -> dict:
    """运行单场比赛"""
    print(f"\n{'='*70}")
    print(f"🏁 MATCH #{match_number}")
    print(f"{'='*70}")
    
    # 生成随机对手
    opponent_team = get_random_opponent_team()
    opponent_display = str(opponent_team)
    print(f"Team A (Main):    CTF-{MAIN_TEAM}-{MAIN_PLAYER} (PickClosestFlagAndBackStrategy)")
    print(f"Team B (Opponent): CTF-{opponent_display}-{OPPONENT_PLAYER} (RandomWalkStrategy)")
    print()
    
    # 初始化JavaScript
    js_runtime, js_bridge = initialize_js_bridge()
    
    try:
        # 创建两个bot控制器
        main_bot = BotController(
            team_num=MAIN_TEAM,
            player_num=MAIN_PLAYER,
            against_team=opponent_team,
            strategy_name="default_strategy.PickClosestFlagAndBackStrategy",
            name=f"7426"
        )
        
        opponent_bot = BotController(
            team_num=opponent_team,
            player_num=OPPONENT_PLAYER,
            against_team=MAIN_TEAM,
            strategy_name="default_strategy.RandomWalkStrategy",
            name=opponent_display
        )
        
        # 启动两个bot
        print("Starting bots...")
        main_bot.start(js_bridge)
        time.sleep(2)  # 延迟启动对手，避免同时连接
        opponent_bot.start(js_bridge)
        
        # 等待游戏结束
        print("Game in progress...")
        start_time = time.time()
        max_wait = 300  # 最多等待5分钟（正常游戏3分钟+准备时间）
        
        while True:
            # 检查是否超时
            elapsed = time.time() - start_time
            if elapsed > max_wait:
                print("⏱️ Match timeout reached!")
                break
            
            # 检查是否任一bot已结束
            if not main_bot.is_alive():
                print("Main bot finished")
                time.sleep(3)  # 给对手一点时间
                break
            if not opponent_bot.is_alive():
                print("Opponent bot finished")
                time.sleep(3)  # 给主bot一点时间
                break
            
            time.sleep(0.5)
        
        # 停止两个bot
        print("Stopping bots...")
        main_bot.stop()
        opponent_bot.stop()
        
        # 等待线程结束
        main_bot.join(timeout=10)
        opponent_bot.join(timeout=10)
        
        # 返回结果
        return {
            "main": {
                "victory": main_bot.victory_detected,
                "victory_team": main_bot.victory_team,
                "error": str(main_bot.error) if main_bot.error else None,
            },
            "opponent": {
                "victory": opponent_bot.victory_detected,
                "victory_team": opponent_bot.victory_team,
                "error": str(opponent_bot.error) if opponent_bot.error else None,
            },
            "opponent_team": opponent_display,
        }
        
    finally:
        # 清理JavaScript运行时
        try:
            js_runtime.terminate()
        except Exception as e:
            print(f"Warning: Error terminating JS runtime: {e}")


def main():
    """主函数"""
    print("="*70)
    print("🤖 MINECRAFT CTF AUTO BATTLE")
    print("="*70)
    print(f"Configuration:")
    print(f"  Main Team:     {MAIN_TEAM}")
    print(f"  Map Mode:      {MAP_MODE}")
    print(f"  Server:        {SERVER}:{PORT}")
    print(f"  Main Strategy: PickClosestFlagAndBackStrategy (Strong)")
    print(f"  Opp Strategy:  RandomWalkStrategy (Weak)")
    print("="*70)
    print()
    
    match_number = 0
    wins = 0
    losses = 0
    errors = 0
    
    try:
        while True:
            match_number += 1
            
            try:
                result = run_match(match_number)
                
                # 分析结果
                main_victory = result["main"]["victory"]
                main_error = result["main"]["error"]
                opponent_team = result["opponent_team"]
                
                print(f"\n📊 MATCH #{match_number} RESULT:")
                
                if main_victory:
                    wins += 1
                    print(f"  🏆 WIN! Team {MAIN_TEAM} defeated team {opponent_team}")
                elif main_error:
                    errors += 1
                    print(f"  ❌ ERROR: {main_error}")
                else:
                    losses += 1
                    print(f"  💔 LOST or Draw against team {opponent_team}")
                
                print(f"  Stats: {wins}W / {losses}L / {errors}E")
                
            except Exception as e:
                errors += 1
                print(f"\n❌ MATCH #{match_number} FAILED: {e}")
                import traceback
                traceback.print_exc()
            
            # 下一轮前的延迟
            print(f"\n⏳ Next match in 3 seconds... (Ctrl+C to stop)")
            time.sleep(3)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user")
    
    print("\n" + "="*70)
    print("📈 FINAL STATS")
    print("="*70)
    print(f"  Total Matches: {match_number}")
    print(f"  Wins:          {wins}")
    print(f"  Losses:        {losses}")
    print(f"  Errors:        {errors}")
    print(f"  Win Rate:      {wins/max(1,match_number)*100:.1f}%")
    print("="*70)


if __name__ == "__main__":
    main()

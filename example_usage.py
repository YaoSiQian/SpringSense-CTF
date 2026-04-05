#!/usr/bin/env python3
"""
CTF 机器人使用示例 - 展示如何 import 引用项目库
"""

from __future__ import annotations

# ============================================
# 1. 导入项目库核心组件
# ============================================

# 动作类型
from lib.actions import Action, MoveTo, Chat

# 观测数据类型
from lib.observation import (
    Observation,
    BotState,
    PlayerState,
    BlockState,
    EntityState,
    GridPosition,
    Vec3,
    MapMetadata,
    TeamName,
    opponent_team,
    normalize_team_name,
)

# 世界连接和管理
from lib.world import (
    World,
    JavaScriptBridge,
    ScanBounds,
    DEFAULT_SERVER,
    DEFAULT_PORT,
    build_multi_log_path,
    build_final_shot_path,
)

# ============================================
# 2. 策略类示例（必须实现两个方法）
# ============================================

from dataclasses import dataclass


@dataclass
class MyCustomStrategy:
    """
    自定义策略类
    
    必须实现两个方法:
    - on_game_start(obs): 游戏开始时调用一次
    - compute_next_action(obs): 每 tick 调用，返回动作列表
    """
    
    # 可以添加自己的状态变量
    target_pos: GridPosition | None = None
    
    def on_game_start(self, obs: Observation) -> None:
        """游戏开始时调用一次，用于初始化"""
        print(f"游戏开始！我是 {obs.bot_name}")
        print(f"队伍: {obs.team}")
        print(f"位置: ({obs.me.position.x}, {obs.me.position.z})")
        
    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat] | None:
        """
        每 tick 调用，决定机器人动作
        
        可用操作:
        - MoveTo(x, z, radius=1, sprint=True): 移动到指定位置
        - Chat(message): 发送聊天消息
        """
        me = obs.self_player  # 获取自己的玩家状态
        
        # 检查是否持有旗帜
        if me.has_flag:
            # 返回己方空目标点
            if obs.my_targets:
                target = obs.my_targets[0].grid_position
                return [MoveTo(x=target.x, z=target.z, radius=0)]
        else:
            # 去夺取敌方旗帜
            if obs.flags_to_capture:
                target = obs.flags_to_capture[0].grid_position
                return [MoveTo(x=target.x, z=target.z, radius=0)]
        
        # 原地不动
        return [MoveTo(x=me.position.x, z=me.position.z, radius=0)]


# ============================================
# 3. 使用示例
# ============================================

def example_connect_and_run():
    """
    示例：连接服务器并运行机器人
    """
    # 需要先初始化 JavaScript 桥接
    from javascript import require, once, On, off, init as js_init
    
    # 初始化 JS 环境
    try:
        require("node:vm")
    except Exception:
        js_init()
    
    # 预加载模块
    for module in ("mineflayer", "mineflayer-pathfinder", "vec3", "minecraft-data"):
        require(module)
    
    # 创建 JS 桥接
    js_bridge = JavaScriptBridge(require=require, once=once, On=On, off=off)
    
    # 创建 World 对象
    world = World(
        js_bridge=js_bridge,
        team_num=52,           # 队伍编号
        player_num=1,          # 玩家编号
        against_team=53,       # 对手队伍编号
        total_player_per_team=1,
        map_mode="fixed",      # 地图模式: fixed 或 random
        server="10.31.0.101",  # 服务器地址
        port=25565,            # 服务器端口
        verbose=True,
    )
    
    # 创建策略实例
    strategy = MyCustomStrategy()
    
    # 运行游戏（带日志）
    world.run_with_logging(
        strategy,
        action_tick_seconds=0.1,    # 动作计算间隔
        snapshot_tick_seconds=1.0,   # 快照记录间隔
        log_path=build_multi_log_path(team_num=52, player_num=1),
    )
    
    # 清理
    world.close()


def example_observation_usage():
    """
    示例：Observation 对象的常用属性
    """
    # 假设 obs 是从 world.observe() 获取的
    obs: Observation = ...  # type: ignore
    
    # 基本信息
    print(f"机器人名称: {obs.bot_name}")
    print(f"所属队伍: {obs.team}")  # "L" 或 "R"
    print(f"敌方队伍: {obs.enemy_team}")
    
    # 自己
    me = obs.self_player
    print(f"自己位置: ({me.position.x}, {me.position.z})")
    print(f"是否持有旗帜: {me.has_flag}")
    print(f"是否在监狱: {me.in_prison}")
    
    # 玩家信息
    print(f"所有玩家: {[p.name for p in obs.players]}")
    print(f"队友: {[p.name for p in obs.teammates]}")
    print(f"敌人: {[p.name for p in obs.enemies]}")
    
    # 旗帜信息
    print(f"可夺取的敌方旗帜: {len(obs.flags_to_capture)}")
    print(f"需要保护的我方旗帜: {len(obs.flags_to_protect)}")
    
    # 目标点
    print(f"己方空目标点: {len(obs.my_targets)}")
    for target in obs.my_targets:
        print(f"  - 位置: ({target.grid_position.x}, {target.grid_position.z})")
    
    # 地图边界
    print(f"地图范围: X({obs.map.min_x}~{obs.map.max_x}), Z({obs.map.min_z}~{obs.map.max_z})")
    
    # 分数
    print(f"分数 - L: {obs.scores.L}, R: {obs.scores.R}")


# ============================================
# 4. 作为主程序运行
# ============================================

if __name__ == "__main__":
    print("CTF 机器人使用示例")
    print("=" * 50)
    print()
    print("可用的导入:")
    print("  from lib.actions import MoveTo, Chat")
    print("  from lib.observation import Observation, GridPosition, ...")
    print("  from lib.world import World, JavaScriptBridge, ...")
    print()
    print("策略类必须实现:")
    print("  - on_game_start(self, obs)")
    print("  - compute_next_action(self, obs) -> list[MoveTo | Chat]")

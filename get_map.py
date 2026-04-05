#!/usr/bin/env python3
"""
可直接运行的 CTF 机器人示例
展示如何导入项目库、连接服务器、加入游戏并获取游戏地图
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 导入项目库
from lib.world import (
    DEFAULT_PORT,
    DEFAULT_SERVER,
    JavaScriptBridge,
    World,
    build_final_shot_path,
    build_multi_log_path,
)
from lib.observation import Observation, GridPosition
from lib.actions import MoveTo, Chat

# 预加载的 JS 模块
JS_PRELOAD_MODULES = (
    "mineflayer",
    "mineflayer-pathfinder",
    "vec3",
    "minecraft-data",
    "node:vm",
)


def initialize_js_bridge() -> tuple[Any, JavaScriptBridge]:
    """初始化 JavaScript 桥接"""
    try:
        import javascript
        from javascript import On, off, require, once
    except Exception as exc:
        raise RuntimeError(
            "无法初始化 Python JavaScript 桥接。"
            "请先安装 `javascript` 包和 Mineflayer 依赖。"
            f"原因: {type(exc).__name__}: {exc}"
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
                "无法初始化 Python JavaScript 桥接。"
                f"原因: {type(exc).__name__}: {exc}"
            ) from exc

    # 预加载必要的模块
    for module_name in JS_PRELOAD_MODULES:
        require(module_name)

    return javascript, JavaScriptBridge(require=require, once=once, On=On, off=off)


@dataclass
class SimpleCTFStrategy:
    """
    简单的 CTF 策略示例
    - 如果持有旗帜，返回己方空目标点
    - 否则去夺取最近的敌方旗帜
    """
    last_target: GridPosition | None = field(default=None, init=False)
    last_intent: str = field(default="", init=False)

    def on_game_start(self, obs: Observation) -> None:
        """游戏开始时调用一次"""
        print(f"🎮 游戏开始！我是 {obs.bot_name}，队伍: {obs.team}")
        print(f"   位置: ({obs.me.position.x}, {obs.me.position.z})")
        print(f"   可夺取旗帜: {len(obs.flags_to_capture)} 个")
        print(f"   己方目标点: {len(obs.my_targets)} 个")

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat] | None:
        """
        每 tick 调用以决定机器人动作
        返回 MoveTo 或 Chat 动作的列表
        """
        me = obs.self_player
        actions: list[MoveTo | Chat] = []

        if me.has_flag:
            # 持有旗帜，返回己方空目标点
            targets = obs.my_targets
            if targets:
                target = targets[0].grid_position
                if self.last_intent != "returning":
                    actions.append(Chat(message=f"Returning flag to ({target.x}, {target.z})"))
                    self.last_intent = "returning"
                actions.append(MoveTo(x=target.x, z=target.z, radius=0))
            else:
                # 没有空目标点，原地等待
                if self.last_intent != "waiting":
                    actions.append(Chat(message="No empty targets, holding position"))
                    self.last_intent = "waiting"
                actions.append(MoveTo(x=me.position.x, z=me.position.z, radius=0))
        else:
            # 去夺取最近的敌方旗帜
            flags = obs.flags_to_capture
            if flags:
                # 找到最近的旗帜
                closest_flag = min(
                    flags,
                    key=lambda f: abs(f.grid_position.x - me.position.x) 
                                + abs(f.grid_position.z - me.position.z)
                )
                target = closest_flag.grid_position
                if self.last_intent != "capturing":
                    actions.append(Chat(message=f"Capturing flag at ({target.x}, {target.z})"))
                    self.last_intent = "capturing"
                actions.append(MoveTo(x=target.x, z=target.z, radius=0))
            else:
                # 没有可夺取的旗帜
                if self.last_intent != "searching":
                    actions.append(Chat(message="No flags to capture"))
                    self.last_intent = "searching"
                actions.append(MoveTo(x=me.position.x, z=me.position.z, radius=0))

        return actions


def run_bot(
    team_num: int,
    player_num: int,
    against_team: int | None = 52,
    server: str = DEFAULT_SERVER,
    port: int = DEFAULT_PORT,
    verbose: bool = True,
) -> None:
    """
    运行 CTF 机器人
    
    参数:
        team_num: 队伍编号
        player_num: 玩家编号
        against_team: 对手队伍编号，None 表示调试模式
        server: 服务器地址
        port: 服务器端口
        verbose: 是否输出详细日志
    """
    js_runtime: Any | None = None
    world: World | None = None
    
    # 准备日志路径
    run_time = datetime.now()
    multi_log_path = build_multi_log_path(
        team_num=team_num,
        player_num=player_num,
        when=run_time,
    )
    final_shot_path = build_final_shot_path(
        team_num=team_num,
        player_num=player_num,
        when=run_time,
    )

    try:
        # 1. 初始化 JS 桥接
        print("🔧 初始化 JavaScript 桥接...")
        js_runtime, js_bridge = initialize_js_bridge()
        print("✅ JavaScript 桥接初始化成功")

        # 2. 创建 World 对象
        print(f"🌐 连接到服务器 {server}:{port}...")
        world = World(
            js_bridge=js_bridge,
            team_num=team_num,
            player_num=player_num,
            against_team=against_team,
            total_player_per_team=1,
            map_mode="fixed",
            server=server,
            port=port,
            verbose=verbose,
        )

        # 3. 创建策略
        strategy = SimpleCTFStrategy()

        # 4. 加入世界并运行游戏（带日志记录）
        print("🚀 加入游戏世界...")
        world.run_with_logging(
            strategy,
            action_tick_seconds=0.1,    # 每 0.1 秒计算一次动作
            snapshot_tick_seconds=1.0,   # 每 1 秒记录一次快照
            log_path=multi_log_path,
        )

        print(f"✅ 游戏结束！日志保存到: {multi_log_path}")

        # 5. 保存最终观察状态
        final_observation = world.observe()
        final_shot_path.parent.mkdir(parents=True, exist_ok=True)
        final_shot_path.write_text(
            json.dumps(final_observation.to_dict(), indent=2),
            encoding="utf-8",
        )
        print(f"📝 最终状态保存到: {final_shot_path}")

    finally:
        # 清理资源
        if world is not None:
            world.close()
        if js_runtime is not None:
            try:
                js_runtime.terminate()
            except Exception:
                pass


def inspect_map_only(
    team_num: int = 1,
    player_num: int = 1,
    server: str = DEFAULT_SERVER,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    """
    仅获取游戏地图快照，不加入游戏
    
    返回包含方块、实体等信息的字典
    """
    js_runtime: Any | None = None
    world: World | None = None

    try:
        print("🔧 初始化 JavaScript 桥接...")
        js_runtime, js_bridge = initialize_js_bridge()
        print("✅ JavaScript 桥接初始化成功")

        print(f"🌐 连接到服务器 {server}:{port}...")
        world = World(
            js_bridge=js_bridge,
            team_num=team_num,
            player_num=player_num,
            against_team=None,  # 调试模式，不加入游戏
            server=server,
            port=port,
            verbose=True,
        )

        print("📸 获取地图快照...")
        snapshot = world.inspect()
        
        print(f"✅ 获取成功！")
        print(f"   方块数量: {snapshot['summary']['block_count']}")
        print(f"   实体数量: {snapshot['summary']['entity_count']}")
        print(f"   扫描范围: X({snapshot['bounds']['min_x']}~{snapshot['bounds']['max_x']}), "
              f"Z({snapshot['bounds']['min_z']}~{snapshot['bounds']['max_z']})")
        
        return snapshot

    finally:
        if world is not None:
            world.close()
        if js_runtime is not None:
            try:
                js_runtime.terminate()
            except Exception:
                pass


def connect_bot_with_timeout(
    js_bridge: JavaScriptBridge,
    server: str,
    port: int,
    username: str,
    timeout_seconds: float = 30.0,
    verbose: bool = True,
) -> Any:
    """
    带超时的机器人连接
    
    参数:
        js_bridge: JavaScript 桥接
        server: 服务器地址
        port: 服务器端口
        username: 机器人用户名
        timeout_seconds: 连接超时时间（秒）
        verbose: 是否输出详细日志
    
    返回:
        bot 对象
    
    抛出:
        TimeoutError: 连接超时
        ConnectionError: 连接失败
    """
    import threading
    from javascript import require
    
    require_func = js_bridge.require
    once_func = js_bridge.once
    mineflayer = require_func("mineflayer")
    pathfinder = require_func("mineflayer-pathfinder")
    
    if verbose:
        print(f"   正在连接 {server}:{port} (用户名: {username})...")
    
    # 创建机器人
    try:
        bot = mineflayer.createBot({
            "host": server,
            "port": port,
            "username": username,
            "hideErrors": False,
            "connectTimeout": int(timeout_seconds * 1000),  # 毫秒
        })
    except Exception as exc:
        raise ConnectionError(f"创建机器人失败: {exc}") from exc

    
    return bot


def join_and_get_map(
    team_num: int,
    player_num: int,
    map_mode: str = "fixed",
    server: str = DEFAULT_SERVER,
    port: int = DEFAULT_PORT,
    wait_seconds: float = 1.0,
    connect_timeout: float = 30.0,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    加入游戏对局并获取对局地图
    
    流程:
    1. 连接服务器
    2. 发送加入消息 "with none 1 fixed"
    3. 等待服务器响应并传送到对局地图
    4. 获取对局地图快照
    
    参数:
        team_num: 队伍编号
        player_num: 玩家编号
        map_mode: 地图模式 ("fixed" 或 "random")
        server: 服务器地址
        port: 服务器端口
        wait_seconds: 传送等待时间（秒）
        connect_timeout: 连接超时时间（秒）
        verbose: 是否输出详细日志
    
    返回:
        地图快照字典
    
    抛出:
        TimeoutError: 连接超时
        ConnectionError: 连接失败
    """
    js_runtime: Any | None = None
    
    try:
        # 1. 初始化 JS 桥接
        if verbose:
            print("🔧 初始化 JavaScript 桥接...")
        js_runtime, js_bridge = initialize_js_bridge()
        if verbose:
            print("✅ JavaScript 桥接初始化成功")

        # 2. 计算机器人名称
        bot_name = f"CTF-{team_num}-{player_num}"
        if verbose:
            print(f"🤖 机器人名称: {bot_name}")

        # 3. 连接服务器（带超时）
        if verbose:
            print(f"🌐 连接到服务器 {server}:{port}...")
        time.sleep(wait_seconds)
        try:
            bot = connect_bot_with_timeout(
                js_bridge=js_bridge,
                server=server,
                port=port,
                username=bot_name,
                timeout_seconds=connect_timeout,
                verbose=verbose,
            )
        except TimeoutError as exc:
            print(f"\n❌ {exc}")
            raise
        except ConnectionError as exc:
            print(f"\n❌ {exc}")
            raise
        time.sleep(wait_seconds)
        # 4. 发送加入对局的消息
        # 构建意图消息: "with none 1 fixed"
        intent_message = f"with none 1 {map_mode}"
        if verbose:
            print(f"💬 发送加入消息: '{intent_message}'")
        
        try:
            bot.chat(intent_message)
        except Exception as exc:
            print(f"⚠️ 发送消息失败: {exc}")
        
        # 5. 等待服务器传送（默认1秒）
        if verbose:
            print(f"⏳ 等待传送... ({wait_seconds}s)")
        time.sleep(wait_seconds)
        
        # 6. 获取地图快照
        if verbose:
            print("📸 获取对局地图...")
        
        # 使用 lib.world 中的方法直接获取快照
        from lib.world import _build_js_helpers
        
        require = js_bridge.require
        block_to_json, entities_to_json, players_to_json, _, position_to_json, _ = _build_js_helpers(require)
        vec3 = require("vec3")
        
        # 扫描范围（限定范围）
        bounds = {
            "min_x": -24, "max_x": 24,
            "min_y": 0, "max_y": 3,
            "min_z": -36, "max_z": 36
        }
        
        bot_position = json.loads(position_to_json(bot.entity.position))
        
        # 扫描方块
        air_blocks = {"air", "cave_air", "void_air"}
        blocks: list[dict[str, Any]] = []
        for y in range(bounds["min_y"], bounds["max_y"] + 1):
            for z in range(bounds["min_z"], bounds["max_z"] + 1):
                for x in range(bounds["min_x"], bounds["max_x"] + 1):
                    block = bot.blockAt(vec3(x, y, z))
                    raw_block = block_to_json(block)
                    if not raw_block:
                        continue
                    block_data = json.loads(raw_block)
                    if block_data["name"] in air_blocks:
                        continue
                    blocks.append(block_data)
        
        entities = json.loads(entities_to_json(bot.entities))
        
        snapshot = {
            "captured_at": datetime.now().isoformat(),
            "server": {"host": server, "port": port, "username": bot_name},
            "bounds": bounds,
            "plane_y": 1,
            "bot": {
                "position": bot_position,
                "username": bot_name,
                "team": None,
            },
            "summary": {
                "block_count": len(blocks),
                "entity_count": len(entities),
            },
            "blocks": blocks,
            "entities": entities,
        }
        
        if verbose:
            print("✅ 获取成功！")
            print(f"   方块数量: {snapshot['summary']['block_count']}")
            print(f"   实体数量: {snapshot['summary']['entity_count']}")
            print(f"   机器人位置: ({snapshot['bot']['position']['x']:.1f}, "
                  f"{snapshot['bot']['position']['y']:.1f}, "
                  f"{snapshot['bot']['position']['z']:.1f})")
        
        return snapshot

    finally:
        # 关闭 bot
        if 'bot' in dir() and bot is not None:
            try:
                bot.quit()
            except Exception:
                pass
        if js_runtime is not None:
            try:
                js_runtime.terminate()
            except Exception:
                pass


def diagnose_connection(server: str, port: int) -> None:
    """
    诊断服务器连接问题
    """
    import socket
    
    print("\n🔍 连接诊断:")
    print(f"   目标服务器: {server}:{port}")
    
    # 检查是否可以解析主机名
    try:
        ip = socket.getaddrinfo(server, None)[0][4][0]
        print(f"   ✅ 主机名解析成功: {server} -> {ip}")
    except socket.gaierror as e:
        print(f"   ❌ 无法解析主机名: {e}")
        print(f"      请检查服务器地址是否正确")
        return
    
    # 检查 TCP 端口连通性
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((server, port))
        if result == 0:
            print(f"   ✅ TCP 端口 {port} 可连接")
        else:
            print(f"   ❌ TCP 端口 {port} 无法连接 (错误码: {result})")
            print(f"      可能原因:")
            print(f"      - Minecraft 服务器未运行")
            print(f"      - 防火墙阻止连接")
            print(f"      - 服务器地址/端口错误")
        sock.close()
    except Exception as e:
        print(f"   ❌ 网络检查失败: {e}")
    
    # 检查 javascript 模块
    try:
        import javascript
        print(f"   ✅ Python javascript 模块已安装")
    except ImportError:
        print(f"   ❌ Python javascript 模块未安装")
        print(f"      请运行: pip install javascript")
    
    print()


def quick_demo():
    """
    快速演示：连接服务器，获取地图，运行简单策略
    """
    # 配置
    SERVER = "10.31.0.101"  # 修改为你的服务器地址
    PORT = 25565
    TEAM_NUM = 52
    PLAYER_NUM = 1
    AGAINST_TEAM = 53  # 设为 None 进入调试模式（不真正对战）

    print("=" * 50)
    print("CTF 机器人演示")
    print("=" * 50)
    
    # 先运行诊断
    diagnose_connection(SERVER, PORT)

    # 方式 1: 加入对局并获取地图快照
    print("\n📍 方式 1: 加入对局并获取地图\n")
    try:
        snapshot = join_and_get_map(
            team_num=TEAM_NUM,
            player_num=PLAYER_NUM,
            map_mode="fixed",  # 或 "random"
            server=SERVER,
            port=PORT,
            wait_seconds=1.0,  # 等待1秒让服务器传送
            connect_timeout=30.0,  # 连接超时30秒
            verbose=True,
        )
        # 保存地图快照
        output_path = Path("game_map_snapshot.json")
        output_path.write_text(
            json.dumps(snapshot, indent=2), encoding="utf-8"
        )
        print(f"\n📝 地图快照已保存到: {output_path}")
    except TimeoutError as e:
        print(f"\n❌ 连接超时: {e}")
        print("\n💡 建议:")
        print("   1. 确认 Minecraft 服务器正在运行")
        print("   2. 检查服务器地址和端口是否正确")
        print("   3. 检查网络连接和防火墙设置")
        print("   4. 尝试增加 connect_timeout 参数")
    except ConnectionError as e:
        print(f"\n❌ 连接失败: {e}")
    except Exception as e:
        print(f"\n❌ 错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # 方式 2: 完整运行机器人（参与实际对战）
    # print("\n📍 方式 2: 运行完整机器人\n")
    # run_bot(
    #     team_num=TEAM_NUM,
    #     player_num=PLAYER_NUM,
    #     against_team=AGAINST_TEAM,
    #     server=SERVER,
    #     port=PORT,
    #     verbose=True,
    # )


if __name__ == "__main__":
    quick_demo()

# SpringSense-CTF 智能体文档

## 项目概述

SpringSense-CTF 是一个 **Minecraft 夺旗战 (CTF) 机器人框架**，使 AI 智能体能够在 Minecraft 中玩自定义 CTF 游戏模式。该项目提供了一个基于 Python 的接口，通过 Python-JavaScript 桥接使用 Mineflayer JavaScript 库来控制 Minecraft 机器人。

### 游戏规则摘要

CTF 游戏遵循以下核心机制：
- **目标**：夺取敌方旗帜并将其放置在你方半场的金块目标点上
- **胜利条件**：首先占领所有 8 个目标点的队伍获胜；否则，3 分钟后占领点数更多的队伍获胜
- **队伍**：两队 - 左队 (L/红) 和右队 (R/蓝)，通过玩家名称前缀识别
- **夺取**：接近敌方旗帜台以拾取旗帜；将其带到你方空的金块上
- **监狱**：在敌方领土被捕的玩家会被送入监狱；死亡后在你方监狱重生
- **地图**：固定或随机生成的地图，包含树木、哞菇和发光鱿鱼障碍物

## 技术栈

### 核心依赖

| 组件 | 用途 |
|-----------|---------|
| Python 3.12+ | 主要运行时语言 |
| `javascript` Python 包 | 通往 Node.js 的桥接（StPyV8 或类似后端） |
| Mineflayer | 用于 Node.js 的 Minecraft 机器人 API |
| mineflayer-pathfinder | Mineflayer 的路径寻找插件 |
| Pillow (PIL) | 用于可视化的图像渲染 |
| matplotlib | 可选的 2D 地图可视化 |
| Jupyter/IPython | 交互式开发环境 |

### 关键 JavaScript 模块（预加载）

- `mineflayer` - 核心机器人 API
- `mineflayer-pathfinder` - 移动和路径寻找
- `vec3` - 3D 向量工具
- `minecraft-data` - Minecraft 方块/实体数据
- `node:vm` - 辅助函数的 VM 上下文

## 项目结构

```
SpringSense-CTF/
├── main.py                    # 运行机器人的 CLI 入口点
├── lib/                       # 核心框架库
│   ├── __init__.py           # 包导出
│   ├── actions.py            # 动作数据类 (MoveTo, Chat)
│   ├── observation.py        # 游戏状态观测类
│   └── world.py              # 世界连接和机器人生命周期
├── student_strategy.py        # 学生实现（用户可编辑）
├── default_strategy.py        # 参考策略实现
├── student-strategy.py        # 策略重新导出
├── map_to_json.py            # 地图捕获和 ASCII/matplotlib 可视化
├── render.py                 # 基于 PIL 的观测渲染器
├── ctf_mineflayer.ipynb      # 分步教程笔记本（中文）
├── play.ipynb                # 完整游戏执行笔记本
├── README.md                 # 游戏规则和设置指南（中文）
└── logs/                     # 运行时日志和快照
    └── *.jsonl               # 多次游戏记录
```

## 代码组织

### 1. 核心库 (`lib/`)

#### `lib/actions.py`
定义策略的动作接口：
```python
@dataclass(frozen=True, slots=True)
class MoveTo:
    x: int
    z: int
    radius: int = 1      # 到达目标的容差半径
    sprint: bool = True  # 是否疾跑

@dataclass(frozen=True, slots=True)
class Chat:
    message: str
```

#### `lib/observation.py`
全面的游戏状态表示：
- `Observation` - 根状态对象，包含玩家、方块、实体、分数
- `BotState` - 自身机器人状态（位置、队伍）
- `PlayerState` - 其他玩家状态（位置、是否持有旗帜、是否在监狱）
- `BlockState` - 方块信息（金块、旗帜、障碍物）
- `EntityState` - 非玩家实体（哞菇、鱿鱼）
- `GridPosition` / `Vec3` - 坐标工具
- `MapMetadata` - 地图边界和地标

关键观测属性：
- `observation.self_player` - 你的机器人的玩家状态
- `observation.my_targets` - 你方用于放置旗帜的空金块
- `observation.flags_to_capture` - 可偷取的敌方旗帜
- `observation.teammates` / `observation.enemies` - 队伍分区

#### `lib/world.py`
主机器人生命周期和连接管理：
- `World` 类 - 机器人初始化、游戏循环、动作执行
- `JavaScriptBridge` - JS 运行时接口
- `ScanBounds` - 默认地图扫描边界

### 2. 策略接口

策略必须实现此接口：

```python
class MyStrategy:
    def on_game_start(self, obs: Observation) -> None:
        """游戏开始时调用一次。"""
        pass
    
    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat] | None:
        """每 tick 调用以确定机器人动作。"""
        return [MoveTo(x=10, z=5)]
```

### 3. 参考策略 (`default_strategy.py`)

- `RandomWalkStrategy` - 简单的左右巡逻（演示）
- `PickClosestFlagAndBackStrategy` - 贪婪夺旗启发式算法

## 运行机器人

### 方法 1：命令行 (main.py)

```bash
python main.py \
    --my-team 52 \
    --my-no 1 \
    --against none \
    --per-team-player 1 \
    --map fixed \
    --server 10.31.0.101 \
    --port 25565 \
    --strategy student_strategy.RandomWalkStrategy \
    --action-tick 0.1 \
    --snapshot-tick 1.0 \
    --verbose
```

**参数：**
- `--my-team`, `--team-num` (必需) - 你的队伍编号
- `--my-no`, `--player-num` (必需) - 你在队伍中的玩家编号
- `--against` - 对手队伍编号或 "none" 用于调试模式
- `--per-team-player` - 比赛中每队玩家数
- `--map` - "fixed"（固定）或 "random"（随机）地图模式
- `--server`, `--port` - Minecraft 服务器地址
- `--strategy` - 策略类的 Python 路径 (module.ClassName)
- `--action-tick` - 动作计算间隔秒数（默认：0.1）
- `--snapshot-tick` - 日志快照间隔秒数（默认：1.0）
- `--verbose` - 启用详细日志

机器人名称格式为：`CTF-{team_num}-{player_num}`（例如：`CTF-52-1`）

### 方法 2：Jupyter Notebook (play.ipynb)

交互式开发工作流：
1. 在 Config 单元格中配置 `TEAM_NUM`、`PLAYER_NUM`、`AGAINST_TEAM` 等
2. 运行 Utils 单元格初始化 JavaScript 桥接
3. 运行 Run 单元格执行游戏循环并实时显示更新

### 方法 3：分步笔记本 (ctf_mineflayer.ipynb)

学习用教育笔记本：
1. 初始化 Mineflayer 和路径寻找器
2. 使用机器人连接到服务器
3. 通过聊天命令加入游戏
4. 实现移动和夺旗逻辑
5. 处理游戏状态消息

## 开发规范

### 代码风格

- **类型提示**：需要完整的类型注解（Python 3.10+ 语法）
- **数据类**：对值对象使用 `@dataclass(frozen=True, slots=True)`
- **不可变性**：观测对象是不可变的；使用 `patch_observation()` 进行更新
- **命名**：Python 使用 snake_case，与 JavaScript 交互时使用 camelCase

### 策略开发模式

1. 在 `student_strategy.py` 中创建一个类
2. 实现 `on_game_start()` 进行初始化
3. 实现 `compute_next_action()` 返回动作列表
4. 使用 `observation.self_player.has_flag` 检查是否持有旗帜
5. 使用 `observation.my_targets` 查找放置位置
6. 使用 `observation.flags_to_capture` 查找敌方旗帜

示例：
```python
@dataclass
class MyStrategy:
    target: GridPosition | None = None
    
    def on_game_start(self, obs: Observation) -> None:
        self.target = None
    
    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        me = obs.self_player
        
        if me.has_flag:
            # 返回空目标点
            targets = obs.my_targets
            if targets:
                target = targets[0].grid_position
                return [MoveTo(x=target.x, z=target.z, radius=0)]
        else:
            # 前往敌方旗帜
            flags = obs.flags_to_capture
            if flags:
                target = flags[0].grid_position
                return [MoveTo(x=target.x, z=target.z, radius=0)]
        
        return [MoveTo(x=obs.me.position.x, z=obs.me.position.z)]
```

### 重要实现注意事项

1. **路径寻找器限制**：内置路径寻找器会避开所有实体，包括被动的哞菇，当哞菇追逐机器人时可能导致死锁。机器人**可以**穿过哞菇 - 如需自定义避障请自行实现。

2. **动作 Tick 频率**：默认 0.1 秒（10Hz）用于动作计算；快照日志记录为 1Hz。平衡响应性与 CPU 使用率。

3. **坐标系统**：
   - X < 0：左队 (L/红) 领地
   - X > 0：右队 (R/蓝) 领地
   - Y = 1：默认游戏平面
   - 监狱区域：L 在 (-18..-14, 26..30)，R 在 (14..18, 26..30)

4. **队伍识别**：队伍由服务器在游戏开始时分配；不要仅根据名称前缀假设。

## 测试与调试

### 日志文件

日志写入 `logs/` 目录：
- `{timestamp}-CTF-{team}-{player}-multi-shot.jsonl` - 包含状态和动作的游戏时间线
- `{timestamp}-CTF-{team}-{player}-final-shot.json` - 最终观测快照

日志格式 (JSONL)：
```json
{"event": "session_start", "timestamp": 1234567890, ...}
{"timestamp": 1234567891, "me": {...}, "players": [...], "actions": [...]}
{"event": "session_end", ...}
```

### 可视化工具

1. **map_to_json.py** - 捕获和查看地图状态：
   ```bash
   python map_to_json.py --host 10.31.0.101 --plot
   ```
   输出 ASCII 地图和可选的 matplotlib 可视化。

2. **render.py** - 将观测渲染为图像/GIF：
   ```bash
   # 单帧
   python render.py --input logs/final-shot.json --output map.png
   
   # 从会话日志生成动画 GIF
   python render.py --gif --input logs/multi-shot.jsonl --output replay.gif
   ```

3. **Jupyter 显示** - play.ipynb 在游戏过程中显示实时地图更新

### 常见问题

| 问题 | 解决方案 |
|-------|----------|
| 路径寻找器卡住 | 机器人可以穿过哞菇；如需自定义移动请自行实现 |
| 连接被拒绝 | 检查服务器 IP/端口；验证 Minecraft 服务器是否正在运行 |
| JavaScript 桥接错误 | 重启 Python 内核；确保 `javascript` 包已安装 |
| 队伍分配失败 | 在第一次观测前等待 "Game start:" 消息 |
| 模块未找到 | 确保所有依赖已安装；检查 Python 路径 |

## 安全注意事项

1. **服务器连接**：仅连接到受信任的 Minecraft 服务器。机器人执行来自服务器的 JavaScript 代码（通过 mineflayer 协议）。

2. **代码注入**：`javascript` 桥接在 Node.js VM 上下文中评估代码。对动态策略加载保持谨慎。

3. **日志文件**：游戏日志包含位置数据和用户名。分享前请检查。

4. **网络**：默认服务器地址 `10.31.0.101:25565` 是本地网络地址。适当配置防火墙规则。

## 依赖安装

该项目需要 `javascript` Python 包（通常使用 StPyV8 或 PyMiniRacer）和带有 Mineflayer 的 Node.js。

标准设置（可能因平台而异）：
```bash
# 安装 Python 依赖
pip install javascript pillow matplotlib

# 安装 Node.js 依赖（由 javascript 包处理）
# 或手动安装：
npm install mineflayer mineflayer-pathfinder vec3 minecraft-data
```

## 语言说明

- 源代码和文档主要使用**英文**
- 面向用户的文档（README.md、ctf_mineflayer.ipynb）为目标教育受众使用**简体中文**
- 代码中的注释使用英文
- 错误消息使用英文

修改代码时，代码/注释保持英文。如果用户面向的行为发生重大变化，请更新中文文档。

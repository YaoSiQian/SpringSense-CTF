from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState

if TYPE_CHECKING:
    from lib.actions import Action

# =============================================================================
# 常量定义
# =============================================================================

# 监狱压力板位置（踩下可立即开门解救队友）
PRISON_PRESSURE_PLATE: dict[str, GridPosition] = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}

# 监狱危险区域 Z 坐标（z=28 的压力板会重置60秒计时器）
PRISON_DANGER_ZONE_Z = 28

# 中场控制点
MIDFIELD_ANCHOR: dict[str, GridPosition] = {
    "L": GridPosition(x=-6, z=0),
    "R": GridPosition(x=6, z=0),
}

# 监狱逃脱目标（z < 26 即算逃脱）
PRISON_EXIT_TARGET: dict[str, GridPosition] = {
    "L": GridPosition(x=-16, z=25),
    "R": GridPosition(x=16, z=25),
}

# 决策阈值
STUCK_THRESHOLD_TICKS = 4
RESCUE_MAX_DISTANCE = 25
OBJECTIVE_HOLD_TICKS_MAX = 10
ROLE_SWITCH_COOLDOWN = 5


# =============================================================================
# 枚举与数据结构
# =============================================================================

class Role(Enum):
    """角色类型"""
    ATTACKER = auto()   # 进攻手：专注夺旗
    DEFENDER = auto()   # 防守手：拦截携旗敌人
    SUPPORT = auto()    # 支援手：救援、中场控制


class State(Enum):
    """状态机状态"""
    IDLE = auto()
    ESCAPING = auto()
    CARRYING = auto()
    INTERCEPTING = auto()
    CAPTURING = auto()
    RESCUING = auto()
    DEFENDING = auto()


@dataclass(frozen=True)
class Objective:
    """目标对象"""
    label: str
    target: GridPosition
    radius: int
    sprint: bool
    priority: int = 0


# =============================================================================
# EliteCTFStrategy - 精英夺旗策略
# =============================================================================

@dataclass
class EliteCTFStrategy:
    """
    高级自适应夺旗策略
    
    核心特性：
    1. 动态角色分配（进攻/防守/支援）
    2. 智能救援系统（主动踩压力板）
    3. 预测性拦截（计算敌方返回路径）
    4. 安全夺旗评估（距离+安全性）
    5. 团队协作（信息共享）
    6. 快速脱困（卡位检测与逃脱）
    """
    
    # 角色与状态
    role: Role = field(default_factory=lambda: Role.ATTACKER)
    state: State = field(default_factory=lambda: State.IDLE)
    
    # 目标管理
    current_objective: Objective | None = None
    objective_hold_ticks: int = 0
    last_declared_intent: tuple[str, int, int] | None = None
    
    # 卡位检测
    last_position: GridPosition | None = None
    stuck_ticks: int = 0
    
    # 角色切换冷却
    role_switch_cooldown: int = 0
    
    # 持旗确认计数
    return_home_ticks: int = 0
    
    # 随机数生成器
    rng: random.Random = field(default_factory=random.Random)
    
    # 调试模式
    verbose: bool = True

    def on_game_start(self, obs: Observation) -> None:
        """游戏开始时初始化"""
        self.role = Role.ATTACKER
        self.state = State.IDLE
        self.current_objective = None
        self.objective_hold_ticks = 0
        self.last_declared_intent = None
        self.last_position = obs.me.position
        self.stuck_ticks = 0
        self.role_switch_cooldown = 0
        self.return_home_ticks = 0

    def compute_next_action(self, obs: Observation) -> list[Action]:
        """
        主决策函数 - 每 tick 调用
        
        决策优先级：
        1. 越狱逃脱（最高优先级）
        2. 卡位脱困
        3. 持旗返回
        4. 救援队友
        5. 拦截携旗敌人
        6. 夺旗
        7. 防守/中场控制
        """
        me = obs.self_player
        actions: list[Action] = []
        
        # 1. 检查卡位并尝试脱困
        escape_action = self._try_escape_if_stuck(obs)
        if escape_action is not None:
            return escape_action
        
        # 2. 监狱逃脱（最高优先级）
        if me.in_prison:
            return self._escape_prison(obs)
        
        # 3. 动态角色分配（带冷却）
        if self.role_switch_cooldown <= 0:
            self._update_role(obs)
        else:
            self.role_switch_cooldown -= 1
        
        # 4. 状态机决策
        if me.has_flag:
            self.state = State.CARRYING
            return self._return_flag(obs)
        
        # 5. 检查是否需要救援
        if self._should_rescue(obs):
            return self._rescue_teammate(obs)
        
        # 6. 拦截携旗敌人
        enemy_carriers = [e for e in obs.enemies if e.has_flag]
        if enemy_carriers:
            return self._intercept_enemy(obs, enemy_carriers[0])
        
        # 7. 夺旗或防守
        if self.role == Role.ATTACKER:
            return self._capture_flag(obs)
        elif self.role == Role.DEFENDER:
            return self._defend_base(obs)
        else:  # SUPPORT
            return self._control_midfield(obs)

    # =========================================================================
    # 角色分配
    # =========================================================================

    def _update_role(self, obs: Observation) -> None:
        """
        动态角色分配逻辑
        
        策略：
        - 敌方有人持旗 → 优先 DEFENDER
        - 多人被困 → 优先 SUPPORT
        - 其他情况 → ATTACKER
        """
        me = obs.self_player
        
        # 计算各类需求
        enemy_carriers = sum(1 for e in obs.enemies if e.has_flag)
        jailed_teammates = sum(1 for p in obs.teammates if p.in_prison)
        free_teammates = len(obs.teammates) - jailed_teammates + 1  # +1 包括自己
        
        new_role = self.role
        
        # 高优先级：敌方持旗 → 需要防守
        if enemy_carriers > 0:
            # 如果自己在敌方半场，不适合防守，保持进攻
            if _is_on_our_side(me.position, obs.team):
                new_role = Role.DEFENDER
        
        # 中优先级：多人被困 → 需要支援
        elif jailed_teammates >= 2 and free_teammates <= 2:
            new_role = Role.SUPPORT
        
        # 默认：进攻
        else:
            new_role = Role.ATTACKER
        
        # 角色切换时重置冷却
        if new_role != self.role:
            self.role = new_role
            self.role_switch_cooldown = ROLE_SWITCH_COOLDOWN
            if self.verbose:
                print(f"[EliteCTF] Role changed to {self.role.name}")

    # =========================================================================
    # 监狱逃脱
    # =========================================================================

    def _escape_prison(self, obs: Observation) -> list[Action]:
        """
        越狱策略：
        1. 首先远离危险压力板（z=28）
        2. 向 z=25 移动（逃脱判定线）
        3. 等待门开后离开
        """
        me = obs.self_player
        actions: list[Action] = []
        
        # 如果还在危险区域（z >= 28），先远离压力板
        if me.position.z >= PRISON_DANGER_ZONE_Z - 1:
            # 向负 Z 方向移动（北方），远离压力板
            safe_target = GridPosition(x=me.position.x, z=26)
            actions.append(self._create_move(safe_target, "Escaping danger zone", radius=0))
            return actions
        
        # 向监狱出口移动（z=25）
        exit_target = PRISON_EXIT_TARGET[obs.team]
        actions.append(self._create_move(exit_target, "Escaping prison", radius=0))
        
        return actions

    # =========================================================================
    # 持旗返回
    # =========================================================================

    def _return_flag(self, obs: Observation) -> list[Action]:
        """
        持旗返回策略：
        - 选择最近的空金块
        - 避开敌人
        - 紧急模式：radius=0, sprint=True
        """
        me = obs.self_player
        self.return_home_ticks += 1
        
        targets = obs.my_targets
        if not targets:
            # 没有目标点，保持原地或去安全位置
            safe_pos = _get_safe_position(obs)
            return [self._create_move(safe_pos, "No target, holding", radius=1)]
        
        # 选择最近的目标点
        target = min(targets, key=lambda t: _manhattan_distance(me.position, t.grid_position))
        target_pos = target.grid_position
        
        # 检查是否需要调整路径以避开敌人
        safe_target = self._find_safe_path_target(me.position, target_pos, obs)
        
        return [self._create_move(safe_target, "Returning flag", radius=0, sprint=True)]

    # =========================================================================
    # 救援系统
    # =========================================================================

    def _should_rescue(self, obs: Observation) -> bool:
        """
        判断是否应该去救援队友
        
        高优先级：
        - 持旗队友被困
        - 多个队友被困导致战力不足
        
        低优先级：
        - 自己在监狱附近，顺路救援
        """
        jailed_teammates = [p for p in obs.teammates if p.in_prison]
        if not jailed_teammates:
            return False
        
        me = obs.self_player
        
        # 高优先级：持旗队友被困
        flag_carriers_jailed = [p for p in jailed_teammates if p.has_flag]
        if flag_carriers_jailed:
            # 检查距离，不要跑太远
            closest_carrier = min(flag_carriers_jailed, 
                                 key=lambda p: _manhattan_distance(me.position, p.position))
            if _manhattan_distance(me.position, closest_carrier.position) <= RESCUE_MAX_DISTANCE:
                return True
        
        # 中优先级：战力不足（多个队友被困）
        free_teammates = [p for p in obs.teammates if not p.in_prison]
        if len(jailed_teammates) >= 2 and len(free_teammates) <= 1:
            return True
        
        # 低优先级：顺路救援（在监狱附近）
        prison_plate = PRISON_PRESSURE_PLATE[obs.team]
        if _manhattan_distance(me.position, prison_plate) <= 10:
            return True
        
        return False

    def _rescue_teammate(self, obs: Observation) -> list[Action]:
        """
        执行救援：走向监狱门外的压力板
        踩下压力板可立即打开监狱门
        """
        prison_plate = PRISON_PRESSURE_PLATE[obs.team]
        print(f"[EliteCTF] RESCUE {prison_plate.x},{prison_plate.z}")
        return [
            self._create_move(prison_plate, "Rescuing teammate", radius=0, sprint=True)
        ]

    # =========================================================================
    # 拦截系统
    # =========================================================================

    def _intercept_enemy(self, obs: Observation, enemy: PlayerState) -> list[Action]:
        """
        拦截携旗敌人
        
        策略：
        1. 预测敌方返回路径
        2. 在敌方路径上选择拦截点
        3. 优先在己方半场拦截（安全）
        """
        intercept_point = self._predict_intercept_point(enemy, obs)
        
        print(f"[EliteCTF] INTERCEPT {intercept_point.x},{intercept_point.z}")
        return [
            self._create_move(intercept_point, "Intercepting carrier", radius=1, sprint=True)
        ]

    def _predict_intercept_point(self, enemy: PlayerState, obs: Observation) -> GridPosition:
        """
        预测最佳拦截点
        
        简单预测：敌方当前位置和我方半场的中间点
        优先选择己方半场的边界位置
        """
        enemy_pos = enemy.position
        
        # 如果敌方已经在己方半场，直接追击
        if _is_on_our_side(enemy_pos, obs.team):
            return enemy_pos
        
        # 计算中场拦截点
        # 目标是敌我之间的连线，偏向己方一侧
        my_side_x = -8 if obs.team == "L" else 8
        
        # 简单线性插值：从敌方位置到己方半场
        # 拦截点 = 敌方位置 + (己方半场 - 敌方位置) * 0.6
        dx = my_side_x - enemy_pos.x
        intercept_x = int(enemy_pos.x + dx * 0.6)
        intercept_z = enemy_pos.z  # 保持相同的 Z
        
        intercept = GridPosition(x=intercept_x, z=intercept_z)
        
        # 确保在地图范围内
        intercept = _clamp_to_map(intercept, obs)
        
        # 如果拦截点还在敌方半场，退回到边界
        if not _is_on_our_side(intercept, obs.team):
            boundary_x = -2 if obs.team == "L" else 2
            intercept = GridPosition(x=boundary_x, z=enemy_pos.z)
        
        return intercept

    # =========================================================================
    # 夺旗系统
    # =========================================================================

    def _capture_flag(self, obs: Observation) -> list[Action]:
        """
        夺旗策略：选择最优旗帜目标
        
        评估因素：
        - 距离
        - 返回路径的安全性
        - 敌方防守压力
        """
        flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        
        if not flags:
            # 没有可夺旗帜，转为防守
            return self._defend_base(obs)
        
        # 选择最优旗帜
        target_flag = self._pick_best_flag(obs, flags)
        target_pos = target_flag.grid_position
        
        print(f"[EliteCTF] CAPTURE {target_pos.x},{target_pos.z}")
        return [
            self._create_move(target_pos, "Attacking flag", radius=1, sprint=True)
        ]

    def _pick_best_flag(self, obs: Observation, flags: tuple[BlockState, ...]) -> BlockState:
        """
        选择最优夺旗目标
        
        评分 = 距离成本 + 安全成本
        - 距离成本：当前位置到旗帜的距离
        - 安全成本：夺旗后返回的风险评估
        """
        me = obs.self_player
        
        def flag_score(flag: BlockState) -> float:
            distance = _manhattan_distance(me.position, flag.grid_position)
            safety_penalty = self._evaluate_flag_safety(flag, obs)
            return distance + safety_penalty
        
        return min(flags, key=flag_score)

    def _evaluate_flag_safety(self, flag: BlockState, obs: Observation) -> float:
        """
        评估夺旗后返回的安全性
        
        越低越安全
        """
        flag_pos = flag.grid_position
        
        # 到最近目标点的距离
        targets = obs.my_targets
        if not targets:
            return float('inf')
        
        min_return_distance = min(
            _manhattan_distance(flag_pos, t.grid_position) 
            for t in targets
        )
        
        # 旗帜附近的敌人数量（敌方防守力量）
        nearby_enemies = sum(
            1 for e in obs.enemies
            if _manhattan_distance(e.position, flag_pos) <= 8
        )
        
        # 返回路径上的敌人（己方半场）
        path_enemies = sum(
            1 for e in obs.enemies
            if _is_on_our_side(e.position, obs.team)
            and _manhattan_distance(e.position, flag_pos) <= 15
        )
        
        # 综合评分
        return min_return_distance * 0.5 + nearby_enemies * 4 + path_enemies * 2

    # =========================================================================
    # 防守与中场控制
    # =========================================================================

    def _defend_base(self, obs: Observation) -> list[Action]:
        """
        基地防守：在己方金块群附近巡逻
        """
        # 选择己方半场的一个防守位置
        if obs.my_targets:
            # 在空金块附近巡逻
            target = obs.my_targets[len(obs.my_targets) // 2]
            defend_pos = target.grid_position
        else:
            # 默认防守位置
            defend_pos = MIDFIELD_ANCHOR[obs.team]
        
        return [self._create_move(defend_pos, "Defending base", radius=2, sprint=False)]

    def _control_midfield(self, obs: Observation) -> list[Action]:
        """
        中场控制：占据有利位置
        """
        midfield = MIDFIELD_ANCHOR[obs.team]
        return [self._create_move(midfield, "Holding midfield", radius=2, sprint=False)]

    # =========================================================================
    # 卡位检测与脱困
    # =========================================================================

    def _try_escape_if_stuck(self, obs: Observation) -> list[Action] | None:
        """
        检测卡位并尝试脱困
        
        返回 None 表示没有卡位
        返回 Action 列表表示执行脱困动作
        """
        me = obs.self_player
        
        # 更新卡位计数
        if self.last_position is None:
            self.last_position = me.position
            self.stuck_ticks = 0
            return None
        
        if _same_grid_position(self.last_position, me.position):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
            self.last_position = me.position
        
        if self.stuck_ticks < STUCK_THRESHOLD_TICKS:
            return None
        
        # 检测到卡位，执行脱困
        return self._escape_from_stuck(obs)

    def _escape_from_stuck(self, obs: Observation) -> list[Action]:
        """
        脱困策略：向远离障碍物的方向移动
        """
        me = obs.self_player
        
        # 检测附近的动物
        nearby_animals = [
            e for e in obs.entities 
            if e.entity_type == "animal"
            and _manhattan_distance(e.grid_position, me.position) <= 4
        ]
        
        # 检测附近的硬障碍物
        nearby_obstacles = [
            b for b in obs.blocks
            if _is_hard_block_name(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 2
        ]
        
        if not nearby_animals and not nearby_obstacles:
            # 没有明显障碍，随机选择一个方向
            directions = [(3, 0), (-3, 0), (0, 3), (0, -3), (2, 2), (-2, 2), (2, -2), (-2, -2)]
            dx, dz = self.rng.choice(directions)
            target = GridPosition(x=me.position.x + dx, z=me.position.z + dz)
        else:
            # 计算逃离向量（远离动物和障碍物的方向）
            escape_x, escape_z = 0, 0
            
            for animal in nearby_animals:
                dx = me.position.x - animal.grid_position.x
                dz = me.position.z - animal.grid_position.z
                escape_x += dx
                escape_z += dz
            
            for obstacle in nearby_obstacles:
                dx = me.position.x - obstacle.grid_position.x
                dz = me.position.z - obstacle.grid_position.z
                escape_x += dx * 2  # 障碍物权重更高
                escape_z += dz * 2
            
            # 归一化并扩展
            if escape_x != 0 or escape_z != 0:
                target = GridPosition(
                    x=me.position.x + (5 if escape_x > 0 else -5),
                    z=me.position.z + (5 if escape_z > 0 else -5)
                )
            else:
                target = GridPosition(x=me.position.x + 5, z=me.position.z)
        
        # 确保目标在地图范围内
        target = _clamp_to_map(target, obs)
        
        print("[EliteCTF] Breaking free from stuck")
        return [
            MoveTo(x=target.x, z=target.z, radius=0, sprint=True)
        ]

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _create_move(
        self, 
        target: GridPosition, 
        label: str, 
        radius: int = 1, 
        sprint: bool = True
    ) -> MoveTo:
        """创建移动动作"""
        return MoveTo(x=target.x, z=target.z, radius=radius, sprint=sprint)

    def _find_safe_path_target(
        self, 
        from_pos: GridPosition, 
        to_pos: GridPosition, 
        obs: Observation
    ) -> GridPosition:
        """
        寻找安全的行走路径目标
        
        简化实现：如果直线路径上有敌人，尝试绕道
        """
        # 检查直线路径上是否有敌人
        enemies_on_path = [
            e for e in obs.enemies
            if _is_on_line_segment(from_pos, to_pos, e.position, tolerance=3)
        ]
        
        if not enemies_on_path:
            return to_pos
        
        # 有敌人，尝试绕道（简化：向 Z 方向偏移）
        offset = 5 if from_pos.z < to_pos.z else -5
        alternative = GridPosition(x=(from_pos.x + to_pos.x) // 2, z=to_pos.z + offset)
        return _clamp_to_map(alternative, obs)


# =============================================================================
# 工具函数
# =============================================================================

def _manhattan_distance(left: GridPosition, right: GridPosition) -> int:
    """曼哈顿距离"""
    return abs(left.x - right.x) + abs(left.z - right.z)


def _same_grid_position(left: GridPosition, right: GridPosition) -> bool:
    """判断两个位置是否相同"""
    return left.x == right.x and left.z == right.z


def _is_on_our_side(position: GridPosition, team: str) -> bool:
    """判断位置是否在己方半场"""
    return position.x <= 0 if team == "L" else position.x >= 0


def _is_on_enemy_side(position: GridPosition, team: str) -> bool:
    """判断位置是否在敌方半场"""
    return not _is_on_our_side(position, team)


def _clamp_to_map(position: GridPosition, obs: Observation) -> GridPosition:
    """将位置限制在地图范围内"""
    return GridPosition(
        x=max(obs.map.min_x, min(obs.map.max_x, position.x)),
        z=max(obs.map.min_z, min(obs.map.max_z, position.z)),
    )


def _get_safe_position(obs: Observation) -> GridPosition:
    """获取安全位置（己方半场深处）"""
    if obs.team == "L":
        return GridPosition(x=-15, z=0)
    else:
        return GridPosition(x=15, z=0)


def _is_hard_block_name(name: str) -> bool:
    """判断是否为硬障碍物"""
    hard_tokens = ("log", "leaves", "fence", "wall", "gate", "glass", "banner")
    return any(token in name.lower() for token in hard_tokens)


def _is_on_line_segment(
    start: GridPosition, 
    end: GridPosition, 
    point: GridPosition, 
    tolerance: int = 2
) -> bool:
    """
    判断点是否在线段附近
    
    简化实现：检查点是否在起点和终点的包围盒内，且距离线段较近
    """
    # 包围盒检查
    min_x, max_x = min(start.x, end.x), max(start.x, end.x)
    min_z, max_z = min(start.z, end.z), max(start.z, end.z)
    
    if not (min_x - tolerance <= point.x <= max_x + tolerance):
        return False
    if not (min_z - tolerance <= point.z <= max_z + tolerance):
        return False
    
    # 距离线段检查（简化：曼哈顿距离到线段的近似）
    # 点到起点的距离 + 点到终点的距离 ≈ 线段长度
    segment_len = _manhattan_distance(start, end)
    point_to_start = _manhattan_distance(point, start)
    point_to_end = _manhattan_distance(point, end)
    
    # 如果点到两端距离之和接近线段长度，则点在线段附近
    return abs(point_to_start + point_to_end - segment_len) <= tolerance * 2


def _unplaced_flags(
    flags: tuple[BlockState, ...],
    gold_block_positions: tuple[GridPosition, ...],
) -> tuple[BlockState, ...]:
    """过滤掉已经被放置的旗帜"""
    gold_positions = {(pos.x, pos.z) for pos in gold_block_positions}
    return tuple(
        flag for flag in flags
        if (flag.grid_position.x, flag.grid_position.z) not in gold_positions
    )


# =============================================================================
# AdaptiveCTFStrategy - 原有策略（保留用于对比测试）
# =============================================================================

# AdaptiveCTFStrategy 使用的辅助函数


def _pick_best_flag_target(obs: Observation, me: PlayerState) -> BlockState | None:
    """选择最优的夺旗目标"""
    flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
    if not flags:
        return None

    enemy_carrier = any(enemy.has_flag for enemy in obs.enemies)
    return min(
        flags,
        key=lambda flag: (
            _manhattan_distance(me.position, flag.grid_position) + _flag_pressure_penalty(obs, flag),
            _distance_to_center(flag.grid_position),
            1 if enemy_carrier else 0,
        ),
    )


def _flag_pressure_penalty(obs: Observation, flag: BlockState) -> int:
    """计算夺旗压力惩罚值"""
    flag_pos = flag.grid_position
    nearby_enemies = sum(
        1 for enemy in obs.enemies if _manhattan_distance(enemy.position, flag_pos) <= 6
    )
    nearby_teammates = sum(
        1 for teammate in obs.teammates if _manhattan_distance(teammate.position, flag_pos) <= 6
    )
    return nearby_enemies * 4 - nearby_teammates * 2


def _closest_enemy_flag_runner(obs: Observation, origin: GridPosition) -> PlayerState | None:
    """找到最近的携旗敌人"""
    carriers = [enemy for enemy in obs.enemies if enemy.has_flag]
    if not carriers:
        return None
    return min(
        carriers,
        key=lambda enemy: (
            _manhattan_distance(origin, enemy.position),
            _distance_to_our_side(enemy.position, obs.team),
        ),
    )


def _closest_jailed_teammate(obs: Observation, origin: GridPosition) -> PlayerState | None:
    """找到最近的被困队友"""
    jailed = [player for player in obs.teammates if player.in_prison]
    if not jailed:
        return None
    return min(jailed, key=lambda player: _manhattan_distance(origin, player.position))


def _should_intercept_flag_runner(obs: Observation, me: PlayerState) -> bool:
    """判断是否应该拦截携旗敌人"""
    if me.has_flag or me.in_prison:
        return False
    active_teammates = [player for player in obs.teammates if not player.in_prison]
    if not active_teammates:
        return True
    defenders_closer_than_me = sum(
        1
        for teammate in active_teammates
        if _distance_to_our_side(teammate.position, obs.team) < _distance_to_our_side(me.position, obs.team)
    )
    return defenders_closer_than_me == 0 or _is_on_our_side(me.position, obs.team)


def _should_rescue_adaptive(obs: Observation, me: PlayerState) -> bool:
    """AdaptiveCTFStrategy 使用的救援判断"""
    if me.has_flag:
        return False
    if any(enemy.has_flag for enemy in obs.enemies) and _is_on_our_side(me.position, obs.team):
        return False
    free_teammates = [player for player in obs.teammates if not player.in_prison]
    return len(free_teammates) <= 1 or _is_on_enemy_side(me.position, obs.team)


def _best_exit_point_adaptive(obs: Observation, position: GridPosition) -> GridPosition:
    """AdaptiveCTFStrategy 使用的越狱出口"""
    CENTER_LANE = GridPosition(x=0, z=0)
    LEFT_STAGING = GridPosition(x=-10, z=0)
    RIGHT_STAGING = GridPosition(x=10, z=0)
    if position.x < 0:
        staging = LEFT_STAGING
    elif position.x > 0:
        staging = RIGHT_STAGING
    else:
        staging = CENTER_LANE
    return _clamp_to_map(staging, obs)


def _best_midfield_anchor_adaptive(obs: Observation) -> GridPosition:
    """AdaptiveCTFStrategy 使用的中场控制点"""
    bias = -6 if obs.team == "L" else 6
    return _clamp_to_map(GridPosition(x=bias, z=0), obs)


def _pick_closest_block(origin: GridPosition, blocks: tuple[BlockState, ...]) -> BlockState | None:
    """选择最近的方块"""
    if not blocks:
        return None
    return min(
        blocks,
        key=lambda block: (
            _manhattan_distance(origin, block.grid_position),
            _distance_to_center(block.grid_position),
        ),
    )


def _distance_to_our_side(position: GridPosition, team: str) -> int:
    """计算到己方半场的距离"""
    if team == "L":
        return max(0, position.x)
    return max(0, -position.x)


def _distance_to_center(position: GridPosition) -> int:
    """计算到地图中心的曼哈顿距离"""
    return abs(position.x) + abs(position.z)


def _is_near_adaptive(x: int, z: int, target: tuple[int, int], threshold: int = 2) -> bool:
    """判断是否接近目标点"""
    return abs(x - target[0]) <= threshold and abs(z - target[1]) <= threshold


def _is_high_priority_label(label: str) -> bool:
    """判断是否为高优先级任务"""
    return label in {"Escaping prison", "Returning flag", "Intercepting carrier"}


def _is_objective_complete(me: PlayerState, objective) -> bool:
    """判断目标是否已完成"""
    return _manhattan_distance(me.position, objective.target) <= max(1, objective.radius + 1)


def _needs_escape_maneuver(
    obs: Observation,
    me: PlayerState,
    stuck_ticks: int,
    stuck_threshold_ticks: int,
) -> bool:
    """判断是否需要执行逃脱机动"""
    if stuck_ticks < stuck_threshold_ticks:
        return False
    return bool(_nearby_animals(obs, me.position) or _nearby_obstacles_adaptive(obs, me.position))


def _best_escape_target_adaptive(obs: Observation, me: PlayerState) -> GridPosition:
    """计算最佳逃脱目标"""
    candidates = [
        GridPosition(x=me.position.x + 3, z=me.position.z),
        GridPosition(x=me.position.x - 3, z=me.position.z),
        GridPosition(x=me.position.x, z=me.position.z + 3),
        GridPosition(x=me.position.x, z=me.position.z - 3),
        GridPosition(x=me.position.x + 2, z=me.position.z + 2),
        GridPosition(x=me.position.x - 2, z=me.position.z + 2),
        GridPosition(x=me.position.x + 2, z=me.position.z - 2),
        GridPosition(x=me.position.x - 2, z=me.position.z - 2),
    ]
    safe_candidates = [
        _clamp_to_map(candidate, obs)
        for candidate in candidates
        if not _is_hard_blocked(obs, candidate)
    ]
    if not safe_candidates:
        return _best_midfield_anchor_adaptive(obs)
    return max(
        safe_candidates,
        key=lambda position: (
            _escape_clearance_score(obs, position),
            _distance_to_center(position),
        ),
    )


def _escape_clearance_score(obs: Observation, position: GridPosition) -> int:
    """计算逃脱位置的清晰度评分"""
    animal_distance = min(
        (_manhattan_distance(position, animal.grid_position) for animal in _nearby_animals(obs, position)),
        default=8,
    )
    obstacle_penalty = len(_nearby_obstacles_adaptive(obs, position))
    return animal_distance * 3 - obstacle_penalty * 2


def _nearby_animals(obs: Observation, position: GridPosition) -> tuple:
    """获取附近的动物"""
    return tuple(
        entity
        for entity in obs.entities
        if entity.entity_type == "animal"
        and _manhattan_distance(position, entity.grid_position) <= 4
    )


def _nearby_obstacles_adaptive(obs: Observation, position: GridPosition) -> tuple[BlockState, ...]:
    """获取附近的障碍物"""
    return tuple(
        block
        for block in obs.blocks
        if _is_hard_block_name(block.name)
        and _manhattan_distance(position, block.grid_position) <= 2
    )


def _is_hard_blocked(obs: Observation, position: GridPosition) -> bool:
    """判断位置是否被硬障碍物阻挡"""
    return any(
        _same_grid_position(block.grid_position, position) and _is_hard_block_name(block.name)
        for block in obs.blocks
    )


# AdaptiveCTFStrategy 类


@dataclass(frozen=True)
class _Objective:
    """AdaptiveCTFStrategy 使用的目标对象"""
    label: str
    target: GridPosition
    radius: int
    sprint: bool


@dataclass
class AdaptiveCTFStrategy:
    """自适应夺旗策略 - 原有的参考策略"""
    radius: int = 1
    last_declared_intent: tuple[str, int, int] | None = None
    current_objective: _Objective | None = None
    objective_hold_ticks: int = 0
    objective_max_hold_ticks: int = 12
    return_home_ticks: int = 0
    return_home_confirm_ticks: int = 2
    last_position: GridPosition | None = None
    stuck_ticks: int = 0
    stuck_threshold_ticks: int = 6

    def on_game_start(self, obs: Observation) -> None:
        self.last_declared_intent = None
        self.current_objective = None
        self.objective_hold_ticks = 0
        self.return_home_ticks = 0
        self.last_position = obs.self_player.position
        self.stuck_ticks = 0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        me = obs.self_player
        objective = self._choose_objective(obs, me)
        target = objective.target
        declared_intent = (objective.label, target.x, target.z)

        actions: list[MoveTo | Chat] = []
        if declared_intent != self.last_declared_intent:
            print(f"[AdaptiveCTF] {objective.label} at ({target.x}, {target.z})")
            self.last_declared_intent = declared_intent

        actions.append(
            MoveTo(
                x=target.x,
                z=target.z,
                radius=objective.radius,
                sprint=objective.sprint,
            )
        )
        return actions

    def _choose_objective(self, obs: Observation, me: PlayerState) -> "_Objective":
        self._update_stuck_state(me)

        if me.has_flag:
            self.return_home_ticks += 1
        else:
            self.return_home_ticks = 0

        objective = self._pick_fresh_objective(obs, me)
        if self._should_keep_current_objective(obs, me, objective):
            assert self.current_objective is not None
            self.objective_hold_ticks += 1
            return self.current_objective

        self.current_objective = objective
        self.objective_hold_ticks = 0
        return objective

    def _update_stuck_state(self, me: PlayerState) -> None:
        if self.last_position is None:
            self.last_position = me.position
            self.stuck_ticks = 0
            return

        if _same_grid_position(self.last_position, me.position):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
            self.last_position = me.position

    def _pick_fresh_objective(self, obs: Observation, me: PlayerState) -> "_Objective":
        if me.in_prison:
            return _Objective("Escaping prison", _best_exit_point_adaptive(obs, me.position), radius=1, sprint=True)

        if _needs_escape_maneuver(obs, me, self.stuck_ticks, self.stuck_threshold_ticks):
            return _Objective("Breaking free", _best_escape_target_adaptive(obs, me), radius=0, sprint=True)

        if self.return_home_ticks >= self.return_home_confirm_ticks:
            target_block = _pick_closest_block(me.position, obs.my_targets)
            if target_block is not None:
                return _Objective("Returning flag", target_block.grid_position, radius=0, sprint=True)

        enemy_flag_runner = _closest_enemy_flag_runner(obs, me.position)
        if enemy_flag_runner is not None and _should_intercept_flag_runner(obs, me):
            return _Objective("Intercepting carrier", enemy_flag_runner.position, radius=1, sprint=True)

        jailed_teammate = _closest_jailed_teammate(obs, me.position)
        if jailed_teammate is not None and _should_rescue_adaptive(obs, me):
            rescue_target = _best_exit_point_adaptive(obs, jailed_teammate.position)
            return _Objective("Rescuing teammate", rescue_target, radius=1, sprint=True)

        target_flag = _pick_best_flag_target(obs, me)
        if target_flag is not None:
            return _Objective("Attacking flag", target_flag.grid_position, radius=1, sprint=True)

        intercept_point = _best_midfield_anchor_adaptive(obs)
        return _Objective("Holding midfield", intercept_point, radius=1, sprint=False)

    def _should_keep_current_objective(
        self,
        obs: Observation,
        me: PlayerState,
        next_objective: "_Objective",
    ) -> bool:
        current = self.current_objective
        if current is None:
            return False

        if current.label != next_objective.label:
            if _is_high_priority_label(next_objective.label):
                return False
            if _is_high_priority_label(current.label) and not _is_objective_complete(me, current):
                return True
            return self.objective_hold_ticks < self.objective_max_hold_ticks

        if _same_grid_position(current.target, next_objective.target):
            return True

        if self.objective_hold_ticks >= self.objective_max_hold_ticks:
            return False

        if _is_objective_complete(me, current):
            return False

        return _manhattan_distance(me.position, current.target) > 2


# =============================================================================
# 向后兼容：保留原有策略
# =============================================================================

# 重新导出旧策略以保持兼容性
from default_strategy import RandomWalkStrategy, PickClosestFlagAndBackStrategy

__all__ = [
    "EliteCTFStrategy",
    "AdaptiveCTFStrategy",
    "RandomWalkStrategy", 
    "PickClosestFlagAndBackStrategy",
]

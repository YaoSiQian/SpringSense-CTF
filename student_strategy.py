from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional, Tuple

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
STUCK_THRESHOLD_TICKS = 3  # 卡位检测阈值（降低以更快响应哞菇卡位）
STUCK_DISTANCE_THRESHOLD = 0.3  # 移动距离小于此值视为卡住（用于检测微小移动）
RESCUE_MAX_DISTANCE = 25
OBJECTIVE_HOLD_TICKS_MAX = 10
ROLE_SWITCH_COOLDOWN = 3  # 降低冷却，更快响应局势变化

# 新增：夺旗攻击性参数
CAPTURE_AGGRESSION_BONUS = -5  # 夺旗目标评分加成（负值=更优先）


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
    7. 插旗后冷却（避免卡在金块区）
    8. 预防性绕树避障
    """
    
    # 角色与状态
    role: Role = field(default_factory=lambda: Role.ATTACKER)
    state: State = field(default_factory=lambda: State.IDLE)
    
    # 目标管理
    current_objective: Objective | None = None
    objective_hold_ticks: int = 0
    last_declared_intent: tuple[str, int, int] | None = None
    
    # 卡位检测（原有）
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
    
    # ===== 新增：半场判断（动态确定）=====
    my_half_negative: Optional[bool] = None
    
    # ===== 新增：插旗后冷却机制 =====
    had_flag_last_tick: bool = False
    escape_target: Optional[Tuple[int, int]] = None
    post_plant_cooldown: int = 0
    
    # ===== 新增：连续夺旗优化 =====
    last_flag_captured_time: int = 0  # 用于记录上次夺旗时间
    consecutive_captures: int = 0  # 连续夺旗计数
    
    # ===== 新增：预防性绕树避障 =====
    last_pos_float: Optional[Tuple[float, float]] = None
    stuck_ticks_avoidance: int = 0
    avoidance_target: Optional[Tuple[int, int]] = None
    
    # ===== 新增：敌方树叶卡住检测 =====
    enemy_position_history: dict[str, list[GridPosition]] = field(default_factory=dict)
    enemy_stuck_ticks: dict[str, int] = field(default_factory=dict)
    
    # ===== 新增：立即卡位检测 =====
    micro_stuck_ticks: int = 0  # 微动卡住计数
    last_micro_check_pos: Optional[GridPosition] = None  # 上次检测位置
    
    # ===== 新增：移动意图与实际移动不符检测 =====
    movement_intent_start_pos: Optional[GridPosition] = None  # 开始移动意图时的位置
    movement_intent_start_tick: int = 0  # 开始移动意图时的tick计数
    movement_intent_threshold: int = 2  # 移动意图持续tick阈值（超过此值未移动1格视为卡住）

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
        
        # ===== 新增：动态确定半场 =====
        if obs.my_targets:
            self.my_half_negative = obs.my_targets[0].grid_position.x < 0
        else:
            self.my_half_negative = True  # 默认左队
        
        # ===== 新增：重置插旗冷却 =====
        self.had_flag_last_tick = False
        self.escape_target = None
        self.post_plant_cooldown = 0
        
        # ===== 新增：重置避障状态 =====
        self.last_pos_float = None
        self.stuck_ticks_avoidance = 0
        self.avoidance_target = None
        
        # ===== 新增：重置敌方跟踪 =====
        self.enemy_position_history = {}
        self.enemy_stuck_ticks = {}
        
        # ===== 新增：重置立即卡位检测 =====
        self.micro_stuck_ticks = 0
        self.last_micro_check_pos = None
        
        # ===== 新增：重置移动意图检测 =====
        self.movement_intent_start_pos = None
        self.movement_intent_start_tick = 0

    def compute_next_action(self, obs: Observation) -> list[Action]:
        """
        主决策函数 - 每 tick 调用
        
        决策优先级（按 STRATEGY.md 5.2 调整）：
        1. 越狱逃脱（最高优先级）
        2. 持旗返回（最高优先级，避免掉旗）
        3. 插旗后冷却/脱离
        4. 预防性绕树避障
        5. 卡位脱困
        6. 拦截携旗敌人（次高优先级）
        7. 评估抓捕空载敌人（新增，见机行事）
        8. 救援队友（降低优先级，见机行事）
        9. 夺旗（默认行为，提高优先级）
        10. 防守/中场控制
        """
        me = obs.self_player
        actions: list[Action] = []
        
        # ===== 新增：更新敌方跟踪（检测被树叶卡住的敌人） =====
        self._update_enemy_tracking(obs)
        
        # ===== 新增：插旗检测与冷却处理 =====
        just_planted = self.had_flag_last_tick and not me.has_flag
        self.had_flag_last_tick = me.has_flag
        
        if just_planted:
            # 刚插旗成功，设置冷却并生成脱离目标
            self.post_plant_cooldown = 3  # 减少冷却时间，更快进入下一波进攻
            self.consecutive_captures += 1
            # 向敌方半场深处移动，准备继续夺旗
            direction = 1 if self._is_enemy_half(me.position.x) else -1
            # 继续深入敌方半场夺旗，而不是返回
            escape_x = me.position.x + direction * 8  # 增加距离，深入敌后
            escape_x = max(-28, min(28, escape_x))  # 限制在地图范围内
            self.escape_target = (int(escape_x), me.position.z)
            self.last_declared_intent = None
            # 重置避障状态
            self.stuck_ticks_avoidance = 0
            self.avoidance_target = None
        
        # 冷却递减
        if self.post_plant_cooldown > 0:
            self.post_plant_cooldown -= 1
        
        # 优先执行脱离移动（远离金块区）
        if self.escape_target is not None:
            if abs(me.position.x - self.escape_target[0]) <= 1 and abs(me.position.z - self.escape_target[1]) <= 1:
                self.escape_target = None
            else:
                return [self._create_move(
                    GridPosition(x=self.escape_target[0], z=self.escape_target[1]),
                    "Moving away from planted block",
                    radius=0
                )]
        
        # ===== 新增：预防性绕树避障逻辑 =====
        current_pos_float = (me.position.x, me.position.z)
        if self.last_pos_float is not None:
            dist_moved = math.hypot(
                current_pos_float[0] - self.last_pos_float[0],
                current_pos_float[1] - self.last_pos_float[1]
            )
            if dist_moved < 0.1:  # 几乎没移动，可能被树挡住
                self.stuck_ticks_avoidance += 1
            else:
                self.stuck_ticks_avoidance = 0
        self.last_pos_float = current_pos_float
        
        # 如果已有绕行目标，继续执行
        if self.avoidance_target is not None:
            if abs(me.position.x - self.avoidance_target[0]) <= 1 and abs(me.position.z - self.avoidance_target[1]) <= 1:
                self.avoidance_target = None
                self.stuck_ticks_avoidance = 0
            else:
                return [self._create_move(
                    GridPosition(x=self.avoidance_target[0], z=self.avoidance_target[1]),
                    "Avoiding tree obstacle",
                    radius=0
                )]
        
        # 检测到卡住且没有活跃绕行目标时，生成新的绕行点
        if self.stuck_ticks_avoidance > 3 and self.avoidance_target is None:
            # 向 z 方向随机一侧移动 5 格来绕开障碍
            direction = 1 if self.rng.random() > 0.5 else -1
            avoid_z = int(me.position.z + direction * 5)
            avoid_z = max(-28, min(28, avoid_z))  # 保持在地图边界内
            self.avoidance_target = (int(me.position.x), avoid_z)
            return [self._create_move(
                GridPosition(x=self.avoidance_target[0], z=self.avoidance_target[1]),
                "Avoiding tree obstacle",
                radius=0
            )]
        
        # 冷却期间：持续向敌方半场深处移动（确保远离金块）
        if self.post_plant_cooldown > 0:
            target_x = 15 if self._is_enemy_half(me.position.x) else -15
            return [self._create_move(
                GridPosition(x=target_x, z=me.position.z),
                "Post-plant cooldown",
                radius=0
            )]
        
        # 1. 监狱逃脱（最高优先级）
        if me.in_prison:
            return self._escape_prison(obs)
        
        # 2. 持旗返回（最高优先级，避免掉旗）
        if me.has_flag:
            self.state = State.CARRYING
            return self._return_flag(obs)
        
        # 3. 检查"移动意图与实际移动不符"（最高优先级脱困）
        # 根据 STRATEGY.md：当处于移动状态但x、z未移动超过1格时立即处理
        intent_stuck_action = self._check_movement_intent_stuck(obs)
        if intent_stuck_action is not None:
            return intent_stuck_action
        
        # 4. 常规卡位检测（备用）
        escape_action = self._try_escape_if_stuck(obs)
        if escape_action is not None:
            return escape_action
        
        # 5. 动态角色分配（带冷却）
        if self.role_switch_cooldown <= 0:
            self._update_role(obs)
        else:
            self.role_switch_cooldown -= 1
        
        # 6. 拦截携旗敌人（次高优先级）- 过滤被树叶卡住的
        enemy_carriers = [
            e for e in obs.enemies 
            if e.has_flag and not self._is_enemy_stuck_in_leaves(e, obs)
        ]
        if enemy_carriers:
            # 判断是否应该拦截：如果在己方半场或很近，优先拦截
            closest_carrier = min(enemy_carriers, 
                                 key=lambda e: _manhattan_distance(me.position, e.position))
            if self._should_intercept(obs, closest_carrier):
                return self._intercept_enemy(obs, closest_carrier)
        
        # 7. 评估抓捕空载敌人（见机行事，新增）
        capture_action = self._evaluate_capture_empty_enemy(obs)
        if capture_action:
            return capture_action
        
        # 8. 检查是否需要救援（降低优先级，只在顺路或严重时救援）
        if self._should_rescue_aggressive(obs):
            return self._rescue_teammate(obs)
        
        # 9. 夺旗或防守（夺旗优先级提升）
        if self.role == Role.ATTACKER or self._should_prioritize_attack(obs):
            return self._capture_flag(obs)
        elif self.role == Role.DEFENDER:
            return self._defend_base(obs)
        else:  # SUPPORT
            return self._control_midfield(obs)

    def _check_movement_intent_stuck(self, obs: Observation, current_action_target: Optional[GridPosition] = None) -> list[Action] | None:
        """
        检测"移动意图与实际移动不符"的情况
        
        原理：
        - 如果有移动目标（current_objective）
        - 且位置在目标点1格范围外（确实需要移动）
        - 但连续多个tick x、z坐标变化不超过1格
        - 则视为被卡住，立即切换到脱困处理
        
        根据 STRATEGY.md 第8节，这种情况通常是：
        - 被哞菇等实体卡住（pathfinder试图避开但哞菇追踪）
        - 被树叶等障碍物卡住
        - 被地形/玻璃墙卡住
        
        返回 None 表示未卡住，返回 Action 列表表示需要脱困
        """
        me = obs.self_player
        
        # 检查是否有活跃移动目标
        has_movement_intent = False
        if self.current_objective is not None:
            dist_to_target = _manhattan_distance(me.position, self.current_objective.target)
            if dist_to_target > 1:  # 距离目标超过1格，确实需要移动
                has_movement_intent = True
        
        # 如果没有移动意图，重置状态
        if not has_movement_intent:
            self.movement_intent_start_pos = None
            self.movement_intent_start_tick = 0
            return None
        
        # 有移动意图，开始或继续跟踪
        if self.movement_intent_start_pos is None:
            # 开始新的移动意图跟踪
            self.movement_intent_start_pos = me.position
            self.movement_intent_start_tick = 1
            return None
        
        # 计算从移动意图开始到现在的位移
        dx = abs(me.position.x - self.movement_intent_start_pos.x)
        dz = abs(me.position.z - self.movement_intent_start_pos.z)
        
        # 如果已经移动超过1格，重置跟踪（正常移动）
        if dx > 1 or dz > 1:
            self.movement_intent_start_pos = me.position
            self.movement_intent_start_tick = 0
            return None
        
        # 未移动超过1格，增加计数
        self.movement_intent_start_tick += 1
        
        # 如果持续多个tick未移动超过1格，判定为卡住
        if self.movement_intent_start_tick >= self.movement_intent_threshold:
            # 重置跟踪状态（避免重复触发）
            self.movement_intent_start_pos = None
            self.movement_intent_start_tick = 0
            
            # 立即执行脱困（使用专门的快速脱困方法）
            print(f"[EliteCTF] MOVEMENT INTENT STUCK detected: {dx}x{dz} in {self.movement_intent_threshold} ticks")
            return self._escape_from_movement_stuck(obs)
        
        return None
    
    def _escape_from_movement_stuck(self, obs: Observation) -> list[Action]:
        """
        针对"移动意图与实际移动不符"的快速脱困策略
        
        根据 STRATEGY.md 的指导：
        1. 优先尝试跳跃+疾跑突破（哞菇可以穿过）
        2. 如果是树叶，尝试向空旷方向移动
        3. 如果是地形，尝试绕路
        
        与常规脱困的区别：
        - 反应更快（阈值更低）
        - 动作更激进（强制跳跃）
        - 优先向垂直于当前朝向的方向移动（绕开障碍）
        """
        me = obs.self_player
        
        # 策略1：如果是被哞菇等实体卡住，尝试强行穿过
        nearby_animals = [
            e for e in obs.entities 
            if e.entity_type == "animal"
            and _manhattan_distance(e.grid_position, me.position) <= 2
        ]
        
        if nearby_animals:
            # 有动物在附近，向远离动物的方向强行移动
            escape_x, escape_z = 0, 0
            for animal in nearby_animals:
                dx = me.position.x - animal.grid_position.x
                dz = me.position.z - animal.grid_position.z
                escape_x += dx
                escape_z += dz
            
            # 归一化并扩展
            magnitude = math.hypot(escape_x, escape_z)
            if magnitude > 0:
                scale = 6 / magnitude
                escape_x *= scale
                escape_z *= scale
            else:
                # 默认向 z 方向移动
                escape_z = 6 if me.position.z <= 0 else -6
            
            target = GridPosition(
                x=int(me.position.x + escape_x),
                z=int(me.position.z + escape_z)
            )
            target = _clamp_to_map(target, obs)
            
            print(f"[EliteCTF] Quick escape from animals -> ({target.x}, {target.z})")
            return [MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)]
        
        # 策略2：检测周围是否有树叶
        leaves_blocks = [
            b for b in obs.blocks
            if self._is_leaves_block(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 1
        ]
        
        if leaves_blocks:
            # 向远离树叶的方向移动
            escape_x, escape_z = 0, 0
            for leaf in leaves_blocks:
                dx = me.position.x - leaf.grid_position.x
                dz = me.position.z - leaf.grid_position.z
                escape_x += dx
                escape_z += dz
            
            # 强制向 z 方向为主移动（树叶通常在 x 方向排列）
            if abs(escape_z) < 1:
                escape_z = 5 if me.position.z <= 0 else -5
            
            magnitude = math.hypot(escape_x, escape_z)
            if magnitude > 0:
                scale = 5 / magnitude
                escape_x *= scale
                escape_z *= scale
            
            target = GridPosition(
                x=int(me.position.x + escape_x),
                z=int(me.position.z + escape_z)
            )
            target = _clamp_to_map(target, obs)
            
            print(f"[EliteCTF] Quick escape from leaves -> ({target.x}, {target.z})")
            return [MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)]
        
        # 策略3：没有明显障碍，可能是地形卡位，尝试向垂直方向移动
        # 根据当前移动目标判断主要移动方向
        if self.current_objective is not None:
            target = self.current_objective.target
            main_dx = target.x - me.position.x
            main_dz = target.z - me.position.z
            
            # 向垂直于主要方向的方向移动（绕路）
            if abs(main_dx) > abs(main_dz):
                # 主要向 x 方向移动，改为向 z 方向绕路
                escape_z = 6 if me.position.z <= 0 else -6
                target = GridPosition(x=me.position.x, z=me.position.z + escape_z)
            else:
                # 主要向 z 方向移动，改为向 x 方向绕路
                escape_x = 6 if self._is_enemy_half(me.position.x) else -6
                target = GridPosition(x=me.position.x + escape_x, z=me.position.z)
        else:
            # 没有明确目标，向随机方向移动
            directions = [(0, 5), (0, -5), (5, 0), (-5, 0)]
            dx, dz = self.rng.choice(directions)
            target = GridPosition(x=me.position.x + dx, z=me.position.z + dz)
        
        target = _clamp_to_map(target, obs)
        print(f"[EliteCTF] Quick escape from terrain -> ({target.x}, {target.z})")
        return [MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)]

    # =========================================================================
    # 角色分配
    # =========================================================================

    # =========================================================================
    # 新增：半场判断辅助方法
    # =========================================================================
    
    def _is_my_half(self, x: int) -> bool:
        """判断 X 坐标是否在己方半场"""
        if self.my_half_negative is None:
            return True
        return (x < 0) if self.my_half_negative else (x >= 0)
    
    def _is_enemy_half(self, x: int) -> bool:
        """判断 X 坐标是否在敌方半场"""
        return not self._is_my_half(x)
    
    def _update_role(self, obs: Observation) -> None:
        """
        动态角色分配逻辑（优化后，更注重进攻）
        
        策略：
        - 敌方有人持旗且在己方半场 → DEFENDER
        - 多个队友被困且自己在监狱附近 → SUPPORT
        - 其他情况 → ATTACKER（默认进攻）
        """
        me = obs.self_player
        
        # 计算各类需求 - 过滤被树叶卡住的敌人
        enemy_carriers = [
            e for e in obs.enemies 
            if e.has_flag and not self._is_enemy_stuck_in_leaves(e, obs)
        ]
        jailed_teammates = [p for p in obs.teammates if p.in_prison]
        free_teammates = len(obs.teammates) - len(jailed_teammates) + 1  # +1 包括自己
        
        new_role = self.role
        
        # 高优先级：敌方持旗且在己方半场 → 需要防守
        if enemy_carriers:
            # 检查最近的携旗敌人是否在己方半场
            closest_carrier = min(enemy_carriers, 
                                 key=lambda e: _manhattan_distance(me.position, e.position))
            if self._is_my_half(closest_carrier.position.x):
                # 只有自己在己方半场且敌人靠近时才防守
                if self._is_my_half(me.position.x):
                    new_role = Role.DEFENDER
        
        # 中优先级：多人被困且自己在监狱附近 → 需要支援
        elif len(jailed_teammates) >= 2 and free_teammates <= 2:
            prison_plate = PRISON_PRESSURE_PLATE[obs.team]
            if _manhattan_distance(me.position, prison_plate) <= 15:
                new_role = Role.SUPPORT
        
        # 默认：进攻（提高攻击性）
        else:
            new_role = Role.ATTACKER
        
        # 角色切换时重置冷却
        if new_role != self.role:
            self.role = new_role
            self.role_switch_cooldown = ROLE_SWITCH_COOLDOWN
            if self.verbose:
                print(f"[EliteCTF] Role changed to {self.role.name}")
    
    def _should_prioritize_attack(self, obs: Observation) -> bool:
        """
        判断是否应优先进攻（即使角色不是 ATTACKER）
        
        场景：
        - 己方没有空金块可插旗（防守无意义）
        - 自己在敌方半场且离旗帜很近
        - 敌方没有携旗者（无需防守）
        """
        me = obs.self_player
        
        # 如果没有空金块，防守无意义，去夺旗
        if not obs.my_targets:
            return True
        
        # 如果在敌方半场且离旗帜很近，优先完成夺旗
        if self._is_enemy_half(me.position.x):
            flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
            if flags:
                closest_flag = min(flags, key=lambda f: _euclidean_distance(me.position, f.grid_position))
                if _euclidean_distance(me.position, closest_flag.grid_position) <= 5:
                    return True
        
        # 如果敌方没有携旗者，无需防守
        if not any(e.has_flag for e in obs.enemies):
            return True
        
        return False
    
    def _should_intercept(self, obs: Observation, carrier: PlayerState) -> bool:
        """
        判断是否值得拦截携旗敌人
        
        值得拦截的情况：
        - 敌人在己方半场（可以抓捕）
        - 敌人离己方目标点很近（紧急）
        - 自己离敌人很近（容易拦截）
        
        避免拦截的情况：
        - 自己在敌方半场深处（去拦截会浪费进攻机会）
        - 自己离敌人很远（拦截来不及）
        """
        me = obs.self_player
        carrier_dist = _manhattan_distance(me.position, carrier.position)
        
        # 敌人在己方半场：应该拦截
        if self._is_my_half(carrier.position.x):
            return True
        
        # 敌人离己方目标点很近：紧急拦截
        if obs.my_targets:
            closest_target = min(obs.my_targets, 
                                key=lambda t: _manhattan_distance(carrier.position, t.grid_position))
            if _manhattan_distance(carrier.position, closest_target.grid_position) <= 8:
                return carrier_dist <= 15  # 只有距离合适才拦截
        
        # 自己在敌方半场深处：优先夺旗，不拦截
        if self._is_enemy_half(me.position.x):
            flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
            if flags:
                closest_flag = min(flags, key=lambda f: _euclidean_distance(me.position, f.grid_position))
                if _euclidean_distance(me.position, closest_flag.grid_position) <= 8:
                    return False  # 优先夺旗
        
        # 自己离敌人很远：拦截来不及
        if carrier_dist > 20:
            return False
        
        return True

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
        - 选择最近的空金块（严格过滤：只选己方半场的）
        - 避开敌人
        - 紧急模式：radius=0, sprint=True
        """
        me = obs.self_player
        self.return_home_ticks += 1
        
        # 只选择己方半场的目标点
        targets = [t for t in obs.my_targets if self._is_my_half(t.grid_position.x)]
        
        if not targets:
            # 没有目标点，保持原地或去安全位置
            safe_pos = _get_safe_position(obs)
            return [self._create_move(safe_pos, "No target, holding", radius=1)]
        
        # 选择最近的目标点
        target = min(targets, key=lambda t: _euclidean_distance(me.position, t.grid_position))
        target_pos = target.grid_position
        
        # 检查是否需要调整路径以避开敌人
        safe_target = self._find_safe_path_target(me.position, target_pos, obs)
        
        return [self._create_move(safe_target, "Returning flag", radius=0, sprint=True)]

    # =========================================================================
    # 救援系统
    # =========================================================================

    def _should_rescue(self, obs: Observation) -> bool:
        """
        判断是否应该去救援队友（原方法保留兼容性）
        """
        return self._should_rescue_aggressive(obs)
    
    def _should_rescue_aggressive(self, obs: Observation) -> bool:
        """
        判断是否应该去救援队友（优化后，更保守）
        
        只在以下情况救援：
        - 持旗队友被困（极高优先级）
        - 多个队友被困且自己就在监狱门口（顺路）
        
        避免救援的情况：
        - 自己在敌方半场深处（浪费进攻机会）
        - 需要跑很远去救援
        """
        jailed_teammates = [p for p in obs.teammates if p.in_prison]
        if not jailed_teammates:
            return False
        
        me = obs.self_player
        
        # 极高优先级：持旗队友被困且自己离监狱不远
        flag_carriers_jailed = [p for p in jailed_teammates if p.has_flag]
        if flag_carriers_jailed:
            prison_plate = PRISON_PRESSURE_PLATE[obs.team]
            if _manhattan_distance(me.position, prison_plate) <= 12:
                return True
        
        # 低优先级：多个队友被困且自己就在监狱门口
        free_teammates = [p for p in obs.teammates if not p.in_prison]
        if len(jailed_teammates) >= 2 and len(free_teammates) <= 1:
            prison_plate = PRISON_PRESSURE_PLATE[obs.team]
            # 必须非常顺路才去
            if _manhattan_distance(me.position, prison_plate) <= 6:
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
    
    def _evaluate_capture_empty_enemy(self, obs: Observation) -> list[Action] | None:
        """
        评估是否抓捕空载敌人（见机行事，按 STRATEGY.md 5.3）
        
        高收益场景（推荐抓捕）：
        - 敌人在己方半场深处 → 抓捕容易且安全
        - **敌人在己方半场且距离很近 → 优先抓捕**
        - 敌人离旗帜很近 → 阻止敌方夺旗
        - 多个敌人聚集 → 一网打尽机会
        
        低收益/高风险场景（不推荐抓捕）：
        - 追击到敌方半场 → 自己会被抓（绝不越界）
        - 离旗帜很近时 → 机会成本高
        - 敌人在边界徘徊 → 可能引诱你进入敌方半场
        
        返回 None 表示不抓捕，返回 Action 列表表示执行抓捕
        """
        me = obs.self_player
        
        # 过滤掉已经在监狱、持旗、或被树叶卡住的敌人
        empty_enemies = [
            e for e in obs.enemies 
            if not e.in_prison and not e.has_flag and not self._is_enemy_stuck_in_leaves(e, obs)
        ]
        if not empty_enemies:
            return None
        
        best_target = None
        best_score = float('-inf')
        
        # 获取旗帜信息用于判断
        flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        my_distance_to_flag = float('inf')
        if flags:
            closest_flag_to_me = min(flags, 
                key=lambda f: _manhattan_distance(me.position, f.grid_position))
            my_distance_to_flag = _manhattan_distance(me.position, closest_flag_to_me.grid_position)
        
        for enemy in empty_enemies:
            score = 0
            distance = _manhattan_distance(me.position, enemy.position)
            enemy_on_my_half = self._is_my_half(enemy.position.x)
            
            # ===== 核心：己方领地+近距离敌人优先抓捕 =====
            if enemy_on_my_half:
                # 敌人在己方半场，根据距离给予不同优先级
                if distance <= 3:
                    # 非常近（3格内）：极高优先级抓捕
                    score += 50
                    if self.verbose:
                        print(f"[EliteCTF] Enemy very close ({distance}m) on our half!")
                elif distance <= 6:
                    # 较近（6格内）：高优先级抓捕
                    score += 35
                elif distance <= 10:
                    # 中等距离（10格内）：如果顺路可以抓捕
                    score += 20
                else:
                    # 较远：只在很深入时考虑
                    if abs(enemy.position.x) >= 15:
                        score += 10
                
                # 己方半场深度加成
                depth = abs(enemy.position.x) + 5
                score += depth * 0.3
            else:
                # 敌人在敌方半场 → 风险高，大幅减分，一般不考虑
                score -= 100
                continue  # 跳过敌方半场的敌人
            
            # 基础分：越近越好
            score += max(0, 15 - distance)
            
            # 2. 检查敌人是否靠近旗帜（阻止夺旗）
            if flags:
                closest_flag_to_enemy = min(flags, 
                    key=lambda f: _manhattan_distance(enemy.position, f.grid_position))
                enemy_to_flag = _manhattan_distance(enemy.position, closest_flag_to_enemy.grid_position)
                if enemy_to_flag <= 5:
                    # 敌人在旗帜附近，阻止其夺旗
                    score += 25
                    if distance <= 6:
                        score += 15  # 近距离+近旗 = 紧急
            
            # 3. 检查附近是否有其他敌人（一网打尽机会）
            nearby_enemies = sum(
                1 for e in empty_enemies
                if _manhattan_distance(e.position, enemy.position) <= 4
            )
            score += nearby_enemies * 8  # 提高聚集加成
            
            # 风险扣分
            
            # 如果敌人在边界附近（x 接近 0），可能被引诱到敌方半场
            if abs(enemy.position.x) <= 3:
                score -= 15
            
            # 如果去抓捕会让自己远离旗帜太远，扣分（但如果敌人很近则不减）
            if my_distance_to_flag <= 3 and distance > 6:
                # 自己就在旗帜旁，不要为了远敌放弃防守
                score -= 40
            
            # 阈值判断：根据距离动态调整阈值
            # 近距离敌人阈值更低，更容易触发抓捕
            threshold = 20 if distance <= 4 else 30
            
            if score > best_score and score >= threshold:
                best_score = score
                best_target = enemy
        
        if best_target:
            distance = _manhattan_distance(me.position, best_target.position)
            print(f"[EliteCTF] CHASE empty enemy at {best_target.position.x},{best_target.position.z} "
                  f"(dist={distance}, score={best_score:.1f})")
            return [
                self._create_move(best_target.position, "Chasing empty enemy", radius=0, sprint=True, jump=True)
            ]
        
        return None

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
        夺旗策略：选择最优旗帜目标（优化后更具攻击性）
        
        评估因素：
        - 距离（最优先）
        - 返回路径的安全性
        - 敌方防守压力
        - 严格半场过滤（只选敌方半场的旗帜）
        - 新增：连续夺旗奖励（保持进攻节奏）
        """
        flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        
        # 只选择敌方半场的旗帜
        flags = tuple(
            flag for flag in flags 
            if self._is_enemy_half(flag.grid_position.x)
        )
        
        if not flags:
            # 没有可夺旗帜，转为防守
            return self._defend_base(obs)
        
        # 选择最优旗帜（优化评分）
        target_flag = self._pick_best_flag_aggressive(obs, flags)
        if target_flag is None:
            return self._defend_base(obs)
        
        target_pos = target_flag.grid_position
        
        print(f"[EliteCTF] CAPTURE {target_pos.x},{target_pos.z}")
        return [
            self._create_move(target_pos, "Attacking flag", radius=0, sprint=True)  # radius=0 更精确
        ]
    
    def _pick_best_flag_aggressive(self, obs: Observation, flags: tuple[BlockState, ...]) -> Optional[BlockState]:
        """
        选择最优夺旗目标（攻击性版本）
        
        评分 = 距离成本 + 安全成本 + 攻击性加成
        - 距离成本：当前位置到旗帜的距离（权重降低）
        - 安全成本：夺旗后返回的风险评估（权重降低）
        - 攻击性加成：连续夺旗奖励，优先选择靠近其他旗帜的位置
        """
        me = obs.self_player
        
        # 过滤掉距离过近的目标（避免选自身所在位置）
        valid_flags = [
            flag for flag in flags
            if _euclidean_distance(me.position, flag.grid_position) >= 0.5
        ]
        
        if not valid_flags:
            return None
        
        def flag_score_aggressive(flag: BlockState) -> float:
            distance = _euclidean_distance(me.position, flag.grid_position)
            safety_penalty = self._evaluate_flag_safety(flag, obs)
            
            # 攻击性加成：距离越近分数越低（越优先）
            # 降低安全因素的权重，更注重距离
            score = distance * 0.7 + safety_penalty * 0.3
            
            # 如果在敌方半场深处，给予额外奖励（深入敌后策略）
            if self._is_enemy_half(flag.grid_position.x):
                enemy_depth = abs(flag.grid_position.x) - 10  # 假设中场在 x=±10
                if enemy_depth > 0:
                    score -= enemy_depth * 0.2  # 深入敌后略有奖励
            
            return score
        
        return min(valid_flags, key=flag_score_aggressive)

    def _pick_best_flag(self, obs: Observation, flags: tuple[BlockState, ...]) -> Optional[BlockState]:
        """
        选择最优夺旗目标
        
        评分 = 距离成本 + 安全成本
        - 距离成本：当前位置到旗帜的距离
        - 安全成本：夺旗后返回的风险评估
        - 排除距离过近的目标（避免原地踏步）
        """
        me = obs.self_player
        
        # 过滤掉距离小于 0.5 格的目标（避免选自身所在位置）
        valid_flags = [
            flag for flag in flags
            if _euclidean_distance(me.position, flag.grid_position) >= 0.5
        ]
        
        if not valid_flags:
            return None
        
        def flag_score(flag: BlockState) -> float:
            distance = _euclidean_distance(me.position, flag.grid_position)
            safety_penalty = self._evaluate_flag_safety(flag, obs)
            return distance + safety_penalty
        
        return min(valid_flags, key=flag_score)

    def _evaluate_flag_safety(self, flag: BlockState, obs: Observation) -> float:
        """
        评估夺旗后返回的安全性（优化后，降低权重）
        
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
        
        # 旗帜附近的敌人数量（敌方防守力量）- 降低权重
        nearby_enemies = sum(
            1 for e in obs.enemies
            if _manhattan_distance(e.position, flag_pos) <= 6
        )
        
        # 返回路径上的敌人（己方半场）- 降低权重
        path_enemies = sum(
            1 for e in obs.enemies
            if _is_on_our_side(e.position, obs.team)
            and _manhattan_distance(e.position, flag_pos) <= 12
        )
        
        # 综合评分 - 降低安全因素的权重，更注重进攻
        return min_return_distance * 0.3 + nearby_enemies * 2 + path_enemies * 1

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
        
        检测逻辑：
        - 完全静止：位置不变
        - 无效移动：有微小移动但无法有效前进（常见于被哞菇挤来挤去）
        
        返回 None 表示没有卡位
        返回 Action 列表表示执行脱困动作
        """
        me = obs.self_player
        
        # 更新卡位计数
        if self.last_position is None:
            self.last_position = me.position
            self.stuck_ticks = 0
            return None
        
        # 计算实际移动距离（欧几里得距离）
        distance_moved = _euclidean_distance(self.last_position, me.position)
        
        if distance_moved < STUCK_DISTANCE_THRESHOLD:
            # 移动距离太小，视为卡住（包括完全静止或微小移动）
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
            self.last_position = me.position
        
        if self.stuck_ticks < STUCK_THRESHOLD_TICKS:
            return None
        
        # 重置卡位计数器，准备执行脱困
        self.stuck_ticks = 0
        
        # 检测到卡位，执行脱困
        return self._escape_from_stuck(obs)

    def _escape_from_stuck(self, obs: Observation) -> list[Action]:
        """
        脱困策略：向远离障碍物的方向移动
        
        针对哞菇卡位的特殊处理：
        - 哞菇会追踪玩家，导致 pathfinder 死锁（pathfinder 试图避开哞菇，但哞菇紧跟玩家）
        - 实际上玩家可以穿过哞菇，需要强制生成逃离路径
        - 只有周围1个方块内有超过3只哞菇才考虑脱困
        """
        me = obs.self_player
        
        # 检测非常近距离的动物（主要是哞菇）- 只有1格内超过3只才脱困
        very_nearby_animals = [
            e for e in obs.entities 
            if e.entity_type == "animal"
            and _manhattan_distance(e.grid_position, me.position) <= 1  # 1格内
        ]
        
        # 检测附近的硬障碍物
        nearby_obstacles = [
            b for b in obs.blocks
            if _is_hard_block_name(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 2
        ]
        
        # 检测是否卡在树叶中
        leaves_escape = self._escape_from_leaves(obs)
        if leaves_escape:
            return leaves_escape
        
        # 特判：只有周围1格内有超过3只动物才强力脱困
        if len(very_nearby_animals) > 3:
            return self._escape_from_mooshrooms(obs, very_nearby_animals)
        
        if not nearby_obstacles:
            # 没有明显障碍，随机选择一个方向
            directions = [(4, 0), (-4, 0), (0, 4), (0, -4), (3, 3), (-3, 3), (3, -3), (-3, -3)]
            dx, dz = self.rng.choice(directions)
            target = GridPosition(x=me.position.x + dx, z=me.position.z + dz)
        else:
            # 计算逃离向量（远离障碍物的方向）
            escape_x, escape_z = 0, 0
            
            for obstacle in nearby_obstacles:
                dx = me.position.x - obstacle.grid_position.x
                dz = me.position.z - obstacle.grid_position.z
                escape_x += dx * 2  # 障碍物权重更高
                escape_z += dz * 2
            
            # 归一化并扩展
            if escape_x != 0 or escape_z != 0:
                target = GridPosition(
                    x=me.position.x + (6 if escape_x > 0 else -6),
                    z=me.position.z + (6 if escape_z > 0 else -6)
                )
            else:
                target = GridPosition(x=me.position.x + 5, z=me.position.z)
        
        # 确保目标在地图范围内
        target = _clamp_to_map(target, obs)
        
        print(f"[EliteCTF] Breaking free from stuck -> ({target.x}, {target.z})")
        return [
            MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)
        ]
    
    def _escape_from_mooshrooms(self, obs: Observation, animals: list) -> list[Action]:
        """
        专门处理被哞菇（或其他动物）卡住的情况
        
        策略：
        1. 计算远离所有动物的合力方向
        2. 优先向己方半场或安全区域移动
        3. 增加跳跃以尝试越过/穿过实体
        """
        me = obs.self_player
        
        # 计算逃离向量（远离所有动物）
        escape_x, escape_z = 0, 0
        
        for animal in animals:
            dx = me.position.x - animal.grid_position.x
            dz = me.position.z - animal.grid_position.z
            # 使用欧几里得距离加权，越近的动物影响越大
            dist = max(1, math.hypot(dx, dz))
            weight = 3.0 / dist  # 近距离动物权重更高
            escape_x += dx * weight
            escape_z += dz * weight
        
        # 如果没有明显的逃离方向，默认向己方半场移动
        if abs(escape_x) < 0.1 and abs(escape_z) < 0.1:
            # 向己方半场深处移动
            escape_x = -8 if self.my_half_negative else 8
            escape_z = 0
        
        # 归一化并扩展逃离距离（至少 6 格以确保脱离卡位）
        magnitude = math.hypot(escape_x, escape_z)
        if magnitude > 0:
            scale = 8 / magnitude  # 确保逃离距离足够远
            escape_x *= scale
            escape_z *= scale
        
        # 计算目标位置
        target = GridPosition(
            x=int(me.position.x + escape_x),
            z=int(me.position.z + escape_z)
        )
        
        # 确保在地图范围内
        target = _clamp_to_map(target, obs)
        
        # 确保目标位置不会离当前位置太近（至少移动 4 格）
        if _manhattan_distance(me.position, target) < 4:
            # 向 z 方向偏移以增加移动距离
            dz = 5 if me.position.z <= 0 else -5
            target = GridPosition(x=target.x, z=me.position.z + dz)
            target = _clamp_to_map(target, obs)
        
        print(f"[EliteCTF] Escaping from {len(animals)} animals -> ({target.x}, {target.z})")
        return [
            MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)
        ]
    
    def _escape_from_leaves(self, obs: Observation) -> list[Action] | None:
        """
        专门处理卡在树叶中的情况
        
        树叶虽然是透明方块，但碰撞箱可能导致卡住。
        策略：
        1. 检测周围1格内是否有树叶
        2. 如果有，尝试向空旷方向移动（优先向下或侧向）
        3. 使用跳跃+疾跑尝试脱离
        """
        me = obs.self_player
        
        # 检测周围1格内的树叶方块
        leaves_blocks = [
            b for b in obs.blocks
            if self._is_leaves_block(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 1
        ]
        
        if not leaves_blocks:
            return None
        
        # 计算逃离方向（远离树叶的合力方向）
        escape_x, escape_z = 0, 0
        
        for leaf in leaves_blocks:
            dx = me.position.x - leaf.grid_position.x
            dz = me.position.z - leaf.grid_position.z
            # 越近的树叶影响越大
            dist = max(1, abs(dx) + abs(dz))
            weight = 2.0 / dist
            escape_x += dx * weight
            escape_z += dz * weight
        
        # 如果逃离方向不明显，默认向 z 方向移动（通常树叶在 x 方向排列）
        if abs(escape_x) < 0.5 and abs(escape_z) < 0.5:
            escape_z = 5 if me.position.z <= 0 else -5
            escape_x = 3 if self._is_enemy_half(me.position.x) else -3
        
        # 归一化并扩展逃离距离
        magnitude = math.hypot(escape_x, escape_z)
        if magnitude > 0:
            scale = 5 / magnitude
            escape_x *= scale
            escape_z *= scale
        
        # 计算目标位置
        target = GridPosition(
            x=int(me.position.x + escape_x),
            z=int(me.position.z + escape_z)
        )
        
        # 确保在地图范围内
        target = _clamp_to_map(target, obs)
        
        print(f"[EliteCTF] Escaping from {len(leaves_blocks)} leaves blocks -> ({target.x}, {target.z})")
        return [
            MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)
        ]
    
    def _is_leaves_block(self, name: str) -> bool:
        """判断是否为树叶方块"""
        leaves_tokens = ("leaves", "oak_leaves", "spruce_leaves", "birch_leaves", 
                        "jungle_leaves", "acacia_leaves", "dark_oak_leaves",
                        "mangrove_leaves", "azalea_leaves", "flowering_azalea_leaves")
        return any(token in name.lower() for token in leaves_tokens)
    
    def _update_enemy_tracking(self, obs: Observation) -> None:
        """
        更新敌方位置跟踪，检测被树叶卡住的敌人
        
        如果敌方在同一个位置许久未动，且附近有树叶，
        认为其被树叶卡住，在决策时忽略该敌人
        """
        ENEMY_HISTORY_SIZE = 10  # 保留最近10个位置
        STUCK_DISTANCE_THRESHOLD = 0.5  # 视为未动的距离阈值
        STUCK_TICKS_THRESHOLD = 5  # 5 tick 未动视为卡住
        
        for enemy in obs.enemies:
            enemy_name = enemy.name
            current_pos = enemy.position
            
            # 初始化该敌人的历史记录
            if enemy_name not in self.enemy_position_history:
                self.enemy_position_history[enemy_name] = []
                self.enemy_stuck_ticks[enemy_name] = 0
            
            history = self.enemy_position_history[enemy_name]
            
            # 检查移动距离
            if history:
                last_pos = history[-1]
                distance = _euclidean_distance(last_pos, current_pos)
                
                if distance < STUCK_DISTANCE_THRESHOLD:
                    # 几乎没动
                    self.enemy_stuck_ticks[enemy_name] = self.enemy_stuck_ticks.get(enemy_name, 0) + 1
                else:
                    # 移动了，重置计数
                    self.enemy_stuck_ticks[enemy_name] = 0
            
            # 更新历史记录
            history.append(current_pos)
            if len(history) > ENEMY_HISTORY_SIZE:
                history.pop(0)
    
    def _is_enemy_stuck_in_leaves(self, enemy: PlayerState, obs: Observation) -> bool:
        """
        判断敌人是否被树叶卡住
        
        条件：
        1. 敌人最近5个tick几乎没动
        2. 敌人位置附近有树叶方块
        """
        enemy_name = enemy.name
        
        # 检查是否满足卡住的时间条件
        stuck_ticks = self.enemy_stuck_ticks.get(enemy_name, 0)
        if stuck_ticks < 5:  # 需要至少5 tick 未动
            return False
        
        # 检查附近是否有树叶
        for block in obs.blocks:
            if self._is_leaves_block(block.name):
                distance = _manhattan_distance(block.grid_position, enemy.position)
                if distance <= 2:  # 2格内有树叶
                    if self.verbose:
                        print(f"[EliteCTF] Enemy {enemy_name} stuck in leaves at "
                              f"({enemy.position.x},{enemy.position.z})")
                    return True
        
        return False

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _create_move(
        self, 
        target: GridPosition, 
        label: str, 
        radius: int = 1, 
        sprint: bool = True,
        jump: bool = True
    ) -> MoveTo:
        """创建移动动作"""
        return MoveTo(x=target.x, z=target.z, radius=radius, sprint=sprint, jump=jump)

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


def _euclidean_distance(left: GridPosition, right: GridPosition) -> float:
    """欧几里得距离"""
    return math.hypot(left.x - right.x, left.z - right.z)


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
    jump: bool = True


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
                jump=objective.jump,
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

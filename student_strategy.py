"""
EliteCTFStrategy v2.0 - 融合 OptimalCTFStrategy 核心算法的增强版

主要改进：
1. 整合 calculate_evasion_waypoint - 智能绕行算法（持旗返回时）
2. 整合 predict_enemy_target - 敌人目标预测（拦截时）
3. 整合 _is_stalemate - 僵持检测与目标切换（夺旗时）
4. 地图适配 - 支持 fixed 和 random 地图模式
5. 优化决策流程 - 新老元素协调
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional, Tuple

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState

if TYPE_CHECKING:
    from lib.actions import Action

# =============================================================================
# 常量定义 - 根据 STRATEGY.md 和地图适配
# =============================================================================

# 监狱压力板位置（踩下可立即开门解救队友）
PRISON_PRESSURE_PLATE: dict[str, GridPosition] = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}

# 监狱危险区域 Z 坐标（z=28 的压力板会重置60秒计时器）
PRISON_DANGER_ZONE_Z = 28

# 监狱逃脱目标（z < 26 即算逃脱）
PRISON_EXIT_TARGET: dict[str, GridPosition] = {
    "L": GridPosition(x=-16, z=25),
    "R": GridPosition(x=16, z=25),
}

# 中场控制点
MIDFIELD_ANCHOR: dict[str, GridPosition] = {
    "L": GridPosition(x=-6, z=0),
    "R": GridPosition(x=6, z=0),
}

# 决策阈值
STUCK_THRESHOLD_TICKS = 3
STUCK_DISTANCE_THRESHOLD = 0.3
RESCUE_MAX_DISTANCE = 25
OBJECTIVE_HOLD_TICKS_MAX = 10
ROLE_SWITCH_COOLDOWN = 3

# 新增：夺旗攻击性参数
CAPTURE_AGGRESSION_BONUS = -5

# 新增：僵持检测参数（来自 OptimalCTFStrategy）
STALEMATE_TIMEOUT = 5.0  # 5秒超时
STALEMATE_ENEMY_DISTANCE = 7  # 敌人距离阈值

# 新增：绕行算法参数（来自 OptimalCTFStrategy）
EVASION_CANDIDATE_DIRECTIONS = [
    (4, 0), (-4, 0), (0, 4), (0, -4),
    (3, 3), (3, -3), (-3, 3), (-3, -3)
]
EVASION_ENEMY_THRESHOLD = 8  # 持旗返回时，超过此距离不需要绕行
EVASION_CAPTURE_THRESHOLD = 10  # 夺旗时，超过此距离不需要绕行（更积极）

# 地图边界（根据 STRATEGY.md 的核心区定义）
MAP_BOUNDS = {
    "min_x": -24,
    "max_x": 24,
    "min_z": -36,
    "max_z": 36,
}

# 树叶方块集合（用于绕行避障）
LEAVES_BLOCKS = {
    "oak_leaves", "birch_leaves", "spruce_leaves", "jungle_leaves",
    "acacia_leaves", "dark_oak_leaves", "mangrove_leaves", "cherry_leaves",
    "azalea_leaves", "flowering_azalea_leaves", "oak_log"
}


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


@dataclass(frozen=True)
class EvasionWaypoint:
    """绕行路径点"""
    position: GridPosition
    score: float
    reason: str


# =============================================================================
# EliteCTFStrategy v2.0 - 融合版
# =============================================================================

@dataclass
class EliteCTFStrategy:
    """
    精英夺旗策略 v2.0 - 融合 OptimalCTFStrategy 核心算法
    
    核心特性：
    1. 动态角色分配（进攻/防守/支援）
    2. 智能绕行系统（calculate_evasion_waypoint）
    3. 预测性拦截（predict_enemy_target）
    4. 僵持检测与目标切换（_is_stalemate）
    5. 地图自适应（fixed/random）
    6. 快速脱困（卡位检测与逃脱）
    """
    
    # ========== 角色与状态 ==========
    role: Role = field(default_factory=lambda: Role.ATTACKER)
    state: State = field(default_factory=lambda: State.IDLE)
    
    # ========== 目标管理 ==========
    current_objective: Objective | None = None
    objective_hold_ticks: int = 0
    last_declared_intent: tuple[str, int, int] | None = None
    
    # ========== 卡位检测 ==========
    last_position: GridPosition | None = None
    stuck_ticks: int = 0
    
    # ========== 角色切换冷却 ==========
    role_switch_cooldown: int = 0
    
    # ========== 持旗确认 ==========
    return_home_ticks: int = 0
    had_flag_last_tick: bool = False
    
    # ========== 随机数生成器 ==========
    rng: random.Random = field(default_factory=random.Random)
    
    # ========== 调试模式 ==========
    verbose: bool = True
    
    # ========== 半场判断（动态确定）==========
    my_half_negative: Optional[bool] = None
    
    # ========== 插旗后冷却 ==========
    escape_target: Optional[Tuple[int, int]] = None
    post_plant_cooldown: int = 0
    
    # ========== 预防性绕树避障 ==========
    last_pos_float: Optional[Tuple[float, float]] = None
    stuck_ticks_avoidance: int = 0
    avoidance_target: Optional[Tuple[int, int]] = None
    
    # ========== 敌方跟踪 ==========
    enemy_position_history: dict[str, list[GridPosition]] = field(default_factory=dict)
    enemy_stuck_ticks: dict[str, int] = field(default_factory=dict)
    
    # ========== 立即卡位检测 ==========
    micro_stuck_ticks: int = 0
    last_micro_check_pos: Optional[GridPosition] = None
    
    # ========== 移动意图检测 ==========
    movement_intent_start_pos: Optional[GridPosition] = None
    movement_intent_start_tick: int = 0
    movement_intent_threshold: int = 2
    
    # ========== 新增：OptimalCTFStrategy 元素 ==========
    # 僵持检测相关
    _enemy_stalemate_start: float | None = field(default=None, repr=False)
    _switch_target: bool = field(default=False, repr=False)
    _current_attack_target: tuple[int, int] | None = field(default=None, repr=False)
    
    # 敌方压力板位置（避开）
    _enemy_pressure_plate: tuple[int, int] | None = field(default=None, repr=False)
    
    # 地图类型检测
    _is_fixed_map: bool = field(default=False, repr=False)
    
    # ========== 聊天冷却 ==========
    _last_chat_time: float = field(default=0.0, repr=False)
    _chat_cooldown_seconds: float = field(default=3.0, repr=False)
    _pending_chat_message: str | None = field(default=None, repr=False)
    
    # ========== 追逐状态跟踪 ==========
    _chasing_target_name: str | None = field(default=None, repr=False)  # 当前追逐的敌人名字
    _chasing_target_last_x: int | None = field(default=None, repr=False)  # 追逐目标上次x坐标
    _chasing_start_x: int | None = field(default=None, repr=False)  # 追逐开始时的x坐标（用于检测跨越中界）
    
    def on_game_start(self, obs: Observation) -> None:
        """游戏开始时初始化"""
        # 基础状态重置
        self.role = Role.ATTACKER
        self.state = State.IDLE
        self.current_objective = None
        self.objective_hold_ticks = 0
        self.last_declared_intent = None
        self.last_position = obs.me.position
        self.stuck_ticks = 0
        self.role_switch_cooldown = 0
        self.return_home_ticks = 0
        
        # 半场判断
        if obs.my_targets:
            self.my_half_negative = obs.my_targets[0].grid_position.x < 0
        else:
            self.my_half_negative = obs.team == "L"
        
        # 插旗冷却重置
        self.had_flag_last_tick = False
        self.escape_target = None
        self.post_plant_cooldown = 0
        
        # 避障状态重置
        self.last_pos_float = None
        self.stuck_ticks_avoidance = 0
        self.avoidance_target = None
        
        # 敌方跟踪重置
        self.enemy_position_history = {}
        self.enemy_stuck_ticks = {}
        
        # 卡位检测重置
        self.micro_stuck_ticks = 0
        self.last_micro_check_pos = None
        self.movement_intent_start_pos = None
        self.movement_intent_start_tick = 0
        
        # ===== 新增：OptimalCTFStrategy 元素初始化 =====
        self._enemy_stalemate_start = None
        self._switch_target = False
        self._current_attack_target = None
        
        # 敌方压力板位置
        my_team = obs.team
        if my_team == "L":
            self._enemy_pressure_plate = (16, 24)
        else:
            self._enemy_pressure_plate = (-16, 24)
        
        # 地图类型检测（根据金块位置判断）
        self._detect_map_type(obs)
        
        if self.verbose:
            print(f"[EliteCTF v2.0] Game started! Team={my_team}, "
                  f"FixedMap={self._is_fixed_map}, PressurePlate={self._enemy_pressure_plate}")
    
    def _detect_map_type(self, obs: Observation) -> None:
        """检测地图类型（fixed 或 random）"""
        if not obs.gold_blocks:
            self._is_fixed_map = False
            return
        
        # fixed 地图的金块在固定位置：x=±22, z=6,10,14,18,22,26,30,34
        gold_positions = [(b.grid_position.x, b.grid_position.z) for b in obs.gold_blocks]
        fixed_z_positions = {6, 10, 14, 18, 22, 26, 30, 34}
        
        # 检查是否有金块在固定位置
        fixed_pattern_matches = 0
        for x, z in gold_positions:
            if abs(x) == 22 and z in fixed_z_positions:
                fixed_pattern_matches += 1
        
        # 如果超过6个金块匹配固定模式，认为是 fixed 地图
        self._is_fixed_map = fixed_pattern_matches >= 6
    
    # ========================================================================
    # 主决策函数
    # ========================================================================
    
    def compute_next_action(self, obs: Observation) -> list[Action]:
        """
        主决策函数 - 每 tick 调用
        
        决策优先级（融合 OptimalCTFStrategy 元素）：
        1. 越狱逃脱（最高优先级）
        2. 持旗返回（最高优先级，使用智能绕行）
        3. 插旗后冷却/脱离
        4. 预防性绕树避障
        5. 卡位脱困
        6. 拦截携旗敌人（使用预测性拦截）
        7. 评估抓捕空载敌人
        8. 救援队友
        9. 夺旗（使用僵持检测）
        10. 防守/中场控制
        """
        me = obs.self_player
        
        # 更新敌方跟踪
        self._update_enemy_tracking(obs)
        
        # 插旗检测与冷却处理
        just_planted = self.had_flag_last_tick and not me.has_flag
        self.had_flag_last_tick = me.has_flag
        
        if just_planted:
            self._handle_flag_planted(me)
        
        # 冷却递减
        if self.post_plant_cooldown > 0:
            self.post_plant_cooldown -= 1
        
        # 优先执行脱离移动
        if self.escape_target is not None:
            if self._is_near_position(me.position, self.escape_target[0], self.escape_target[1], 1):
                self.escape_target = None
            else:
                return [self._create_move(
                    GridPosition(x=self.escape_target[0], z=self.escape_target[1]),
                    "Moving away from planted block",
                    radius=0
                )]
        
        # 预防性绕树避障
        avoidance_action = self._handle_tree_avoidance(me)
        if avoidance_action:
            return avoidance_action
        
        # 1. 监狱逃脱
        if me.in_prison:
            return self._escape_prison(obs)
        
        # 2. 持旗返回（使用智能绕行）
        if me.has_flag:
            self.state = State.CARRYING
            return self._return_flag_with_evasion(obs)
        
        # 3. 移动意图卡位检测
        intent_stuck_action = self._check_movement_intent_stuck(obs)
        if intent_stuck_action is not None:
            return intent_stuck_action
        
        # 4. 常规卡位检测
        escape_action = self._try_escape_if_stuck(obs)
        if escape_action is not None:
            return escape_action
        
        # 5. 僵持检测（可能设置 _switch_target）
        self._check_stalemate(obs)
        
        # 6. 动态角色分配
        if self.role_switch_cooldown <= 0:
            self._update_role(obs)
        else:
            self.role_switch_cooldown -= 1
        
        # 7. 拦截携旗敌人（使用预测性拦截）
        enemy_carriers = self._get_active_enemy_carriers(obs)
        if enemy_carriers:
            closest_carrier = min(enemy_carriers,
                                 key=lambda e: _manhattan_distance(me.position, e.position))
            if self._should_intercept(obs, closest_carrier):
                intercept_action = self._intercept_enemy_with_prediction(obs, closest_carrier)
                if intercept_action:
                    return intercept_action
        
        # 8. 评估抓捕空载敌人（己方区域积极追捕）
        capture_action = self._evaluate_capture_empty_enemy(obs)
        if capture_action:
            return capture_action
        
        # 9. 检查是否需要救援
        if self._should_rescue_aggressive(obs):
            return self._rescue_teammate(obs)
        
        # 10. 夺旗（使用僵持检测）或防守
        if self.role == Role.ATTACKER or self._should_prioritize_attack(obs):
            return self._capture_flag_with_stalemate(obs)
        elif self.role == Role.DEFENDER:
            return self._defend_base(obs)
        else:
            return self._control_midfield(obs)
    
    # ========================================================================
    # 新增：OptimalCTFStrategy 核心方法
    # ========================================================================
    
    def _calculate_evasion_waypoint(
        self,
        me_pos: GridPosition,
        target_pos: GridPosition,
        enemies: tuple[PlayerState, ...],
        obs: Observation,
        threshold: int | None = None
    ) -> GridPosition | None:
        """
        智能绕行点计算（来自 OptimalCTFStrategy）
        
        在持旗返回或夺旗时，计算一个安全的绕行点，
        既能避开敌人又能接近目标。
        
        Args:
            threshold: 触发绕行的距离阈值，默认使用 EVASION_ENEMY_THRESHOLD
        """
        if threshold is None:
            threshold = EVASION_ENEMY_THRESHOLD
        
        # 筛选危险敌人：所有未坐牢的敌人（无论他们在哪个半场）
        # 在敌方区域时，任何能动的敌人都是威胁！
        dangerous_enemies = [
            e for e in enemies
            if not e.in_prison
        ]
        
        if not dangerous_enemies:
            return None
        
        # 找到最近的危险敌人
        closest_enemy = min(dangerous_enemies,
                           key=lambda e: _manhattan_distance(me_pos, e.position))
        enemy_distance = _manhattan_distance(me_pos, closest_enemy.position)
        
        # 如果敌人距离超过阈值，不需要绕行
        if enemy_distance > threshold:
            return None
        
        # 生成候选点
        best_point = None
        best_score = -float('inf')
        
        # 基础方向 + 敌方半场额外加成
        directions = list(EVASION_CANDIDATE_DIRECTIONS)
        if self._is_enemy_half(me_pos.x):
            # 向中线移动的方向
            directions.append((-me_pos.x, 0))
            directions.append((-me_pos.x // 2, 0))
        
        for dx, dz in directions:
            cx, cz = me_pos.x + dx, me_pos.z + dz
            
            # 边界检查
            if not self._is_in_map_bounds(cx, cz):
                continue
            
            # 避开树叶
            if self._is_near_leaves(GridPosition(x=cx, z=cz), obs, min_distance=1):
                continue
            
            candidate = GridPosition(x=cx, z=cz)
            
            # 计算评分
            e_dist = _manhattan_distance(candidate, closest_enemy.position)
            t_dist = _manhattan_distance(candidate, target_pos)
            
            # 中线奖励：在敌方半场时鼓励向中线移动
            midline_bonus = 0
            if self._is_enemy_half(me_pos.x):
                midline_bonus = -abs(cx)
            
            # 评分公式：优先远离敌人，其次接近目标，最后考虑中线
            score = e_dist * 2.0 - t_dist + midline_bonus * 0.5
            
            if score > best_score:
                best_score = score
                best_point = candidate
        
        return best_point
    
    def _predict_enemy_target(self, enemy: PlayerState, obs: Observation) -> GridPosition | None:
        """
        敌人目标预测（来自 OptimalCTFStrategy）
        
        预测敌人（携旗者或空载）的目标位置，用于提前拦截。
        
        Args:
            enemy: 敌人状态
            obs: 游戏观测
            
        Returns:
            GridPosition | None: 预测的目标位置
        """
        if enemy.has_flag:
            # 敌人持旗：预测其要去的金块（敌方半场的空金块）
            their_golds = [
                b for b in obs.gold_blocks
                if self._is_enemy_half(b.grid_position.x)
            ]
            closest_gold = self._pick_closest_block(enemy.position, their_golds)
            return closest_gold.grid_position if closest_gold else None
        else:
            # 敌人空载：预测其要夺的旗帜（我方旗帜）
            if not obs.flags_to_protect:
                return None
            closest_flag = self._pick_closest_block(enemy.position, obs.flags_to_protect)
            return closest_flag.grid_position if closest_flag else None
    
    def _check_stalemate(self, obs: Observation) -> bool:
        """
        僵持检测（来自 OptimalCTFStrategy）
        
        检测是否在敌方半场与敌人陷入长时间对峙，
        如果是则设置 _switch_target 标志，触发目标切换。
        
        Returns:
            bool: 是否处于僵持状态
        """
        me = obs.self_player
        
        # 必须在敌方半场
        if not self._is_enemy_half(me.position.x):
            self._enemy_stalemate_start = None
            return False
        
        # 必须有敌人在附近
        if not obs.enemies:
            self._enemy_stalemate_start = None
            return False
        
        # 计算最近敌人距离
        nearest_dist = min(
            _manhattan_distance(me.position, e.position)
            for e in obs.enemies
        )
        
        if nearest_dist > STALEMATE_ENEMY_DISTANCE:
            self._enemy_stalemate_start = None
            return False
        
        # 计时逻辑
        now = time.time()
        if self._enemy_stalemate_start is None:
            self._enemy_stalemate_start = now
            return False
        else:
            elapsed = now - self._enemy_stalemate_start
            if elapsed >= STALEMATE_TIMEOUT:
                if self.verbose:
                    print(f"[EliteCTF v2.0] Stalemate detected ({elapsed:.1f}s), switching target")
                self._enemy_stalemate_start = None
                self._switch_target = True
                return True
            return False
    
    def _avoid_enemy_pressure_plate(self, target: tuple[int, int], my_team: str) -> tuple[int, int]:
        """
        避开敌方监狱压力板（避免触发重置计时器）
        
        Args:
            target: 目标位置 (x, z)
            my_team: 我方队伍
            
        Returns:
            tuple[int, int]: 调整后的目标位置
        """
        if self._enemy_pressure_plate is None:
            return target
        
        px, pz = target
        ex, ez = self._enemy_pressure_plate
        
        # 计算到压力板的距离
        dist = abs(px - ex) + abs(pz - ez)
        
        # 如果距离大于2，不需要调整
        if dist > 2:
            return target
        
        # 计算远离方向
        dx = 0 if px == ex else (1 if px > ex else -1)
        dz = 0 if pz == ez else (1 if pz > ez else -1)
        
        # 如果没有明确方向，默认向己方半场移动
        if dx == 0 and dz == 0:
            dx = -1 if my_team == "L" else 1
            dz = 0
        
        # 调整目标位置
        new_x = max(MAP_BOUNDS["min_x"], min(MAP_BOUNDS["max_x"], px + dx))
        new_z = max(MAP_BOUNDS["min_z"], min(MAP_BOUNDS["max_z"], pz + dz))
        
        if self.verbose:
            print(f"[EliteCTF v2.0] Avoiding pressure plate ({ex},{ez}): "
                  f"({px},{pz}) -> ({new_x},{new_z})")
        
        return (new_x, new_z)
    
    # ========================================================================
    # 重构：融合新方法的核心逻辑
    # ========================================================================
    
    def _return_flag_with_evasion(self, obs: Observation) -> list[Action]:
        """
        持旗返回（融合智能绕行）
        
        策略：
        1. 只选择己方半场的目标金块
        2. 如果在敌方半场，使用 calculate_evasion_waypoint 计算绕行点
        3. 避开敌方压力板
        """
        me = obs.self_player
        self.return_home_ticks += 1
        
        # 只选择己方半场的目标点
        targets = [t for t in obs.my_targets if self._is_my_half(t.grid_position.x)]
        
        if not targets:
            # 没有目标点，去安全位置
            safe_pos = _get_safe_position(obs)
            actions: list[Action] = self._get_chat_actions("\u00A7a哎呀~ 没有可以放旗子的地方惹，先待在这里吧~ >_<")
            actions.append(self._create_move(safe_pos, "No target, holding", radius=1))
            return actions
        
        # 选择最近的目标点
        target = min(targets, key=lambda t: _euclidean_distance(me.position, t.grid_position))
        target_pos = target.grid_position
        
        # 如果在敌方半场，先检查是否需要绕行
        if self._is_enemy_half(me.position.x):
            # 避开敌方压力板
            adjusted_target = self._avoid_enemy_pressure_plate(
                (target_pos.x, target_pos.z), obs.team
            )
            target_pos = GridPosition(x=adjusted_target[0], z=adjusted_target[1])
            
            # 计算绕行点
            evasion_point = self._calculate_evasion_waypoint(
                me.position, target_pos, obs.enemies, obs
            )
            
            if evasion_point:
                actions: list[Action] = self._get_chat_actions(f"呀呀~ 发现敌人惹，正在绕行躲避的说~ >▽< 目标是 ({target_pos.x},{target_pos.z})")
                actions.append(self._create_move(
                    evasion_point,
                    f"Evading with flag (to {target_pos.x},{target_pos.z})",
                    radius=0,
                    sprint=True
                ))
                return actions
        
        actions: list[Action] = self._get_chat_actions(f"嘿嘿~ 带着旗帜跑路中~ 目的地是 ({target_pos.x},{target_pos.z}) 喵~ >ω<")
        actions.append(self._create_move(target_pos, "Returning flag", radius=0, sprint=True))
        return actions
    
    def _intercept_enemy_with_prediction(self, obs: Observation, enemy: PlayerState) -> list[Action] | None:
        """
        拦截敌人（融合预测性拦截）
        
        策略：
        1. 使用 predict_enemy_target 预测敌人目标
        2. 在敌人当前位置和目标位置之间选择拦截点（0.4/0.6加权）
        3. 优先在己方半场拦截
        4. 如果敌人跨越中界，放弃追逐
        """
        me = obs.self_player
        
        # 检查是否正在追逐此敌人，且敌人已跨越中界
        if self._chasing_target_name == enemy.name:
            current_x_sign = 1 if enemy.position.x >= 0 else -1
            last_x_sign = 1 if self._chasing_target_last_x >= 0 else -1
            
            # 如果敌人跨越中界，放弃追逐
            if current_x_sign != last_x_sign and abs(enemy.position.x) < 5:
                self._clear_chasing_state()
                return None
        
        # 记录追逐状态
        self._chasing_target_name = enemy.name
        self._chasing_target_last_x = enemy.position.x
        self._chasing_start_x = me.position.x
        
        # 预测敌人目标
        predicted_target = self._predict_enemy_target(enemy, obs)
        
        if predicted_target and not self._is_enemy_half(enemy.position.x):
            # 敌人不在敌方半场，使用预测拦截点
            # 0.4 * 敌人位置 + 0.6 * 目标位置
            intercept_x = int(enemy.position.x * 0.4 + predicted_target.x * 0.6)
            intercept_z = int(enemy.position.z * 0.4 + predicted_target.z * 0.6)
            intercept_point = GridPosition(x=intercept_x, z=intercept_z)
            
            # 确保拦截点在己方半场
            if self._is_enemy_half(intercept_point.x):
                # 退回到边界
                boundary_x = -2 if obs.team == "L" else 2
                intercept_point = GridPosition(x=boundary_x, z=enemy.position.z)
            
            # 避开树叶
            if self._is_near_leaves(intercept_point, obs):
                intercept_point = self._adjust_for_leaves(intercept_point)
            
            actions: list[Action] = self._get_chat_actions(f"发现 {enemy.name} 惹！预测他要去的方向~ 准备拦截惹！>ω<")
            actions.append(self._create_move(
                intercept_point,
                f"Intercepting {enemy.name} (predicted)",
                radius=1,
                sprint=True
            ))
            return actions
        else:
            # 无法预测或敌人在敌方半场，直接追击
            actions: list[Action] = self._get_chat_actions(f"看到 {enemy.name} 了！追上去给他一点颜色看看！>ω<")
            actions.append(self._create_move(
                enemy.position,
                f"Chasing {enemy.name}",
                radius=1,
                sprint=True
            ))
            return actions
    
    def _capture_flag_with_stalemate(self, obs: Observation) -> list[Action]:
        """
        夺旗（融合僵持检测）
        
        策略：
        1. 敌方区域优先：检查8格内是否有敌人，有则先逃避
        2. 获取可用旗帜
        3. 如果 _switch_target 为 True，排除当前攻击目标
        4. 选择最优旗帜
        """
        me = obs.self_player
        
        # 优先检查：在敌方区域且8格内有敌人，先逃避
        should_evade, enemy_pos = self._should_evade_in_enemy_territory(obs)
        if should_evade and enemy_pos:
            # 计算逃避点：远离敌人，向己方半场方向
            dx = me.position.x - enemy_pos.x
            dz = me.position.z - enemy_pos.z
            
            # 归一化并扩展
            dist = max(1, math.hypot(dx, dz))
            escape_x = int(me.position.x + (dx / dist) * 7)
            escape_z = int(me.position.z + (dz / dist) * 7)
            
            # 额外向己方半场移动
            if self._is_enemy_half(me.position.x):
                if self.my_half_negative:
                    escape_x = min(escape_x, me.position.x - 3)  # 向负方向移动
                else:
                    escape_x = max(escape_x, me.position.x + 3)  # 向正方向移动
            
            escape_point = _clamp_to_map(GridPosition(x=escape_x, z=escape_z), obs)
            
            actions: list[Action] = self._get_chat_actions(f"敌方区域发现敌人！先逃避再夺旗！距离 {_manhattan_distance(me.position, enemy_pos)} 格 >▽<")
            actions.append(self._create_move(escape_point, f"Evading enemy in enemy territory", radius=0, sprint=True, jump=True))
            return actions
        
        flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        
        # 只选择敌方半场的旗帜
        flags = tuple(
            flag for flag in flags
            if self._is_enemy_half(flag.grid_position.x)
        )
        
        if not flags:
            return self._defend_base(obs)
        
        # 处理目标切换（僵持检测触发）
        if self._switch_target and self._current_attack_target is not None:
            filtered = [
                f for f in flags
                if (f.grid_position.x, f.grid_position.z) != self._current_attack_target
            ]
            if filtered:
                flags = tuple(filtered)
                if self.verbose:
                    print(f"[EliteCTF v2.0] Switching target from {self._current_attack_target}")
            self._switch_target = False
        
        # 选择最优旗帜
        target_flag = self._pick_best_flag_aggressive(obs, flags)
        if target_flag is None:
            return self._defend_base(obs)
        
        # 记录当前攻击目标
        flag_pos = (target_flag.grid_position.x, target_flag.grid_position.z)
        self._current_attack_target = flag_pos
        
        # 避开敌方压力板
        if self._is_enemy_half(target_flag.grid_position.x):
            adjusted = self._avoid_enemy_pressure_plate(flag_pos, obs.team)
            target_pos = GridPosition(x=adjusted[0], z=adjusted[1])
        else:
            target_pos = target_flag.grid_position
        
        # 如果在敌方半场，检查是否需要绕行（使用更积极的阈值）
        me = obs.self_player
        if self._is_enemy_half(me.position.x):
            evasion_point = self._calculate_evasion_waypoint(
                me.position, target_pos, obs.enemies, obs,
                threshold=EVASION_CAPTURE_THRESHOLD
            )
            if evasion_point:
                actions: list[Action] = self._get_chat_actions("\u00A7a哎呀~ 敌人太多惹！先绕一下再夺取旗帜喵~ >▽<")
                actions.append(self._create_move(
                    evasion_point,
                    f"Evading to flag at {target_pos.x},{target_pos.z}",
                    radius=0,
                    sprint=True
                ))
                return actions
        
        actions: list[Action] = self._get_chat_actions(f"发现敌方旗帜惹！正在夺取中~ >ω< 目标是 ({target_pos.x},{target_pos.z})")
        actions.append(self._create_move(target_pos, "Capturing flag", radius=0, sprint=True))
        return actions
    
    # ========================================================================
    # 辅助方法（保留原 EliteCTFStrategy 的功能）
    # ========================================================================
    
    def _handle_flag_planted(self, me: PlayerState) -> None:
        """处理刚插旗成功的情况"""
        self.post_plant_cooldown = 3
        self.consecutive_captures = getattr(self, 'consecutive_captures', 0) + 1
        
        # 向敌方半场深处移动，准备继续夺旗
        direction = 1 if self._is_enemy_half(me.position.x) else -1
        escape_x = me.position.x + direction * 8
        escape_x = max(MAP_BOUNDS["min_x"], min(MAP_BOUNDS["max_x"], escape_x))
        self.escape_target = (int(escape_x), me.position.z)
        self.last_declared_intent = None
        self.stuck_ticks_avoidance = 0
        self.avoidance_target = None
    
    def _handle_tree_avoidance(self, me: PlayerState) -> list[Action] | None:
        """处理预防性绕树避障"""
        current_pos_float = (me.position.x, me.position.z)
        
        if self.last_pos_float is not None:
            dist_moved = math.hypot(
                current_pos_float[0] - self.last_pos_float[0],
                current_pos_float[1] - self.last_pos_float[1]
            )
            if dist_moved < 0.1:
                self.stuck_ticks_avoidance += 1
            else:
                self.stuck_ticks_avoidance = 0
        
        self.last_pos_float = current_pos_float
        
        # 如果已有绕行目标，继续执行
        if self.avoidance_target is not None:
            if self._is_near_position(me.position, self.avoidance_target[0], self.avoidance_target[1], 1):
                self.avoidance_target = None
                self.stuck_ticks_avoidance = 0
            else:
                actions: list[Action] = self._get_chat_actions("\u00A7a呀！被木头挡住了！换一个方向走啦~ >▽<")
                actions.append(self._create_move(
                    GridPosition(x=self.avoidance_target[0], z=self.avoidance_target[1]),
                    "Avoiding tree obstacle",
                    radius=0
                ))
                return actions
        
        # 检测到卡住且没有活跃绕行目标时，生成新的绕行点
        if self.stuck_ticks_avoidance > 3 and self.avoidance_target is None:
            direction = 1 if self.rng.random() > 0.5 else -1
            avoid_z = int(me.position.z + direction * 5)
            avoid_z = max(MAP_BOUNDS["min_z"], min(MAP_BOUNDS["max_z"], avoid_z))
            self.avoidance_target = (int(me.position.x), avoid_z)
            actions: list[Action] = self._get_chat_actions("\u00A7a嘿呀~ 发现树木障碍惹，正在绕行~ >ω<")
            actions.append(self._create_move(
                GridPosition(x=self.avoidance_target[0], z=self.avoidance_target[1]),
                "Avoiding tree obstacle",
                radius=0
            ))
            return actions
        
        # 冷却期间：持续向敌方半场深处移动
        if self.post_plant_cooldown > 0:
            target_x = 15 if self._is_enemy_half(me.position.x) else -15
            actions: list[Action] = self._get_chat_actions("\u00A7a插完旗子先溜一下惹~ >ω<")
            actions.append(self._create_move(
                GridPosition(x=target_x, z=me.position.z),
                "Post-plant cooldown",
                radius=0
            ))
            return actions
        
        return None
    
    def _is_near_position(self, pos: GridPosition, x: int, z: int, threshold: int) -> bool:
        """检查位置是否在目标点附近"""
        return abs(pos.x - x) <= threshold and abs(pos.z - z) <= threshold
    
    def _is_my_half(self, x: int) -> bool:
        """判断 X 坐标是否在己方半场"""
        if self.my_half_negative is None:
            return True
        return (x < 0) if self.my_half_negative else (x >= 0)
    
    def _is_enemy_half(self, x: int) -> bool:
        """判断 X 坐标是否在敌方半场"""
        return not self._is_my_half(x)
    
    def _is_in_map_bounds(self, x: int, z: int) -> bool:
        """检查坐标是否在地图范围内"""
        return (MAP_BOUNDS["min_x"] <= x <= MAP_BOUNDS["max_x"] and
                MAP_BOUNDS["min_z"] <= z <= MAP_BOUNDS["max_z"])
    
    def _is_near_leaves(self, pos: GridPosition, obs: Observation, min_distance: int = 1) -> bool:
        """检查位置是否靠近树叶方块"""
        for block in obs.blocks:
            if block.name.lower() in LEAVES_BLOCKS:
                if _manhattan_distance(block.grid_position, pos) <= min_distance:
                    return True
        return False
    
    def _adjust_for_leaves(self, pos: GridPosition) -> GridPosition:
        """调整位置以避开树叶"""
        new_x = max(MAP_BOUNDS["min_x"], min(MAP_BOUNDS["max_x"],
                                             pos.x + (1 if pos.x < 0 else -1)))
        new_z = max(MAP_BOUNDS["min_z"], min(MAP_BOUNDS["max_z"],
                                             pos.z + (1 if pos.z < 0 else -1)))
        return GridPosition(x=new_x, z=new_z)
    
    def _get_active_enemy_carriers(self, obs: Observation) -> list[PlayerState]:
        """获取活跃的携旗敌人（过滤被树叶卡住的）"""
        return [
            e for e in obs.enemies
            if e.has_flag and not self._is_enemy_stuck_in_leaves(e, obs)
        ]
    
    def _pick_closest_block(
        self,
        origin: GridPosition,
        blocks: list[BlockState]
    ) -> BlockState | None:
        """选择最近的方块"""
        if not blocks:
            return None
        return min(blocks, key=lambda b: _manhattan_distance(origin, b.grid_position))
    
    # ========================================================================
    # 其他方法（保持原 EliteCTFStrategy 的实现）
    # ========================================================================
    
    def _escape_prison(self, obs: Observation) -> list[Action]:
        """越狱策略"""
        me = obs.self_player
        
        # 如果还在危险区域（z >= 28），先远离压力板
        if me.position.z >= PRISON_DANGER_ZONE_Z - 1:
            safe_target = GridPosition(x=me.position.x, z=26)
            actions: list[Action] = self._get_chat_actions("\u00A7a被关起来了！(>_<) 正在尝试越狱的说...")
            actions.append(self._create_move(safe_target, "Escaping danger zone", radius=0))
            return actions
        
        # 向监狱出口移动
        exit_target = PRISON_EXIT_TARGET[obs.team]
        actions: list[Action] = self._get_chat_actions("\u00A7a逃出来了喵！>ω< 正在离开危险区域~")
        actions.append(self._create_move(exit_target, "Escaping prison", radius=0))
        return actions
    
    def _check_movement_intent_stuck(self, obs: Observation) -> list[Action] | None:
        """检测移动意图与实际移动不符"""
        me = obs.self_player
        
        # 检查是否有活跃移动目标
        has_movement_intent = False
        if self.current_objective is not None:
            dist_to_target = _manhattan_distance(me.position, self.current_objective.target)
            if dist_to_target > 1:
                has_movement_intent = True
        
        if not has_movement_intent:
            self.movement_intent_start_pos = None
            self.movement_intent_start_tick = 0
            return None
        
        # 开始或继续跟踪
        if self.movement_intent_start_pos is None:
            self.movement_intent_start_pos = me.position
            self.movement_intent_start_tick = 1
            return None
        
        # 计算位移
        dx = abs(me.position.x - self.movement_intent_start_pos.x)
        dz = abs(me.position.z - self.movement_intent_start_pos.z)
        
        # 如果已经移动超过1格，重置
        if dx > 1 or dz > 1:
            self.movement_intent_start_pos = me.position
            self.movement_intent_start_tick = 0
            return None
        
        # 未移动超过1格，增加计数
        self.movement_intent_start_tick += 1
        
        # 持续多个tick未移动超过1格，判定为卡住
        if self.movement_intent_start_tick >= self.movement_intent_threshold:
            self.movement_intent_start_pos = None
            self.movement_intent_start_tick = 0
            
            if self.verbose:
                print(f"[EliteCTF v2.0] Movement intent stuck detected")
            return self._escape_from_movement_stuck(obs)
        
        return None
    
    def _escape_from_movement_stuck(self, obs: Observation) -> list[Action]:
        """针对移动意图卡住的快速脱困 - 综合处理动物、树叶和硬障碍物"""
        me = obs.self_player
        actions: list[Action] = []
        
        # 检测附近动物
        nearby_animals = [
            e for e in obs.entities
            if e.entity_type == "animal"
            and _manhattan_distance(e.grid_position, me.position) <= 2
        ]
        
        # 检测周围树叶
        leaves_blocks = [
            b for b in obs.blocks
            if self._is_leaves_block(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 1
        ]
        
        # 检测硬障碍物（栅栏等）
        hard_obstacles = [
            b for b in obs.blocks
            if _is_hard_block_name(b.name) and not self._is_leaves_block(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 2
        ]
        
        # 综合计算逃逸向量
        escape_x, escape_z = 0, 0
        has_obstacles = False
        
        # 远离动物
        if nearby_animals:
            has_obstacles = True
            for animal in nearby_animals:
                dx = me.position.x - animal.grid_position.x
                dz = me.position.z - animal.grid_position.z
                dist = max(1, math.hypot(dx, dz))
                weight = 1.5 / dist
                escape_x += dx * weight
                escape_z += dz * weight
        
        # 远离树叶
        if leaves_blocks:
            has_obstacles = True
            for leaf in leaves_blocks:
                dx = me.position.x - leaf.grid_position.x
                dz = me.position.z - leaf.grid_position.z
                escape_x += dx
                escape_z += dz
        
        # 远离硬障碍物（权重更高）
        if hard_obstacles:
            has_obstacles = True
            for obstacle in hard_obstacles:
                dx = me.position.x - obstacle.grid_position.x
                dz = me.position.z - obstacle.grid_position.z
                escape_x += dx * 2
                escape_z += dz * 2
        
        if has_obstacles:
            # 确保有足够的逃逸分量
            if abs(escape_z) < 0.5:
                escape_z = 5 if me.position.z <= 0 else -5
            
            magnitude = math.hypot(escape_x, escape_z)
            if magnitude > 0:
                scale = 6 / magnitude
                escape_x *= scale
                escape_z *= scale
            
            target = GridPosition(
                x=int(me.position.x + escape_x),
                z=int(me.position.z + escape_z)
            )
            target = _clamp_to_map(target, obs)
            
            # 确定消息类型
            if nearby_animals and (leaves_blocks or hard_obstacles):
                msg = "被动物和障碍物卡住了！(>_<) 正在综合脱困~"
            elif nearby_animals:
                msg = "怎么走不动了！(>_<) 换个方向试试~"
            elif leaves_blocks:
                msg = "被树叶挡住了！(>_<) 换个方向走~"
            else:
                msg = "被障碍物挡住了！(>_<) 正在绕行~"
            
            chat = self._try_send_chat(msg)
            actions: list[Action] = [chat] if chat else []
            actions.append(MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True))
            return actions
        
        # 没有检测到明显障碍，向垂直方向移动
        if self.current_objective is not None:
            target = self.current_objective.target
            main_dx = target.x - me.position.x
            main_dz = target.z - me.position.z
            
            if abs(main_dx) > abs(main_dz):
                escape_z = 6 if me.position.z <= 0 else -6
                target = GridPosition(x=me.position.x, z=me.position.z + escape_z)
            else:
                escape_x = 6 if self._is_enemy_half(me.position.x) else -6
                target = GridPosition(x=me.position.x + escape_x, z=me.position.z)
        else:
            directions = [(0, 5), (0, -5), (5, 0), (-5, 0)]
            dx, dz = self.rng.choice(directions)
            target = GridPosition(x=me.position.x + dx, z=me.position.z + dz)
        
        target = _clamp_to_map(target, obs)
        actions: list[Action] = self._get_chat_actions("\u00A7a怎么走不动了Σ(°△°) 一定是障碍物的问题！正在尝试其他方向~")
        actions.append(MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True))
        return actions
    
    def _try_escape_if_stuck(self, obs: Observation) -> list[Action] | None:
        """常规卡位检测"""
        me = obs.self_player
        
        if self.last_position is None:
            self.last_position = me.position
            self.stuck_ticks = 0
            return None
        
        distance_moved = _euclidean_distance(self.last_position, me.position)
        
        if distance_moved < STUCK_DISTANCE_THRESHOLD:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
            self.last_position = me.position
        
        if self.stuck_ticks < STUCK_THRESHOLD_TICKS:
            return None
        
        self.stuck_ticks = 0
        return self._escape_from_stuck(obs)
    
    def _escape_from_stuck(self, obs: Observation) -> list[Action]:
        """常规脱困 - 同时处理动物和障碍物"""
        me = obs.self_player
        actions: list[Action] = []
        
        # 检测附近的动物（扩大检测范围）
        nearby_animals = [
            e for e in obs.entities
            if e.entity_type == "animal"
            and _manhattan_distance(e.grid_position, me.position) <= 3
        ]
        
        nearby_obstacles = [
            b for b in obs.blocks
            if _is_hard_block_name(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 2
        ]
        
        leaves_escape = self._escape_from_leaves(obs)
        if leaves_escape:
            return leaves_escape
        
        # 如果只有大量动物（>3），使用专门的哞菇脱困逻辑
        if len(nearby_animals) > 3 and not nearby_obstacles:
            return self._escape_from_mooshrooms(obs, nearby_animals)
        
        # 计算综合逃逸向量（同时考虑动物和障碍物）
        escape_x, escape_z = 0, 0
        
        # 远离动物（权重较低）
        for animal in nearby_animals:
            dx = me.position.x - animal.grid_position.x
            dz = me.position.z - animal.grid_position.z
            dist = max(1, math.hypot(dx, dz))
            weight = 2.0 / dist  # 距离越近权重越高
            escape_x += dx * weight
            escape_z += dz * weight
        
        # 远离障碍物（权重较高，因为它们更硬）
        for obstacle in nearby_obstacles:
            dx = me.position.x - obstacle.grid_position.x
            dz = me.position.z - obstacle.grid_position.z
            escape_x += dx * 2.5
            escape_z += dz * 2.5
        
        # 如果没有明显威胁，随机选择一个方向
        if abs(escape_x) < 0.1 and abs(escape_z) < 0.1:
            directions = [(4, 0), (-4, 0), (0, 4), (0, -4), (3, 3), (-3, 3), (3, -3), (-3, -3)]
            dx, dz = self.rng.choice(directions)
            target = GridPosition(x=me.position.x + dx, z=me.position.z + dz)
        else:
            # 归一化并扩展逃逸向量
            magnitude = math.hypot(escape_x, escape_z)
            if magnitude > 0:
                scale = 6 / magnitude
                escape_x *= scale
                escape_z *= scale
            
            target = GridPosition(
                x=int(me.position.x + escape_x),
                z=int(me.position.z + escape_z)
            )
        
        target = _clamp_to_map(target, obs)
        
        # 确保目标点不会太近（至少移动4格）
        if _manhattan_distance(me.position, target) < 4:
            dz = 5 if me.position.z <= 0 else -5
            target = GridPosition(x=target.x, z=me.position.z + dz)
            target = _clamp_to_map(target, obs)
        
        actions: list[Action] = self._get_chat_actions("\u00A7a怎么卡住了！(>_<) 正在努力挣脱中...")
        actions.append(MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True))
        return actions
    
    def _escape_from_mooshrooms(self, obs: Observation, animals: list) -> list[Action]:
        """专门处理被哞菇卡住"""
        me = obs.self_player
        actions: list[Action] = []
        
        escape_x, escape_z = 0, 0
        
        for animal in animals:
            dx = me.position.x - animal.grid_position.x
            dz = me.position.z - animal.grid_position.z
            dist = max(1, math.hypot(dx, dz))
            weight = 3.0 / dist
            escape_x += dx * weight
            escape_z += dz * weight
        
        if abs(escape_x) < 0.1 and abs(escape_z) < 0.1:
            escape_x = -8 if self.my_half_negative else 8
            escape_z = 0
        
        magnitude = math.hypot(escape_x, escape_z)
        if magnitude > 0:
            scale = 8 / magnitude
            escape_x *= scale
            escape_z *= scale
        
        target = GridPosition(
            x=int(me.position.x + escape_x),
            z=int(me.position.z + escape_z)
        )
        target = _clamp_to_map(target, obs)
        
        if _manhattan_distance(me.position, target) < 4:
            dz = 5 if me.position.z <= 0 else -5
            target = GridPosition(x=target.x, z=me.position.z + dz)
            target = _clamp_to_map(target, obs)
        
        actions: list[Action] = self._get_chat_actions("\u00A7a好多蘑菇牛牛！Σ(°△°) 正在努力突围啦~ >ω<")
        actions.append(MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True))
        return actions
    
    def _escape_from_leaves(self, obs: Observation) -> list[Action] | None:
        """专门处理卡在树叶中"""
        me = obs.self_player
        
        leaves_blocks = [
            b for b in obs.blocks
            if self._is_leaves_block(b.name)
            and _manhattan_distance(b.grid_position, me.position) <= 1
        ]
        
        if not leaves_blocks:
            return None
        
        actions: list[Action] = []
        actions: list[Action] = self._get_chat_actions("\u00A7a被树叶埋住了！(>_<) 正在努力爬出来...")
        
        escape_x, escape_z = 0, 0
        
        for leaf in leaves_blocks:
            dx = me.position.x - leaf.grid_position.x
            dz = me.position.z - leaf.grid_position.z
            dist = max(1, abs(dx) + abs(dz))
            weight = 2.0 / dist
            escape_x += dx * weight
            escape_z += dz * weight
        
        if abs(escape_x) < 0.5 and abs(escape_z) < 0.5:
            escape_z = 5 if me.position.z <= 0 else -5
            escape_x = 3 if self._is_enemy_half(me.position.x) else -3
        
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
        
        actions.append(MoveTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True))
        return actions
    
    def _update_role(self, obs: Observation) -> None:
        """动态角色分配"""
        me = obs.self_player
        
        enemy_carriers = self._get_active_enemy_carriers(obs)
        jailed_teammates = [p for p in obs.teammates if p.in_prison]
        free_teammates = len(obs.teammates) - len(jailed_teammates) + 1
        
        new_role = self.role
        
        if enemy_carriers:
            closest_carrier = min(enemy_carriers,
                                 key=lambda e: _manhattan_distance(me.position, e.position))
            if self._is_my_half(closest_carrier.position.x):
                if self._is_my_half(me.position.x):
                    new_role = Role.DEFENDER
        
        elif len(jailed_teammates) >= 2 and free_teammates <= 2:
            prison_plate = PRISON_PRESSURE_PLATE[obs.team]
            if _manhattan_distance(me.position, prison_plate) <= 15:
                new_role = Role.SUPPORT
        
        else:
            new_role = Role.ATTACKER
        
        if new_role != self.role:
            self.role = new_role
            self.role_switch_cooldown = ROLE_SWITCH_COOLDOWN
            if self.verbose:
                print(f"[EliteCTF v2.0] Role changed to {self.role.name}")
    
    def _should_intercept(self, obs: Observation, carrier: PlayerState) -> bool:
        """判断是否值得拦截"""
        me = obs.self_player
        carrier_dist = _manhattan_distance(me.position, carrier.position)
        
        if self._is_my_half(carrier.position.x):
            return True
        
        if obs.my_targets:
            closest_target = min(obs.my_targets,
                                key=lambda t: _manhattan_distance(carrier.position, t.grid_position))
            if _manhattan_distance(carrier.position, closest_target.grid_position) <= 8:
                return carrier_dist <= 15
        
        if self._is_enemy_half(me.position.x):
            flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
            if flags:
                closest_flag = min(flags, key=lambda f: _euclidean_distance(me.position, f.grid_position))
                if _euclidean_distance(me.position, closest_flag.grid_position) <= 8:
                    return False
        
        if carrier_dist > 20:
            return False
        
        return True
    
    def _should_prioritize_attack(self, obs: Observation) -> bool:
        """判断是否应优先进攻"""
        me = obs.self_player
        
        if not obs.my_targets:
            return True
        
        if self._is_enemy_half(me.position.x):
            flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
            if flags:
                closest_flag = min(flags, key=lambda f: _euclidean_distance(me.position, f.grid_position))
                if _euclidean_distance(me.position, closest_flag.grid_position) <= 5:
                    return True
        
        if not any(e.has_flag for e in obs.enemies):
            return True
        
        return False
    
    def _evaluate_capture_empty_enemy(self, obs: Observation) -> list[Action] | None:
        """评估是否抓捕空载敌人 - 己方区域积极追捕，敌人跨越中界则放弃"""
        me = obs.self_player
        
        # 检查是否正在追逐某个目标
        if self._chasing_target_name is not None:
            # 查找正在追逐的目标
            chasing_enemy = None
            for e in obs.enemies:
                if e.name == self._chasing_target_name and not e.in_prison:
                    chasing_enemy = e
                    break
            
            if chasing_enemy:
                # 检测敌人是否跨越中界（x坐标符号改变）
                current_x_sign = 1 if chasing_enemy.position.x >= 0 else -1
                last_x_sign = 1 if self._chasing_target_last_x >= 0 else -1
                
                # 如果敌人跨越中界，放弃追逐
                if current_x_sign != last_x_sign and abs(chasing_enemy.position.x) < 5:
                    self._clear_chasing_state()
                    return None
                
                # 更新追逐目标位置
                self._chasing_target_last_x = chasing_enemy.position.x
                
                # 如果敌人在己方半场，继续追逐
                if self._is_my_half(chasing_enemy.position.x):
                    actions: list[Action] = self._get_chat_actions(f"{chasing_enemy.name} 别想跑！继续追！>ω<")
                    actions.append(self._create_move(chasing_enemy.position, f"Chasing {chasing_enemy.name} (pursuit)", radius=0, sprint=True, jump=True))
                    return actions
                else:
                    # 敌人回到敌方半场，放弃追逐
                    self._clear_chasing_state()
                    return None
            else:
                # 目标丢失或坐牢，清除状态
                self._clear_chasing_state()
        
        # 己方半场才积极追捕空载敌人
        if not self._is_my_half(me.position.x):
            return None
        
        empty_enemies = [
            e for e in obs.enemies
            if not e.in_prison and not e.has_flag and not self._is_enemy_stuck_in_leaves(e, obs)
        ]
        
        if not empty_enemies:
            return None
        
        best_target = None
        best_score = float('-inf')
        
        for enemy in empty_enemies:
            score = 0
            distance = _manhattan_distance(me.position, enemy.position)
            
            # 只在己方半场追捕
            if not self._is_my_half(enemy.position.x):
                continue
            
            # 距离评分 - 更激进的评分
            if distance <= 3:
                score += 100  # 极近距离，必追
            elif distance <= 6:
                score += 70
            elif distance <= 10:
                score += 40
            elif distance <= 15:
                score += 20
            else:
                score += 5
            
            # 敌人深入己方半场加分
            depth = abs(enemy.position.x)
            if self.my_half_negative:
                depth = abs(enemy.position.x) if enemy.position.x < 0 else 0
            else:
                depth = enemy.position.x if enemy.position.x > 0 else 0
            score += depth * 2
            
            # 阈值降低，更容易触发追捕
            threshold = 15 if distance <= 6 else 25
            
            if score > best_score and score >= threshold:
                best_score = score
                best_target = enemy
        
        if best_target:
            # 记录追逐状态
            self._chasing_target_name = best_target.name
            self._chasing_target_last_x = best_target.position.x
            self._chasing_start_x = me.position.x
            
            actions: list[Action] = self._get_chat_actions(f"发现 {best_target.name} 在己方区域！追上去送进监狱！>ω<")
            actions.append(self._create_move(best_target.position, f"Chasing {best_target.name} (aggressive)", radius=0, sprint=True, jump=True))
            return actions
        
        return None
    
    def _clear_chasing_state(self) -> None:
        """清除追逐状态"""
        self._chasing_target_name = None
        self._chasing_target_last_x = None
        self._chasing_start_x = None
    
    def _should_evade_in_enemy_territory(self, obs: Observation) -> tuple[bool, GridPosition | None]:
        """
        检查在敌方区域是否需要逃避
        
        Returns:
            (是否需要逃避, 最近的敌人位置)
        """
        me = obs.self_player
        
        # 只有在敌方半场才需要逃避检查
        if not self._is_enemy_half(me.position.x):
            return False, None
        
        # 寻找8格内的非坐牢敌人
        dangerous_enemies = [
            e for e in obs.enemies
            if not e.in_prison
            and _manhattan_distance(me.position, e.position) <= 8
        ]
        
        if not dangerous_enemies:
            return False, None
        
        # 找到最近的敌人
        closest_enemy = min(dangerous_enemies,
                           key=lambda e: _manhattan_distance(me.position, e.position))
        
        return True, closest_enemy.position
    
    def _should_rescue_aggressive(self, obs: Observation) -> bool:
        """判断是否应救援队友"""
        jailed_teammates = [p for p in obs.teammates if p.in_prison]
        if not jailed_teammates:
            return False
        
        me = obs.self_player
        
        flag_carriers_jailed = [p for p in jailed_teammates if p.has_flag]
        if flag_carriers_jailed:
            prison_plate = PRISON_PRESSURE_PLATE[obs.team]
            if _manhattan_distance(me.position, prison_plate) <= 12:
                return True
        
        free_teammates = [p for p in obs.teammates if not p.in_prison]
        if len(jailed_teammates) >= 2 and len(free_teammates) <= 1:
            prison_plate = PRISON_PRESSURE_PLATE[obs.team]
            if _manhattan_distance(me.position, prison_plate) <= 6:
                return True
        
        return False
    
    def _rescue_teammate(self, obs: Observation) -> list[Action]:
        """执行救援"""
        prison_plate = PRISON_PRESSURE_PLATE[obs.team]
        actions: list[Action] = self._get_chat_actions("\u00A7a队友被关起来了！(>_<) 正在前往救援惹~")
        actions.append(self._create_move(prison_plate, "Rescuing teammate", radius=0, sprint=True))
        return actions
    
    def _defend_base(self, obs: Observation) -> list[Action]:
        """基地防守"""
        if obs.my_targets:
            target = obs.my_targets[len(obs.my_targets) // 2]
            defend_pos = target.grid_position
        else:
            defend_pos = MIDFIELD_ANCHOR[obs.team]
        
        actions: list[Action] = self._get_chat_actions("\u00A7a现在要防守基地惹~ 乖乖待在这里等着敌人来~ >ω<")
        actions.append(self._create_move(defend_pos, "Defending base", radius=2, sprint=False))
        return actions
    
    def _control_midfield(self, obs: Observation) -> list[Action]:
        """中场控制"""
        midfield = MIDFIELD_ANCHOR[obs.team]
        actions: list[Action] = self._get_chat_actions("\u00A7a去中场看看有什么好玩的吧~ >ω<")
        actions.append(self._create_move(midfield, "Holding midfield", radius=2, sprint=False))
        return actions
    
    def _pick_best_flag_aggressive(self, obs: Observation, flags: tuple[BlockState, ...]) -> Optional[BlockState]:
        """选择最优夺旗目标（攻击性版本）"""
        me = obs.self_player
        
        valid_flags = [
            flag for flag in flags
            if _euclidean_distance(me.position, flag.grid_position) >= 0.5
        ]
        
        if not valid_flags:
            return None
        
        def flag_score_aggressive(flag: BlockState) -> float:
            distance = _euclidean_distance(me.position, flag.grid_position)
            safety_penalty = self._evaluate_flag_safety(flag, obs)
            
            score = distance * 0.7 + safety_penalty * 0.3
            
            if self._is_enemy_half(flag.grid_position.x):
                enemy_depth = abs(flag.grid_position.x) - 10
                if enemy_depth > 0:
                    score -= enemy_depth * 0.2
            
            return score
        
        return min(valid_flags, key=flag_score_aggressive)
    
    def _evaluate_flag_safety(self, flag: BlockState, obs: Observation) -> float:
        """评估夺旗后返回的安全性"""
        flag_pos = flag.grid_position
        
        targets = obs.my_targets
        if not targets:
            return float('inf')
        
        min_return_distance = min(
            _manhattan_distance(flag_pos, t.grid_position)
            for t in targets
        )
        
        nearby_enemies = sum(
            1 for e in obs.enemies
            if _manhattan_distance(e.position, flag_pos) <= 6
        )
        
        path_enemies = sum(
            1 for e in obs.enemies
            if self._is_my_half(e.position.x)
            and _manhattan_distance(e.position, flag_pos) <= 12
        )
        
        return min_return_distance * 0.3 + nearby_enemies * 2 + path_enemies * 1
    
    def _update_enemy_tracking(self, obs: Observation) -> None:
        """更新敌方位置跟踪"""
        ENEMY_HISTORY_SIZE = 10
        STUCK_DISTANCE_THRESHOLD = 0.5
        
        for enemy in obs.enemies:
            enemy_name = enemy.name
            current_pos = enemy.position
            
            if enemy_name not in self.enemy_position_history:
                self.enemy_position_history[enemy_name] = []
                self.enemy_stuck_ticks[enemy_name] = 0
            
            history = self.enemy_position_history[enemy_name]
            
            if history:
                last_pos = history[-1]
                distance = _euclidean_distance(last_pos, current_pos)
                
                if distance < STUCK_DISTANCE_THRESHOLD:
                    self.enemy_stuck_ticks[enemy_name] = self.enemy_stuck_ticks.get(enemy_name, 0) + 1
                else:
                    self.enemy_stuck_ticks[enemy_name] = 0
            
            history.append(current_pos)
            if len(history) > ENEMY_HISTORY_SIZE:
                history.pop(0)
    
    def _is_enemy_stuck_in_leaves(self, enemy: PlayerState, obs: Observation) -> bool:
        """判断敌人是否被树叶卡住"""
        enemy_name = enemy.name
        
        stuck_ticks = self.enemy_stuck_ticks.get(enemy_name, 0)
        if stuck_ticks < 5:
            return False
        
        for block in obs.blocks:
            if self._is_leaves_block(block.name):
                distance = _manhattan_distance(block.grid_position, enemy.position)
                if distance <= 2:
                    if self.verbose:
                        print(f"[EliteCTF v2.0] Enemy {enemy_name} stuck in leaves")
                    return True
        
        return False
    
    def _is_leaves_block(self, name: str) -> bool:
        """判断是否为树叶方块"""
        leaves_tokens = ("leaves", "oak_leaves", "spruce_leaves", "birch_leaves",
                        "jungle_leaves", "acacia_leaves", "dark_oak_leaves",
                        "mangrove_leaves", "azalea_leaves", "flowering_azalea_leaves")
        return any(token in name.lower() for token in leaves_tokens)
    
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
    
    def _try_send_chat(self, message: str) -> Chat | None:
        """尝试发送聊天消息（带冷却机制）
        
        如果冷却时间内，消息会被忽略。
        返回 Chat 动作或 None。
        """
        now = time.time()
        if now - self._last_chat_time >= self._chat_cooldown_seconds:
            self._last_chat_time = now
            return Chat(message=message)
        return None
    
    def _get_chat_actions(self, message: str) -> list[Action]:
        """获取包含聊天消息的动作列表（带冷却）
        
        如果冷却中，只返回空列表（不包含 Chat）。
        """
        chat = self._try_send_chat(message)
        return [chat] if chat else []


# =============================================================================
# 工具函数
# =============================================================================

def _manhattan_distance(left: GridPosition, right: GridPosition) -> int:
    """曼哈顿距离"""
    return abs(left.x - right.x) + abs(left.z - right.z)


def _euclidean_distance(left: GridPosition, right: GridPosition) -> float:
    """欧几里得距离"""
    return math.hypot(left.x - right.x, left.z - right.z)


def _clamp_to_map(position: GridPosition, obs: Observation) -> GridPosition:
    """将位置限制在地图范围内"""
    return GridPosition(
        x=max(obs.map.min_x, min(obs.map.max_x, position.x)),
        z=max(obs.map.min_z, min(obs.map.max_z, position.z)),
    )


def _get_safe_position(obs: Observation) -> GridPosition:
    """获取安全位置"""
    if obs.team == "L":
        return GridPosition(x=-15, z=0)
    else:
        return GridPosition(x=15, z=0)


def _is_hard_block_name(name: str) -> bool:
    """判断是否为硬障碍物"""
    hard_tokens = ("log", "leaves", "fence", "wall", "gate", "glass", "banner")
    return any(token in name.lower() for token in hard_tokens)


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
# 向后兼容
# =============================================================================

__all__ = [
    "EliteCTFStrategy",
]

"""
AdaptiveCTFStrategy - 对照组策略

作为功能测试时的对照组，保留原有的 AdaptiveCTFStrategy 实现。
可用于与 EliteCTFStrategy 进行性能对比测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState

if TYPE_CHECKING:
    from lib.actions import Action


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
    """判断点是否在线段附近"""
    min_x, max_x = min(start.x, end.x), max(start.x, end.x)
    min_z, max_z = min(start.z, end.z), max(start.z, end.z)

    if not (min_x - tolerance <= point.x <= max_x + tolerance):
        return False
    if not (min_z - tolerance <= point.z <= max_z + tolerance):
        return False

    segment_len = _manhattan_distance(start, end)
    point_to_start = _manhattan_distance(point, start)
    point_to_end = _manhattan_distance(point, end)

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
# AdaptiveCTFStrategy 使用的辅助函数
# =============================================================================

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


# =============================================================================
# AdaptiveCTFStrategy 类
# =============================================================================

@dataclass(frozen=True)
class _Objective:
    """AdaptiveCTFStrategy 使用的目标对象"""
    label: str
    target: GridPosition
    radius: int
    sprint: bool
    jump: bool = False


@dataclass
class AdaptiveCTFStrategy:
    """自适应夺旗策略 - 对照组策略"""
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
# 向后兼容
# =============================================================================

__all__ = [
    "AdaptiveCTFStrategy",
]

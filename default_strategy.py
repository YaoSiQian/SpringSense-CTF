from __future__ import annotations

import random
from dataclasses import dataclass, field

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation

LEFT_ENDPOINT = (-23, 0)
RIGHT_ENDPOINT = (23, 0)


@dataclass
class RandomWalkStrategy:
    current_target: tuple[int, int] | None = None
    rng: random.Random = field(default_factory=random.Random)

    def on_game_start(self, obs: Observation) -> None:
        if self.current_target is None:
            self.current_target = self.rng.choice((LEFT_ENDPOINT, RIGHT_ENDPOINT))

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        if self.current_target is None:
            self.on_game_start(obs)

        assert self.current_target is not None
        switched_direction = False
        if _is_near(obs.me.position.x, obs.me.position.z, self.current_target):
            self.current_target = (
                RIGHT_ENDPOINT if self.current_target == LEFT_ENDPOINT else LEFT_ENDPOINT
            )
            switched_direction = True

        actions: list[MoveTo | Chat] = []
        if switched_direction:
            actions.append(Chat(message=f"Switching direction toward x={self.current_target[0]}"))
        actions.append(MoveTo(x=self.current_target[0], z=self.current_target[1], radius=1))
        return actions


def _is_near(x: int, z: int, target: tuple[int, int], threshold: int = 2) -> bool:
    return abs(x - target[0]) <= threshold and abs(z - target[1]) <= threshold


@dataclass
class PickClosestFlagAndBackStrategy:
    radius: int = 0
    last_declared_intent: tuple[str, int, int] | None = None
    rng: random.Random = field(default_factory=random.Random)

    def on_game_start(self, obs: Observation) -> None:
        return None

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        my_player = obs.self_player
        actions: list[MoveTo | Chat] = []
        if my_player.has_flag:
            intent = "Returning flag"
            target_block = _pick_closest_block(obs.me.position, obs.my_targets)
        else:
            intent = "Capturing flag"
            target_block = _pick_closest_block(
                obs.me.position,
                _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions),
            )

        if target_block is None:
            declared_intent = ("Holding position", obs.me.position.x, obs.me.position.z)
            if declared_intent != self.last_declared_intent:
                actions.append(Chat(message="No valid flag or target found. Holding position."))
                self.last_declared_intent = declared_intent
            actions.append(MoveTo(x=obs.me.position.x, z=obs.me.position.z, radius=self.radius))
            return actions

        target = target_block.grid_position
        declared_intent = (intent, target_block.grid_position.x, target_block.grid_position.z)
        if declared_intent != self.last_declared_intent:
            actions.append(Chat(message=f"{intent} at ({target.x}, {target.z})"))
            self.last_declared_intent = declared_intent
        actions.append(MoveTo(x=target.x, z=target.z, radius=self.radius))

        return actions


def _pick_closest_block(
    origin: GridPosition,
    blocks: tuple[BlockState, ...],
) -> BlockState | None:
    if not blocks:
        return None
    return min(
        blocks,
        key=lambda block: (
            _manhattan_distance(origin, block.grid_position),
            block.grid_position.x,
            block.grid_position.z,
        ),
    )


def _manhattan_distance(left: GridPosition, right: GridPosition) -> int:
    return abs(left.x - right.x) + abs(left.z - right.z)


def _unplaced_flags(
    flags: tuple[BlockState, ...],
    gold_block_positions: tuple[GridPosition, ...],
) -> tuple[BlockState, ...]:
    gold_positions = {(position.x, position.z) for position in gold_block_positions}
    return tuple(
        flag
        for flag in flags
        if (flag.grid_position.x, flag.grid_position.z) not in gold_positions
    )

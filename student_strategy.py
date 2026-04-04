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

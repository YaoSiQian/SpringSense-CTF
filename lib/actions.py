from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class MoveTo:
    x: int
    z: int
    radius: int = 1
    sprint: bool = True


@dataclass(frozen=True, slots=True)
class Chat:
    message: str


Action: TypeAlias = MoveTo | Chat


__all__ = ["Action", "Chat", "MoveTo"]

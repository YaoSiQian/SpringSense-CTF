from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, cast

TeamName = Literal["L", "R"]

_TEAM_PREFIXES = ("L", "R")
_FILTERED_ENTITY_TYPES = {"passive", "other"}
_FILTERED_BLOCK_NAMES = {"dirt", "grass_block", "coarse_dirt"}
_PRISON_ZONES = {
    "L": {"min_x": -18, "max_x": -14, "min_z": 26, "max_z": 30},
    "R": {"min_x": 14, "max_x": 18, "min_z": 26, "max_z": 30},
}
_TEAM_CAPTURE_FLAG_BLOCK = {"L": "blue_banner", "R": "red_banner"}
_TEAM_PROTECT_FLAG_BLOCK = {"L": "red_banner", "R": "blue_banner"}


def opponent_team(team: TeamName) -> TeamName:
    return "R" if team == "L" else "L"


def infer_team_from_bot_name(bot_name: str) -> TeamName:
    prefix = bot_name.split("_", 1)[0]
    if prefix in _TEAM_PREFIXES:
        return cast(TeamName, prefix)
    if bot_name and bot_name[0] in _TEAM_PREFIXES:
        return cast(TeamName, bot_name[0])
    raise ValueError(
        f"Unable to infer team from bot name {bot_name!r}. "
        "Expected a prefix like 'L_Alice' or 'R_Bob'."
    )


def normalize_team_name(team: Any) -> TeamName | None:
    if team is None:
        return None
    value = str(team).strip()
    if not value:
        return None
    if value in _TEAM_PREFIXES:
        return cast(TeamName, value)

    normalized = value.lower().replace("-", " ").replace("_", " ")
    if normalized in {"l", "left", "red"}:
        return "L"
    if normalized in {"r", "right", "blue"}:
        return "R"

    tokens = set(normalized.split())
    if "left" in tokens or "red" in tokens:
        return "L"
    if "right" in tokens or "blue" in tokens:
        return "R"
    return None


def _load_json_like(source: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    path = Path(source)
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class GridPosition:
    x: int
    z: int

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "z": self.z}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GridPosition":
        return cls(x=int(payload["x"]), z=int(payload["z"]))


@dataclass(frozen=True, slots=True)
class Vec3:
    x: float
    y: float
    z: float

    @property
    def grid(self) -> GridPosition:
        return GridPosition(x=math.floor(self.x), z=math.floor(self.z))

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Vec3":
        return cls(x=float(payload["x"]), y=float(payload["y"]), z=float(payload["z"]))


@dataclass(frozen=True, slots=True)
class BotState:
    name: str
    team: TeamName
    position: GridPosition
    world_position: Vec3
    is_self: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "team": self.team,
            "position": self.position.to_dict(),
            "world_position": self.world_position.to_dict(),
            "is_self": self.is_self,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BotState":
        team = normalize_team_name(payload["team"])
        if team is None:
            raise ValueError(f"Unable to normalize bot team from {payload['team']!r}.")
        return cls(
            name=str(payload["name"]),
            team=team,
            position=GridPosition.from_dict(payload["position"]),
            world_position=Vec3.from_dict(payload["world_position"]),
            is_self=bool(payload.get("is_self", False)),
        )


@dataclass(frozen=True, slots=True)
class EntityState:
    entity_id: int | None
    entity_type: str | None
    name: str | None
    username: str | None
    display_name: str | None
    object_type: str | None
    team: str | None
    position: Vec3

    @property
    def label(self) -> str:
        return self.username or self.display_name or self.name or "entity"

    @property
    def grid_position(self) -> GridPosition:
        return self.position.grid

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.entity_id,
            "type": self.entity_type,
            "name": self.name,
            "username": self.username,
            "display_name": self.display_name,
            "object_type": self.object_type,
            "team": self.team,
            "position": self.position.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EntityState":
        return cls(
            entity_id=payload.get("id"),
            entity_type=payload.get("type"),
            name=payload.get("name"),
            username=payload.get("username"),
            display_name=payload.get("display_name") or payload.get("displayName"),
            object_type=payload.get("object_type") or payload.get("display_name") or payload.get("displayName"),
            team=payload.get("team"),
            position=Vec3.from_dict(payload["position"]),
        )


@dataclass(frozen=True, slots=True)
class PlayerState:
    name: str
    team: TeamName | None
    position: GridPosition
    world_position: Vec3
    in_prison: bool = False
    has_flag: bool = False
    held_item_name: str | None = None
    is_self: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "team": self.team,
            "position": self.position.to_dict(),
            "world_position": self.world_position.to_dict(),
            "in_prison": self.in_prison,
            "has_flag": self.has_flag,
            "held_item_name": self.held_item_name,
            "is_self": self.is_self,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PlayerState":
        return cls(
            name=str(payload["name"]),
            team=normalize_team_name(payload.get("team")),
            position=GridPosition.from_dict(payload["position"]),
            world_position=Vec3.from_dict(payload["world_position"]),
            in_prison=bool(payload.get("in_prison", False)),
            has_flag=bool(payload.get("has_flag", False)),
            held_item_name=cast(str | None, payload.get("held_item_name")),
            is_self=bool(payload.get("is_self", False)),
        )


@dataclass(frozen=True, slots=True)
class BlockState:
    name: str
    position: Vec3
    display_name: str | None = None
    block_type: int | None = None
    bounding_box: str | None = None

    @property
    def grid_position(self) -> GridPosition:
        return self.position.grid

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "type": self.block_type,
            "bounding_box": self.bounding_box,
            "position": self.position.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BlockState":
        return cls(
            name=str(payload["name"]),
            position=Vec3.from_dict(payload["position"]),
            display_name=payload.get("display_name") or payload.get("displayName"),
            block_type=payload.get("type"),
            bounding_box=payload.get("bounding_box") or payload.get("boundingBox"),
        )


@dataclass(frozen=True, slots=True)
class FlagState:
    team: TeamName
    home_positions: tuple[GridPosition, ...] = ()
    available_positions: tuple[GridPosition, ...] = ()
    carried_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TeamLandmarks:
    team: TeamName
    flag_markers: tuple[GridPosition, ...] = ()
    capture_pads: tuple[GridPosition, ...] = ()
    prison_entries: tuple[GridPosition, ...] = ()
    prison_gate: GridPosition | None = None
    prison_cells: tuple[GridPosition, ...] = ()
    target_cells: tuple[GridPosition, ...] = ()


@dataclass(frozen=True, slots=True)
class Scoreboard:
    L: int = 0
    R: int = 0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Scoreboard":
        return cls(L=int(payload.get("L", 0)), R=int(payload.get("R", 0)))


@dataclass(frozen=True, slots=True)
class MapMetadata:
    min_x: int
    max_x: int
    min_z: int
    max_z: int
    plane_y: int = 1
    L_landmarks: TeamLandmarks = field(default_factory=lambda: TeamLandmarks(team="L"))
    R_landmarks: TeamLandmarks = field(default_factory=lambda: TeamLandmarks(team="R"))

    @classmethod
    def from_snapshot(cls, snapshot_source: Mapping[str, Any] | str | Path) -> "MapMetadata":
        snapshot = _load_json_like(snapshot_source)
        bounds = snapshot["bounds"]
        return cls(
            min_x=int(bounds["min_x"]),
            max_x=int(bounds["max_x"]),
            min_z=int(bounds["min_z"]),
            max_z=int(bounds["max_z"]),
            plane_y=int(snapshot.get("plane_y", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_x": self.min_x,
            "max_x": self.max_x,
            "min_z": self.min_z,
            "max_z": self.max_z,
            "plane_y": self.plane_y,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MapMetadata":
        return cls(
            min_x=int(payload["min_x"]),
            max_x=int(payload["max_x"]),
            min_z=int(payload["min_z"]),
            max_z=int(payload["max_z"]),
            plane_y=int(payload.get("plane_y", 1)),
        )


@dataclass(frozen=True, slots=True)
class Observation:
    tick_ms: float | None
    bot_name: str
    team: TeamName
    me: BotState
    players: tuple[PlayerState, ...]
    myteam_players: tuple[PlayerState, ...]
    opponent_players: tuple[PlayerState, ...]
    entities: tuple[EntityState, ...]
    blocks: tuple[BlockState, ...]
    gold_blocks: tuple[BlockState, ...]
    gold_block_positions: tuple[GridPosition, ...]
    flag_positions: tuple[GridPosition, ...]
    flags_to_capture: tuple[BlockState, ...]
    flags_to_protect: tuple[BlockState, ...]
    map: MapMetadata
    assigned_teams: tuple[tuple[str, TeamName], ...] = ()
    scores: Scoreboard = field(default_factory=Scoreboard)

    @property
    def my_team(self) -> TeamName:
        return self.team

    @property
    def enemy_team(self) -> TeamName:
        return opponent_team(self.team)

    @property
    def teammates(self) -> tuple[PlayerState, ...]:
        return tuple(
            entity
            for entity in self.myteam_players
            if entity.name != self.bot_name
        )

    @property
    def enemies(self) -> tuple[PlayerState, ...]:
        return self.opponent_players

    @property
    def self_player(self) -> PlayerState:
        player = next((player for player in self.players if player.name == self.bot_name), None)
        if player is None:
            raise ValueError(f"Bot {self.bot_name!r} is not present in observation players.")
        return player

    @property
    def my_targets(self) -> tuple[BlockState, ...]:
        occupied_positions = {
            (position.x, position.z)
            for position in self.flag_positions
        }
        return tuple(
            block
            for block in self.gold_blocks
            if _is_in_team_territory(block.grid_position, self.team)
            and (block.grid_position.x, block.grid_position.z) not in occupied_positions
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick_ms": self.tick_ms,
            "bot_name": self.bot_name,
            "team": self.team,
            "me": self.me.to_dict(),
            "players": [player.to_dict() for player in self.players],
            "myteam_players": [player.to_dict() for player in self.myteam_players],
            "opponent_players": [player.to_dict() for player in self.opponent_players],
            "entities": [entity.to_dict() for entity in self.entities],
            "blocks": [block.to_dict() for block in self.blocks],
            "gold_blocks": [block.to_dict() for block in self.gold_blocks],
            "gold_block_positions": [position.to_dict() for position in self.gold_block_positions],
            "flag_positions": [position.to_dict() for position in self.flag_positions],
            "flags_to_capture": [flag.to_dict() for flag in self.flags_to_capture],
            "flags_to_protect": [flag.to_dict() for flag in self.flags_to_protect],
            "map": self.map.to_dict(),
            "assigned_teams": dict(self.assigned_teams),
            "scores": {"L": self.scores.L, "R": self.scores.R},
        }

    def validate(self) -> "Observation":
        if self.me.name != self.bot_name:
            raise ValueError(
                f"Observation bot mismatch: me.name={self.me.name!r}, bot_name={self.bot_name!r}."
            )
        if self.me.team != self.team:
            raise ValueError(
                f"Observation team mismatch: me.team={self.me.team!r}, team={self.team!r}."
            )
        if not any(player.name == self.bot_name for player in self.players):
            raise ValueError(f"Bot {self.bot_name!r} is not present in snapshot player entities.")

        if not (self.map.min_x <= self.me.position.x <= self.map.max_x):
            raise ValueError(f"Bot x={self.me.position.x} is outside map bounds.")
        if not (self.map.min_z <= self.me.position.z <= self.map.max_z):
            raise ValueError(f"Bot z={self.me.position.z} is outside map bounds.")
        return self

    def patch_observation(self, delta_snapshot: Mapping[str, Any]) -> "Observation":
        if not delta_snapshot:
            return self

        team = self.team
        assigned_teams = dict(self.assigned_teams)
        previous_players_by_name = {player.name: player for player in self.players}

        bot_position = _vec3_from_position_payload(
            delta_snapshot.get("bot", {}).get("position"),
            fallback=self.me.world_position,
        )
        me = BotState(
            name=self.bot_name,
            team=team,
            position=bot_position.grid,
            world_position=bot_position,
            is_self=True,
        )

        players = self.players
        if "players" in delta_snapshot:
            players = tuple(
                _player_from_quick_payload(
                    player_payload,
                    bot_name=self.bot_name,
                    my_team=team,
                    assigned_teams=assigned_teams,
                    previous_player=previous_players_by_name.get(str(player_payload.get("username"))),
                )
                for player_payload in delta_snapshot.get("players", [])
                if player_payload.get("username") is not None
            )

        entities = self.entities
        if "animals" in delta_snapshot or "players" in delta_snapshot:
            entities = _patched_dynamic_entities(
                previous_entities=self.entities,
                players=players,
                animal_payloads=delta_snapshot.get("animals"),
            )

        blocks = self.blocks
        if "blocks" in delta_snapshot:
            banner_blocks = tuple(
                _block_from_snapshot(block)
                for block in delta_snapshot.get("blocks", [])
                if block.get("name") not in _FILTERED_BLOCK_NAMES
            )
            blocks = tuple(
                block
                for block in self.blocks
                if block.name not in {"blue_banner", "red_banner"}
            ) + banner_blocks

        myteam_players = tuple(player for player in players if player.team == team)
        opponent_players = tuple(
            player for player in players if player.team is not None and player.team != team
        )
        flags_to_capture = tuple(
            block for block in blocks if block.name == _TEAM_CAPTURE_FLAG_BLOCK[team]
        )
        flags_to_protect = tuple(
            block for block in blocks if block.name == _TEAM_PROTECT_FLAG_BLOCK[team]
        )

        object.__setattr__(self, "team", team)
        object.__setattr__(self, "me", me)
        object.__setattr__(self, "players", players)
        object.__setattr__(self, "myteam_players", myteam_players)
        object.__setattr__(self, "opponent_players", opponent_players)
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "gold_blocks", _collect_gold_blocks(blocks))
        object.__setattr__(self, "gold_block_positions", _collect_gold_block_positions(blocks))
        object.__setattr__(self, "flag_positions", _collect_flag_positions(blocks))
        object.__setattr__(self, "flags_to_capture", flags_to_capture)
        object.__setattr__(self, "flags_to_protect", flags_to_protect)
        object.__setattr__(self, "assigned_teams", tuple(sorted(assigned_teams.items())))
        return self

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Observation":
        team = normalize_team_name(payload["team"])
        if team is None:
            raise ValueError(f"Unable to normalize observation team from {payload['team']!r}.")
        blocks = tuple(BlockState.from_dict(item) for item in payload.get("blocks", []))
        return cls(
            tick_ms=float(payload["tick_ms"]) if payload.get("tick_ms") is not None else None,
            bot_name=str(payload["bot_name"]),
            team=team,
            me=BotState.from_dict(payload["me"]),
            players=tuple(PlayerState.from_dict(item) for item in payload.get("players", [])),
            myteam_players=tuple(
                PlayerState.from_dict(item) for item in payload.get("myteam_players", [])
            ),
            opponent_players=tuple(
                PlayerState.from_dict(item) for item in payload.get("opponent_players", [])
            ),
            entities=tuple(EntityState.from_dict(item) for item in payload.get("entities", [])),
            blocks=blocks,
            gold_blocks=tuple(BlockState.from_dict(item) for item in payload.get("gold_blocks", []))
            or _collect_gold_blocks(blocks),
            gold_block_positions=tuple(
                GridPosition.from_dict(item) for item in payload.get("gold_block_positions", [])
            )
            or _collect_gold_block_positions(blocks),
            flag_positions=tuple(
                GridPosition.from_dict(item) for item in payload.get("flag_positions", [])
            )
            or _collect_flag_positions(blocks),
            flags_to_capture=tuple(
                BlockState.from_dict(item) for item in payload.get("flags_to_capture", [])
            ),
            flags_to_protect=tuple(
                BlockState.from_dict(item) for item in payload.get("flags_to_protect", [])
            ),
            map=MapMetadata.from_dict(payload["map"]),
            assigned_teams=tuple(
                sorted(_normalize_assigned_teams(payload.get("assigned_teams")).items())
            ),
            scores=Scoreboard.from_dict(payload.get("scores", {})),
        )

    @classmethod
    def from_snapshot(
        cls,
        *,
        snapshot_source: Mapping[str, Any] | str | Path,
        bot_name: str,
        map_metadata: MapMetadata | None = None,
        assigned_teams: Mapping[str, Any] | None = None,
    ) -> "Observation":
        snapshot = _load_json_like(snapshot_source)
        metadata = map_metadata or MapMetadata.from_snapshot(snapshot)
        normalized_assigned_teams = _normalize_assigned_teams(assigned_teams)
        player_payload_by_name = {
            str(item["username"]): item
            for item in snapshot.get("players", [])
            if item.get("username") is not None
        }
        team = (
            normalized_assigned_teams.get(bot_name)
            or normalize_team_name(snapshot.get("bot", {}).get("team"))
            or normalize_team_name(player_payload_by_name.get(bot_name, {}).get("team"))
        )
        if team is None:
            raise ValueError("Unable to determine bot team from server snapshot.")

        entities: list[EntityState] = []
        players: list[PlayerState] = []
        for entity in snapshot.get("entities", []):
            position = entity.get("position")
            if position is None:
                continue
            entity_type = entity.get("type")
            if entity_type in _FILTERED_ENTITY_TYPES:
                continue

            entity_state = EntityState(
                entity_id=entity.get("id"),
                entity_type=entity_type,
                name=entity.get("name"),
                username=entity.get("username"),
                display_name=entity.get("displayName"),
                object_type=entity.get("displayName"),
                team=entity.get("team"),
                position=Vec3(
                    x=float(position["x"]),
                    y=float(position["y"]),
                    z=float(position["z"]),
                ),
            )
            entities.append(entity_state)
            if entity_state.entity_type == "player":
                player_position = entity_state.grid_position
                player_name = entity_state.username or entity_state.name or "player"
                player_payload = player_payload_by_name.get(player_name, {})
                held_item_name = _extract_held_item_name(entity, player_payload)
                players.append(
                    PlayerState(
                        name=player_name,
                        team=_resolve_player_team(
                            entity,
                            player_payload=player_payload,
                            bot_name=bot_name,
                            my_team=team,
                            assigned_teams=normalized_assigned_teams,
                        ),
                        position=player_position,
                        world_position=entity_state.position,
                        in_prison=_is_in_prison_zone(player_position),
                        has_flag=_resolve_has_flag(player_payload, held_item_name),
                        held_item_name=held_item_name,
                        is_self=entity_state.username == bot_name,
                    )
                )
        self_entity = next((entity for entity in players if entity.name == bot_name), None)
        if self_entity is not None:
            me = BotState(
                name=bot_name,
                team=self_entity.team or team,
                position=self_entity.position,
                world_position=self_entity.world_position,
                is_self=True,
            )
        else:
            bot_position_data = snapshot["bot"]["position"]
            bot_position = Vec3(
                x=float(bot_position_data["x"]),
                y=float(bot_position_data["y"]),
                z=float(bot_position_data["z"]),
            )
            me = BotState(
                name=bot_name,
                team=team,
                position=bot_position.grid,
                world_position=bot_position,
                is_self=True,
            )

        blocks = tuple(
            _block_from_snapshot(block)
            for block in snapshot.get("blocks", [])
            if block.get("name") not in _FILTERED_BLOCK_NAMES
        )
        myteam_players = tuple(player for player in players if player.team == team)
        opponent_players = tuple(
            player for player in players if player.team is not None and player.team != team
        )
        flags_to_capture = tuple(
            block for block in blocks if block.name == _TEAM_CAPTURE_FLAG_BLOCK[team]
        )
        flags_to_protect = tuple(
            block for block in blocks if block.name == _TEAM_PROTECT_FLAG_BLOCK[team]
        )
        gold_blocks = _collect_gold_blocks(blocks)
        gold_block_positions = _collect_gold_block_positions(blocks)
        flag_positions = _collect_flag_positions(blocks)
        return cls(
            tick_ms=None,
            bot_name=bot_name,
            team=team,
            me=me,
            players=tuple(players),
            myteam_players=myteam_players,
            opponent_players=opponent_players,
            entities=tuple(entities),
            blocks=blocks,
            gold_blocks=gold_blocks,
            gold_block_positions=gold_block_positions,
            flag_positions=flag_positions,
            flags_to_capture=flags_to_capture,
            flags_to_protect=flags_to_protect,
            map=metadata,
            assigned_teams=tuple(sorted(normalized_assigned_teams.items())),
        )


def _block_from_snapshot(block: Mapping[str, Any]) -> BlockState:
    position = block["position"]
    return BlockState(
        name=str(block["name"]),
        display_name=block.get("displayName"),
        block_type=block.get("type"),
        bounding_box=block.get("boundingBox"),
        position=Vec3(
            x=float(position["x"]),
            y=float(position["y"]),
            z=float(position["z"]),
        ),
    )

def _collect_gold_blocks(blocks: tuple[BlockState, ...]) -> tuple[BlockState, ...]:
    return tuple(block for block in blocks if block.name == "gold_block")


def _collect_gold_block_positions(blocks: tuple[BlockState, ...]) -> tuple[GridPosition, ...]:
    return tuple(block.grid_position for block in blocks if block.name == "gold_block")


def _collect_flag_positions(blocks: tuple[BlockState, ...]) -> tuple[GridPosition, ...]:
    return tuple(block.grid_position for block in blocks if "banner" in block.name)


def _vec3_from_position_payload(
    payload: Mapping[str, Any] | None,
    *,
    fallback: Vec3,
) -> Vec3:
    if payload is None:
        return fallback
    return Vec3(
        x=float(payload["x"]),
        y=float(payload["y"]),
        z=float(payload["z"]),
    )


def _player_from_quick_payload(
    payload: Mapping[str, Any],
    *,
    bot_name: str,
    my_team: TeamName,
    assigned_teams: Mapping[str, TeamName] | None = None,
    previous_player: PlayerState | None = None,
) -> PlayerState:
    world_position = _vec3_from_position_payload(
        cast(Mapping[str, Any] | None, payload.get("position")),
        fallback=Vec3(x=0.0, y=1.0, z=0.0),
    )
    username = str(payload["username"])
    team = None
    if assigned_teams is not None:
        team = assigned_teams.get(username)
    if team is None and previous_player is not None:
        team = previous_player.team
    if team is None:
        team = normalize_team_name(payload.get("team"))
    if username == bot_name and team is None:
        team = my_team
    held_item_name = cast(str | None, payload.get("heldItemName"))
    return PlayerState(
        name=username,
        team=team,
        position=world_position.grid,
        world_position=world_position,
        in_prison=_is_in_prison_zone(world_position.grid),
        has_flag=_resolve_has_flag(payload, held_item_name),
        held_item_name=held_item_name,
        is_self=username == bot_name,
    )


def _entity_from_player(player: PlayerState) -> EntityState:
    return EntityState(
        entity_id=None,
        entity_type="player",
        name=player.name,
        username=player.name,
        display_name=player.name,
        object_type=player.name,
        team=player.team,
        position=player.world_position,
    )


def _animal_entity_from_quick_payload(payload: Mapping[str, Any]) -> EntityState:
    position_payload = cast(Mapping[str, Any] | None, payload.get("position"))
    position = _vec3_from_position_payload(
        position_payload,
        fallback=Vec3(x=0.0, y=1.0, z=0.0),
    )
    return EntityState(
        entity_id=payload.get("id"),
        entity_type=cast(str | None, payload.get("type")) or "animal",
        name=cast(str | None, payload.get("name")),
        username=None,
        display_name=cast(str | None, payload.get("displayName") or payload.get("display_name")),
        object_type=cast(str | None, payload.get("displayName") or payload.get("display_name")),
        team=None,
        position=position,
    )


def _patched_dynamic_entities(
    *,
    previous_entities: tuple[EntityState, ...],
    players: tuple[PlayerState, ...],
    animal_payloads: Any,
) -> tuple[EntityState, ...]:
    static_entities = tuple(
        entity
        for entity in previous_entities
        if entity.entity_type not in {"player", "animal"}
    )
    player_entities = tuple(_entity_from_player(player) for player in players)
    if animal_payloads is None:
        previous_animals = tuple(
            entity for entity in previous_entities if entity.entity_type == "animal"
        )
        return static_entities + player_entities + previous_animals

    animal_entities = tuple(
        _animal_entity_from_quick_payload(payload)
        for payload in animal_payloads
        if isinstance(payload, Mapping)
    )
    return static_entities + player_entities + animal_entities


def _infer_entity_team(entity: EntityState) -> TeamName | None:
    if entity.username and entity.username[:1] in _TEAM_PREFIXES:
        return infer_team_from_bot_name(entity.username)
    return None


def _resolve_player_team(
    entity: Mapping[str, Any],
    *,
    player_payload: Mapping[str, Any],
    bot_name: str,
    my_team: TeamName,
    assigned_teams: Mapping[str, TeamName] | None = None,
) -> TeamName | None:
    username = entity.get("username")
    if isinstance(username, str) and assigned_teams is not None:
        assigned_team = assigned_teams.get(username)
        if assigned_team is not None:
            return assigned_team
    payload_team = normalize_team_name(player_payload.get("team"))
    if payload_team is not None:
        return payload_team
    entity_team = normalize_team_name(entity.get("team"))
    if entity_team is not None:
        return entity_team
    if isinstance(username, str) and username[:1] in _TEAM_PREFIXES:
        return infer_team_from_bot_name(username)
    if username == bot_name:
        return my_team
    return None


def _normalize_assigned_teams(
    assigned_teams: Mapping[str, Any] | None,
) -> dict[str, TeamName]:
    if assigned_teams is None:
        return {}
    normalized: dict[str, TeamName] = {}
    for username, team_value in assigned_teams.items():
        team = normalize_team_name(team_value)
        if team is None:
            continue
        normalized[str(username)] = team
    return normalized


def _extract_held_item_name(
    entity: Mapping[str, Any], player_payload: Mapping[str, Any]
) -> str | None:
    held_item_name = player_payload.get("heldItemName")
    if held_item_name is not None:
        return str(held_item_name)

    held_item = entity.get("heldItem") or entity.get("held_item")
    if isinstance(held_item, Mapping):
        held_item_name = held_item.get("name")
        if held_item_name is not None:
            return str(held_item_name)
    return None


def _resolve_has_flag(player_payload: Mapping[str, Any], held_item_name: str | None) -> bool:
    if "hasBanner" in player_payload:
        return bool(player_payload.get("hasBanner"))
    return bool(held_item_name and "banner" in held_item_name.lower())


def _is_in_prison_zone(position: GridPosition) -> bool:
    return any(
        zone["min_x"] <= position.x <= zone["max_x"] and zone["min_z"] <= position.z <= zone["max_z"]
        for zone in _PRISON_ZONES.values()
    )


def _is_in_team_territory(position: GridPosition, team: TeamName) -> bool:
    if team == "L":
        return position.x < 0
    return position.x > 0


__all__ = [
    "BlockState",
    "BotState",
    "EntityState",
    "FlagState",
    "GridPosition",
    "MapMetadata",
    "Observation",
    "PlayerState",
    "Scoreboard",
    "TeamLandmarks",
    "TeamName",
    "Vec3",
    "infer_team_from_bot_name",
    "normalize_team_name",
    "opponent_team",
]

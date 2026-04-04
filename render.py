from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from lib.observation import (
    BlockState,
    BotState,
    EntityState,
    MapMetadata,
    Observation,
    PlayerState,
    Scoreboard,
    Vec3,
    infer_team_from_bot_name,
)

DEFAULT_INPUT_PATH = Path("logs/one-shot.json")
DEFAULT_OUTPUT_PATH = Path("logs/one-shot.png")
DEFAULT_GIF_INPUT_PATH = Path("logs/multi-shot.jsonl")
DEFAULT_GIF_OUTPUT_PATH = Path("logs/multi-shot.gif")
DEFAULT_FRAME_PAUSE_SECONDS = 0.5
DEFAULT_MAP_BOUNDS = {
    "min_x": -24,
    "max_x": 24,
    "min_z": -36,
    "max_z": 36,
    "plane_y": 1,
}
CELL_SIZE = 14
GRID_LINE = (30, 30, 30)
TEAM_L_BG = (255, 228, 228)
TEAM_R_BG = (214, 232, 255)
MID_BG = (240, 240, 240)
TEAM_L_PLAYER = (255, 192, 203)
TEAM_R_PLAYER = (150, 190, 240)
TEAM_L_FLAG_BG = (255, 0, 0)
TEAM_R_FLAG_BG = (0, 120, 255)

UNKNOWN_PLAYER = (190, 190, 190)
MARKER_TEXT = (20, 20, 20)
MARKER_BG = (255, 255, 255)
PURPLE_MARKER = (140, 90, 190)
TIMESTAMP_BG = (255, 255, 255)
TIMESTAMP_TEXT = (20, 20, 20)
PRISON_OPENINGS = {(-16, 24), (16, 24)}
BANNER_BLOCKS = {"blue_banner", "red_banner"}
NON_BLOCKING_FLOOR_BLOCKS = {
    "blue_banner",
    "red_banner",
    "redstone_wire",
    "stone_pressure_plate",
}
BLOCK_COLORS = {
    "fence": (110, 80, 55),
    "wall": (90, 90, 90),
    "gate": (160, 120, 80),
    "gold_block": (240, 210, 60),
    "oak_log": (60, 150, 80),
    "spruce_log": (45, 130, 70),
    "oak_leaves": (60, 150, 80),
    "spruce_leaves": (60, 150, 80),
    "stripped_oak_log": (60, 150, 80),
    "pitcher_plant": (45, 130,70),
    "blue_banner": (45, 100, 220),
    "red_banner": (210, 60, 60),
    "stone_pressure_plate": (200, 170, 110),
    "redstone_wire": PURPLE_MARKER,
    "oxidized_copper": (56, 142, 120),
    "orange_terracotta": (240, 196, 146),
    "glass": (186, 232, 182),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a one-shot Minecraft CTF observation to an image.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--gif",
        action="store_true",
        help="Interpret the input as JSONL snapshots and render an animated GIF.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_FRAME_PAUSE_SECONDS,
        help="Pause duration for each GIF frame in seconds.",
    )
    parser.add_argument(
        "--obs",
        action="store_true",
        help="Interpret the input file as normalized Observation JSON instead of a raw snapshot.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = args.input
    output_path = args.output
    if args.gif:
        if input_path == DEFAULT_INPUT_PATH:
            input_path = DEFAULT_GIF_INPUT_PATH
        if output_path == DEFAULT_OUTPUT_PATH:
            output_path = DEFAULT_GIF_OUTPUT_PATH
        frames = _render_gif_frames(input_path, use_observation_payload=args.obs)
        _save_gif(frames, output_path, pause_seconds=args.pause_seconds)
        print(f"Wrote animated map to {output_path}")
        return 0

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    observation = _load_observation(payload, use_observation_payload=args.obs)
    image = render_observation(observation, timestamp_text=_timestamp_text(payload))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"Wrote map image to {output_path}")
    return 0


def render_observation(observation: Observation, *, timestamp_text: str | None = None) -> Image.Image:
    bounds = observation.map
    min_x = bounds.min_x
    max_x = bounds.max_x
    min_z = bounds.min_z
    max_z = bounds.max_z
    width = max_x - min_x + 1
    height = max_z - min_z + 1

    image = Image.new("RGB", (width * CELL_SIZE, height * CELL_SIZE), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    for z in range(min_z, max_z + 1):
        for x in range(min_x, max_x + 1):
            left, top, right, bottom = _cell_box(x=x, z=z, min_x=min_x, min_z=min_z)
            draw.rectangle((left, top, right, bottom), fill=_territory_color(x))

    for block in _pick_visible_blocks(_render_blocks(observation)):
        block_name = block.name
        x = block.grid_position.x
        z = block.grid_position.z
        left, top, right, bottom = _cell_box(x=x, z=z, min_x=min_x, min_z=min_z)
        if block_name in BANNER_BLOCKS:
            _draw_labeled_tile(
                draw,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                fill=_color_for_block(block_name),
                label="F",
                text_fill=(255, 255, 255),
            )
            continue
        draw.rectangle((left, top, right, bottom), fill=_color_for_block(block_name))

    for x, z in _blocked_cells(observation):
        left, top, right, bottom = _cell_box(x=x, z=z, min_x=min_x, min_z=min_z)
        inset = max(2, CELL_SIZE // 6)
        draw.rectangle((left + inset, top + inset, right - inset, bottom - inset), outline=(15, 15, 15), width=2)
        draw.line((left + inset, top + inset, right - inset, bottom - inset), fill=(15, 15, 15), width=2)
        draw.line((left + inset, bottom - inset, right - inset, top + inset), fill=(15, 15, 15), width=2)

    for x, z in PRISON_OPENINGS:
        if not (min_x <= x <= max_x and min_z <= z <= max_z):
            continue
        left, top, right, bottom = _cell_box(x=x, z=z, min_x=min_x, min_z=min_z)
        _draw_labeled_tile(
            draw,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            fill=PURPLE_MARKER,
            label="O",
            text_fill=(0, 0, 0),
        )

    for entity in observation.entities:
        if entity.entity_type == "player":
            continue
        x = entity.grid_position.x
        z = entity.grid_position.z
        left, top, right, bottom = _cell_box(x=x, z=z, min_x=min_x, min_z=min_z)
        draw.ellipse((left + 3, top + 3, right - 3, bottom - 3), fill=_color_for_entity(entity))

    self_player = next((player for player in observation.players if player.is_self), None)

    for player in observation.players:
        if player.is_self:
            continue
        x = player.position.x
        z = player.position.z
        left, top, right, bottom = _cell_box(x=x, z=z, min_x=min_x, min_z=min_z)
        label = "F" if player.has_flag else "P"
        _draw_labeled_tile(
            draw,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            fill=_color_for_player(player),
            label=label,
        )

    me_x = observation.me.position.x
    me_z = observation.me.position.z
    left, top, right, bottom = _cell_box(x=me_x, z=me_z, min_x=min_x, min_z=min_z)
    _draw_labeled_tile(
        draw,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        fill=_color_for_player(self_player) if self_player is not None else _color_for_team(observation.team),
        label="B",
    )

    for offset_x in range(width + 1):
        x = offset_x * CELL_SIZE
        draw.line((x, 0, x, height * CELL_SIZE), fill=GRID_LINE, width=1)
    for offset_z in range(height + 1):
        z = offset_z * CELL_SIZE
        draw.line((0, z, width * CELL_SIZE, z), fill=GRID_LINE, width=1)

    if timestamp_text:
        _draw_timestamp(draw, image_width=width * CELL_SIZE, timestamp_text=timestamp_text)

    return image


def _load_observation(payload: dict[str, Any], *, use_observation_payload: bool) -> Observation:
    if "me" in payload:
        return _observation_from_dynamic_payload(payload)
    if use_observation_payload:
        return Observation.from_dict(payload)
    bot = payload["bot"]
    bot_name = str(bot["username"])
    return Observation.from_snapshot(snapshot_source=payload, bot_name=bot_name)


def _render_gif_frames(input_path: Path, *, use_observation_payload: bool) -> list[Image.Image]:
    frames: list[Image.Image] = []
    merged_payload: dict[str, Any] = {}
    merged_payloads: list[dict[str, Any]] = []
    for payload in _read_jsonl_payloads(input_path):
        merged_payload = _merge_frame_payload(merged_payload, payload)
        merged_payloads.append(dict(merged_payload))
    common_map = _resolve_common_map_metadata(merged_payloads)
    for payload in merged_payloads:
        observation = _load_frame_observation(
            payload,
            use_observation_payload=use_observation_payload,
            map_metadata=common_map,
        )
        frames.append(render_observation(observation, timestamp_text=_timestamp_text(payload)))
    if not frames:
        raise ValueError(f"No renderable frames found in {input_path}.")
    return frames


def _save_gif(frames: list[Image.Image], output_path: Path, *, pause_seconds: float) -> None:
    duration_ms = max(1, int(round(pause_seconds * 1000)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
    )


def _read_jsonl_payloads(input_path: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if isinstance(payload.get("observation"), dict):
            observation_payload = dict(payload["observation"])
            if observation_payload.get("bot_name") is None and payload.get("bot_name") is not None:
                observation_payload["bot_name"] = payload["bot_name"]
            if observation_payload.get("timestamp") is None and payload.get("timestamp") is not None:
                observation_payload["timestamp"] = payload["timestamp"]
            payloads.append(observation_payload)
            continue
        if payload.get("event") in {"session_start", "session_end"}:
            continue
        payloads.append(payload)
    return payloads


def _merge_frame_payload(previous: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    merged.update(delta)
    return merged


def _load_frame_observation(
    payload: dict[str, Any],
    *,
    use_observation_payload: bool,
    map_metadata: MapMetadata | None = None,
) -> Observation:
    if "me" in payload:
        return _observation_from_dynamic_payload(payload, map_metadata=map_metadata)

    frame_payload = dict(payload)
    if "bot" not in frame_payload and frame_payload.get("bot_name"):
        frame_payload["bot"] = {
            "username": frame_payload["bot_name"],
            "team": frame_payload.get("team"),
        }
    if use_observation_payload:
        observation = Observation.from_dict(frame_payload)
        if map_metadata is None:
            return observation
        return Observation(
            tick_ms=observation.tick_ms,
            bot_name=observation.bot_name,
            team=observation.team,
            me=observation.me,
            players=observation.players,
            myteam_players=observation.myteam_players,
            opponent_players=observation.opponent_players,
            entities=observation.entities,
            blocks=observation.blocks,
            gold_blocks=observation.gold_blocks,
            gold_block_positions=observation.gold_block_positions,
            flag_positions=observation.flag_positions,
            flags_to_capture=observation.flags_to_capture,
            flags_to_protect=observation.flags_to_protect,
            map=map_metadata,
            scores=observation.scores,
        )
    return Observation.from_snapshot(
        snapshot_source=frame_payload,
        bot_name=str(frame_payload["bot"]["username"]),
        map_metadata=map_metadata,
    )


def _observation_from_dynamic_payload(
    payload: dict[str, Any],
    *,
    map_metadata: MapMetadata | None = None,
) -> Observation:
    me = BotState.from_dict(payload["me"])
    bot_name = str(payload.get("bot_name") or me.name)
    team = payload.get("team") or me.team
    blocks = tuple(
        BlockState.from_dict(item)
        for item in payload.get("blocks", [])
        if item.get("name") not in BANNER_BLOCKS
    )
    flags_to_capture = tuple(
        BlockState.from_dict(item) for item in payload.get("flags_to_capture", [])
    )
    flags_to_protect = tuple(
        BlockState.from_dict(item) for item in payload.get("flags_to_protect", [])
    )
    players = _players_from_dynamic_payload(payload, bot_name=bot_name, team=team, me=me)
    entities = _entities_from_dynamic_payload(payload)
    myteam_players = tuple(player for player in players if player.team == team)
    opponent_players = tuple(
        player for player in players if player.team is not None and player.team != team
    )
    return Observation(
        tick_ms=payload.get("tick_ms"),
        bot_name=bot_name,
        team=team,
        me=me,
        players=players,
        myteam_players=myteam_players,
        opponent_players=opponent_players,
        entities=entities,
        blocks=blocks,
        gold_blocks=tuple(block for block in blocks if block.name == "gold_block"),
        gold_block_positions=tuple(
            block.grid_position for block in blocks if block.name == "gold_block"
        ),
        flag_positions=tuple(
            block.grid_position for block in blocks if "banner" in block.name
        ),
        flags_to_capture=flags_to_capture,
        flags_to_protect=flags_to_protect,
        map=map_metadata or _map_metadata_from_payload(payload),
        scores=Scoreboard.from_dict(payload.get("scores", {})),
    )


def _players_from_dynamic_payload(
    payload: dict[str, Any],
    *,
    bot_name: str,
    team: Any,
    me: BotState,
) -> tuple[PlayerState, ...]:
    players = [_player_state_from_any(item, bot_name=bot_name, team=team) for item in payload.get("players", [])]
    if any(player.name == bot_name for player in players):
        return tuple(players)
    players.append(
        PlayerState(
            name=bot_name,
            team=team,
            position=me.position,
            world_position=me.world_position,
            in_prison=False,
            has_flag=False,
            held_item_name=None,
            is_self=True,
        )
    )
    return tuple(players)


def _player_state_from_any(payload: dict[str, Any], *, bot_name: str, team: Any) -> PlayerState:
    if "world_position" in payload:
        return PlayerState.from_dict(payload)

    name = str(payload.get("username") or payload.get("name") or "player")
    world_position = Vec3.from_dict(payload["position"])
    player_team = payload.get("team")
    if player_team is None and name == bot_name:
        player_team = team
    if player_team is None and name[:1] in {"L", "R"}:
        player_team = infer_team_from_bot_name(name)
    return PlayerState(
        name=name,
        team=player_team,
        position=world_position.grid,
        world_position=world_position,
        in_prison=False,
        has_flag=False,
        held_item_name=None,
        is_self=name == bot_name or bool(payload.get("is_self", False)),
    )


def _entities_from_dynamic_payload(payload: dict[str, Any]) -> tuple[EntityState, ...]:
    entities: list[EntityState] = []
    for item in payload.get("entities", []):
        entities.append(EntityState.from_dict(item))
    for item in payload.get("animals", []):
        x = float(item["position"]["x"])
        z = float(item["position"]["z"])
        entities.append(
            EntityState(
                entity_id=item.get("id"),
                entity_type=str(item.get("type") or "animal"),
                name=item.get("name"),
                username=None,
                display_name=item.get("display_name") or item.get("displayName"),
                object_type=item.get("object_type") or item.get("objectType"),
                team=None,
                position=Vec3(x=x, y=float(DEFAULT_MAP_BOUNDS["plane_y"]), z=z),
            )
        )
    return tuple(entities)


def _map_metadata_from_payload(payload: dict[str, Any]) -> MapMetadata:
    inferred = _explicit_or_inferred_map_metadata(payload)
    if inferred is not None:
        return inferred
    return MapMetadata(**DEFAULT_MAP_BOUNDS)


def _resolve_common_map_metadata(payloads: list[dict[str, Any]]) -> MapMetadata | None:
    common: MapMetadata | None = None
    for payload in payloads:
        current = _explicit_or_inferred_map_metadata(payload)
        if current is None:
            continue
        if common is None:
            common = current
            continue
        common = MapMetadata(
            min_x=min(common.min_x, current.min_x),
            max_x=max(common.max_x, current.max_x),
            min_z=min(common.min_z, current.min_z),
            max_z=max(common.max_z, current.max_z),
            plane_y=common.plane_y,
        )
    if common is None:
        return MapMetadata(**DEFAULT_MAP_BOUNDS)
    return common


def _explicit_or_inferred_map_metadata(payload: dict[str, Any]) -> MapMetadata | None:
    if "map" in payload:
        return MapMetadata.from_dict(payload["map"])
    if "bounds" in payload:
        bounds = payload["bounds"]
        return MapMetadata(
            min_x=int(bounds["min_x"]),
            max_x=int(bounds["max_x"]),
            min_z=int(bounds["min_z"]),
            max_z=int(bounds["max_z"]),
            plane_y=int(payload.get("plane_y", 1)),
        )
    return _infer_map_metadata(payload)


def _infer_map_metadata(payload: dict[str, Any]) -> MapMetadata | None:
    positions: list[tuple[int, int]] = []
    positions.extend(_payload_positions(payload.get("blocks", [])))
    positions.extend(_payload_positions(payload.get("flags_to_capture", [])))
    positions.extend(_payload_positions(payload.get("flags_to_protect", [])))
    positions.extend(_payload_positions(payload.get("players", [])))
    positions.extend(_payload_positions(payload.get("entities", [])))
    positions.extend(_payload_positions(payload.get("animals", [])))
    me = payload.get("me")
    if isinstance(me, dict):
        positions.extend(_payload_positions([me]))
    if not positions:
        return None
    xs = [x for x, _ in positions]
    zs = [z for _, z in positions]
    return MapMetadata(
        min_x=min(xs),
        max_x=max(xs),
        min_z=min(zs),
        max_z=max(zs),
        plane_y=int(payload.get("plane_y", DEFAULT_MAP_BOUNDS["plane_y"])),
    )


def _payload_positions(items: Any) -> list[tuple[int, int]]:
    if not isinstance(items, list):
        return []
    positions: list[tuple[int, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        position = item.get("position")
        if not isinstance(position, dict):
            continue
        x = position.get("x")
        z = position.get("z")
        if x is None or z is None:
            continue
        positions.append((int(float(x)), int(float(z))))
    return positions


def _pick_visible_blocks(blocks: list[Any] | tuple[Any, ...]) -> list[Any]:
    selected: dict[tuple[int, int], Any] = {}
    for block in blocks:
        x = block.grid_position.x
        y = int(block.position.y)
        z = block.grid_position.z
        key = (x, z)
        current = selected.get(key)
        if current is None or _block_priority(block) < _block_priority(current) or (
            _block_priority(block) == _block_priority(current) and y > int(current.position.y)
        ):
            selected[key] = block
    return list(selected.values())


def _block_priority(block: Any) -> tuple[int, int]:
    name = block.name
    y = int(block.position.y)
    if name in {"blue_banner", "red_banner"}:
        return (0, -y)
    if name == "gold_block":
        return (1, -y)
    if "fence" in name or "wall" in name or "gate" in name:
        return (2, -y)
    if block.bounding_box == "block":
        return (3, -y)
    return (4, -y)


def _render_blocks(observation: Observation) -> list[Any]:
    blocks = [block for block in observation.blocks if block.name not in BANNER_BLOCKS]
    current_banners = list(observation.flags_to_capture) + list(observation.flags_to_protect)
    if current_banners:
        blocks.extend(current_banners)
    else:
        blocks.extend(block for block in observation.blocks if block.name in BANNER_BLOCKS)
    return blocks


def _blocked_cells(observation: Observation) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set()
    banner_cells = {
        (block.grid_position.x, block.grid_position.z) for block in _current_banner_blocks(observation)
    }
    for block in observation.blocks:
        if not _is_walk_blocker(block, plane_y=observation.map.plane_y):
            continue
        cell = (block.grid_position.x, block.grid_position.z)
        if cell in banner_cells:
            continue
        blocked.add(cell)
    return blocked


def _current_banner_blocks(observation: Observation) -> list[Any]:
    current_banners = list(observation.flags_to_capture) + list(observation.flags_to_protect)
    if current_banners:
        return current_banners
    return [block for block in observation.blocks if block.name in BANNER_BLOCKS]


def _is_walk_blocker(block: Any, *, plane_y: int) -> bool:
    if int(block.position.y) != plane_y:
        return False
    return block.bounding_box == "block" and block.name not in NON_BLOCKING_FLOOR_BLOCKS


def _cell_box(*, x: int, z: int, min_x: int, min_z: int) -> tuple[int, int, int, int]:
    left = (x - min_x) * CELL_SIZE
    top = (z - min_z) * CELL_SIZE
    return (left, top, left + CELL_SIZE, top + CELL_SIZE)


def _territory_color(x: int) -> tuple[int, int, int]:
    if x < 0:
        return TEAM_L_BG
    if x > 0:
        return TEAM_R_BG
    return MID_BG


def _color_for_block(block_name: str) -> tuple[int, int, int]:
    for token, color in BLOCK_COLORS.items():
        if token in block_name:
            return color
    return (140, 140, 140)


def _color_for_player(player: Any) -> tuple[int, int, int]:
    if player is not None and getattr(player, "has_flag", False):
        if player.team == "L":
            return TEAM_L_FLAG_BG
        if player.team == "R":
            return TEAM_R_FLAG_BG
    return _color_for_team(player.team)


def _color_for_team(team: Any) -> tuple[int, int, int]:
    if team == "L":
        return TEAM_L_PLAYER
    if team == "R":
        return TEAM_R_PLAYER
    return UNKNOWN_PLAYER


def _color_for_entity(entity: Any) -> tuple[int, int, int]:
    username = entity.username or ""
    entity_type = entity.entity_type
    if username.startswith("L_"):
        return (25, 25, 25)
    if username.startswith("R_"):
        return (245, 245, 245)
    if entity_type == "player":
        return (120, 120, 120)
    if entity_type == "animal":
        return (90, 150, 90)
    return (140, 100, 180)


def _draw_labeled_tile(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    fill: tuple[int, int, int],
    label: str,
    text_fill: tuple[int, int, int] = MARKER_TEXT,
) -> None:
    inset = 1
    draw.rectangle((left + inset, top + inset, right - inset, bottom - inset), fill=fill)
    font = ImageFont.load_default()
    draw.text(
        ((left + right) / 2, (top + bottom) / 2),
        label,
        fill=text_fill,
        font=_load_font(10),
        anchor="mm",
    )


def _draw_timestamp(draw: ImageDraw.ImageDraw, *, image_width: int, timestamp_text: str) -> None:
    font = _load_font(18)
    label = f"@{timestamp_text}"
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    padding_x = 6
    padding_y = 4
    text_width = right - left
    text_height = bottom - top
    center_x = image_width / 2
    box_left = int(center_x - text_width / 2 - padding_x)
    box_top = 2
    box_right = int(center_x + text_width / 2 + padding_x)
    box_bottom = box_top + text_height + padding_y * 2
    draw.rectangle((box_left, box_top, box_right, box_bottom), fill=TIMESTAMP_BG, outline=GRID_LINE, width=1)
    draw.text((center_x, box_top + padding_y), label, fill=TIMESTAMP_TEXT, font=font, anchor="ma")


def _timestamp_text(payload: dict[str, Any]) -> str | None:
    timestamp = payload.get("timestamp")
    if timestamp is None:
        return None
    try:
        timestamp_value = float(timestamp)
    except (TypeError, ValueError):
        return str(timestamp)
    dt = datetime.fromtimestamp(timestamp_value)
    milliseconds = int((timestamp_value - int(timestamp_value)) * 1000)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{milliseconds:03d}"


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for font_name in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


if __name__ == "__main__":
    raise SystemExit(main())

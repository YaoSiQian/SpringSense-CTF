#!/usr/bin/env python3
"""
Capture a Mineflayer world snapshot into JSON and visualize a horizontal slice.

The script follows the same Python -> Mineflayer bridge pattern used in
`mineflayer.ipynb`, but packages it into a reusable CLI tool.

Examples
--------
Capture the default y=1 slice around the bot and save it:
    python3 map_to_json.py --host localhost --port 25565 --output map_snapshot.json

Capture a larger box and open a matplotlib view of y=1:
    python3 map_to_json.py --radius 20 --plot

Visualize a previously saved snapshot without reconnecting:
    python3 map_to_json.py --input map_snapshot.json --plot
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AIR_BLOCKS = {"air", "cave_air", "void_air"}
VISIBLE_BLOCK_MIN_Y = 1
VISIBLE_BLOCK_MAX_Y = 2
VISIBLE_Y0_BLOCKS = {"gold_block", "orange_terracotta", "oxidized_copper"}


@dataclass(frozen=True)
class ScanBounds:
    min_x: int
    max_x: int
    min_y: int
    max_y: int
    min_z: int
    max_z: int

    @property
    def width(self) -> int:
        return self.max_x - self.min_x + 1

    @property
    def height(self) -> int:
        return self.max_y - self.min_y + 1

    @property
    def depth(self) -> int:
        return self.max_z - self.min_z + 1

    def to_dict(self) -> dict[str, int]:
        return {
            "min_x": self.min_x,
            "max_x": self.max_x,
            "min_y": self.min_y,
            "max_y": self.max_y,
            "min_z": self.min_z,
            "max_z": self.max_z,
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_bridge():
    try:
        from javascript import require, once
    except Exception as exc:  # pragma: no cover - depends on local runtime
        raise RuntimeError(
            "Unable to import the Python JavaScript bridge. "
            "Install/configure the `javascript` package used by the notebook first."
        ) from exc

    return require, once


def _build_js_helpers(require):
    vm = require("node:vm")

    block_to_json = vm.runInThisContext(
        """
        (block) => {
          if (!block) return "";
          return JSON.stringify({
            name: block.name ?? null,
            displayName: block.displayName ?? null,
            type: block.type ?? null,
            boundingBox: block.boundingBox ?? null,
            transparent: block.transparent ?? null,
            diggable: block.diggable ?? null,
            hardness: block.hardness ?? null,
            position: block.position ? {
              x: block.position.x,
              y: block.position.y,
              z: block.position.z
            } : null
          });
        }
        """
    )
    entities_to_json = vm.runInThisContext(
        """
        (entities) => JSON.stringify(
          Object.values(entities ?? {}).map((entity) => ({
            id: entity.id ?? null,
            type: entity.type ?? null,
            kind: entity.kind ?? null,
            name: entity.name ?? null,
            username: entity.username ?? null,
            displayName: entity.displayName ?? null,
            objectType: entity.objectType ?? null,
            team: entity.team ?? null,
            position: entity.position ? {
              x: entity.position.x,
              y: entity.position.y,
              z: entity.position.z
            } : null
          }))
        )
        """
    )
    players_to_json = vm.runInThisContext(
        """
        (players) => JSON.stringify(
          Object.entries(players ?? {}).map(([username, player]) => {
            const entity = player?.entity ?? null;
            const heldItem = entity?.heldItem;
            return {
              username,
              hasBanner: Boolean((heldItem?.name && heldItem.name.includes('Flag')) || (heldItem?.name && heldItem.name.includes('banner'))),
              heldItemName: heldItem?.name ?? null
            };
          })
        )
        """
    )
    position_to_json = vm.runInThisContext(
        """
        (pos) => JSON.stringify({
          x: pos?.x ?? null,
          y: pos?.y ?? null,
          z: pos?.z ?? null
        })
        """
    )

    return block_to_json, entities_to_json, players_to_json, position_to_json


def connect_bot(host: str, port: int, username: str):
    require, once = _load_bridge()
    mineflayer = require("mineflayer")

    bot = mineflayer.createBot(
        {
            "host": host,
            "port": port,
            "username": username,
            "hideErrors": False,
        }
    )
    once(bot, "login")
    once(bot, "spawn")
    return bot, require


def resolve_scan_bounds(
    bot_position: dict[str, float],
    radius: int,
    plane_y: int,
    min_x: int | None,
    max_x: int | None,
    min_y: int | None,
    max_y: int | None,
    min_z: int | None,
    max_z: int | None,
) -> ScanBounds:
    if any(value is not None for value in (min_x, max_x, min_y, max_y, min_z, max_z)):
        provided = [min_x, max_x, min_y, max_y, min_z, max_z]
        if any(value is None for value in provided):
            raise ValueError(
                "If any explicit scan bound is provided, all of "
                "--min-x/--max-x/--min-y/--max-y/--min-z/--max-z are required."
            )
        return ScanBounds(
            min_x=min_x,
            max_x=max_x,
            min_y=min_y,
            max_y=max_y,
            min_z=min_z,
            max_z=max_z,
        )

    center_x = math.floor(bot_position["x"])
    center_z = math.floor(bot_position["z"])
    return ScanBounds(
        min_x=center_x - radius,
        max_x=center_x + radius,
        min_y=plane_y,
        max_y=plane_y,
        min_z=center_z - radius,
        max_z=center_z + radius,
    )


def snapshot_world(
    host: str,
    port: int,
    username: str,
    *,
    radius: int = 16,
    plane_y: int = 1,
    settle_seconds: float = 2.0,
    min_x: int | None = None,
    max_x: int | None = None,
    min_y: int | None = None,
    max_y: int | None = None,
    min_z: int | None = None,
    max_z: int | None = None,
) -> dict[str, Any]:
    bot, require = connect_bot(host, port, username)
    block_to_json, entities_to_json, players_to_json, position_to_json = _build_js_helpers(require)
    vec3 = require("vec3")

    try:
        time.sleep(settle_seconds)

        bot_position = json.loads(position_to_json(bot.entity.position))
        bounds = resolve_scan_bounds(
            bot_position,
            radius,
            plane_y,
            min_x,
            max_x,
            min_y,
            max_y,
            min_z,
            max_z,
        )

        blocks: list[dict[str, Any]] = []
        for y in range(bounds.min_y, bounds.max_y + 1):
            for z in range(bounds.min_z, bounds.max_z + 1):
                for x in range(bounds.min_x, bounds.max_x + 1):
                    block = bot.blockAt(vec3(x, y, z))
                    raw_block = block_to_json(block)
                    if not raw_block:
                        continue
                    block_data = json.loads(raw_block)
                    if block_data["name"] in AIR_BLOCKS:
                        continue
                    blocks.append(block_data)

        entities = json.loads(entities_to_json(bot.entities))
        player_states = {
            player["username"]: player
            for player in json.loads(players_to_json(bot.players))
            if player.get("username")
        }
        for entity in entities:
            username = entity.get("username")
            player_state = player_states.get(username, {})
            entity["hasBanner"] = bool(player_state.get("hasBanner"))
            entity["heldItemName"] = player_state.get("heldItemName")
        snapshot = {
            "captured_at": _utc_now_iso(),
            "server": {
                "host": host,
                "port": port,
                "username": username,
            },
            "bounds": bounds.to_dict(),
            "plane_y": plane_y,
            "bot": {
                "position": bot_position,
                "username": username,
                "team": getattr(bot, "team", None),
                "hasBanner": bool(
                    getattr(getattr(bot.entity, "equipment", None), "__getitem__", None)
                    and bot.entity.equipment[0]
                    and getattr(bot.entity.equipment[0], "name", None)
                    and "banner" in bot.entity.equipment[0].name
                ),
            },
            "summary": {
                "block_count": len(blocks),
                "entity_count": len(entities),
            },
            "blocks": blocks,
            "entities": entities,
        }
        return snapshot
    finally:
        try:
            bot.quit()
        except Exception:
            pass


def write_snapshot(snapshot: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def load_snapshot(input_path: Path) -> dict[str, Any]:
    return json.loads(input_path.read_text(encoding="utf-8"))


def _block_symbol(block_name: str, bounding_box: str | None) -> str:
    if block_name == "gold_block":
        return "T"
    if block_name == "orange_terracotta":
        return "O"
    if block_name == "oxidized_copper":
        return "X"
    if block_name == "blue_banner":
        return "L"
    if block_name == "red_banner":
        return "R"
    if block_name == "redstone_wire":
        return "W"
    if block_name == "stone_pressure_plate":
        return "S"
    if "water" in block_name:
        return "~"
    if "lava" in block_name:
        return "!"
    if "glass" in block_name:
        return "G"
    if "leaves" in block_name or "vine" in block_name or "pitcher_plant" in block_name:
        return "*"
    if "fence" in block_name or "wall" in block_name:
        return "F"
    if "door" in block_name or "gate" in block_name:
        return "D"
    if bounding_box == "block":
        return "#"
    return "?"


def _resolve_display_mode(
    snapshot: dict[str, Any],
    plane_y: int | None,
    display_all_layers: bool,
) -> tuple[bool, int]:
    bounds = snapshot["bounds"]
    if display_all_layers or bounds["min_y"] != bounds["max_y"]:
        return True, bounds["min_y"]
    return False, snapshot.get("plane_y", 1) if plane_y is None else plane_y


def _display_priority(symbol: str) -> int:
    if symbol in {"B", "P", "l", "r", "L", "R", "E", "O", "T", "X"}:
        return 1
    if symbol in {"#", "F", "D"}:
        return 2
    if symbol in {"~", "!", "*"}:
        return 3
    if symbol == "W":
        return 4
    return 5


def build_plane_grid(
    snapshot: dict[str, Any],
    plane_y: int | None = None,
    *,
    display_all_layers: bool = False,
) -> tuple[list[list[str]], dict[str, int | bool]]:
    bounds = snapshot["bounds"]
    use_projection, selected_plane_y = _resolve_display_mode(snapshot, plane_y, display_all_layers)
    width = bounds["width"]
    depth = bounds["depth"]
    grid = [["." for _ in range(width)] for _ in range(depth)]
    priority = [[float("inf") for _ in range(width)] for _ in range(depth)]
    y_order = [[float("inf") for _ in range(width)] for _ in range(depth)]

    def place_symbol(x: int, z: int, y: int, symbol: str) -> None:
        if not (0 <= x < width and 0 <= z < depth):
            return
        symbol_priority = _display_priority(symbol)
        if not use_projection:
            if y < y_order[z][x]:
                y_order[z][x] = y
                grid[z][x] = symbol
            return
        if (
            symbol_priority < priority[z][x]
            or (symbol_priority == priority[z][x] and y < y_order[z][x])
        ):
            priority[z][x] = symbol_priority
            y_order[z][x] = y
            grid[z][x] = symbol

    for block in snapshot.get("blocks", []):
        position = block["position"]
        block_y = int(position["y"])
        if block_y == 0 and block["name"] not in VISIBLE_Y0_BLOCKS:
            continue
        if block_y < 0 or block_y > VISIBLE_BLOCK_MAX_Y:
            continue
        if block_y < VISIBLE_BLOCK_MIN_Y and block_y != 0:
            continue
        if not use_projection and block_y != selected_plane_y:
            continue
        gx = int(position["x"]) - bounds["min_x"]
        gz = int(position["z"]) - bounds["min_z"]
        place_symbol(gx, gz, block_y, _block_symbol(block["name"], block.get("boundingBox")))

    for entity in snapshot.get("entities", []):
        position = entity.get("position")
        if position is None:
            continue
        entity_plane_y = math.floor(float(position["y"]) - 1.0)
        if not (bounds["min_y"] <= entity_plane_y <= bounds["max_y"]):
            continue
        if not use_projection and entity_plane_y != selected_plane_y:
            continue
        gx = math.floor(float(position["x"])) - bounds["min_x"]
        gz = math.floor(float(position["z"])) - bounds["min_z"]
        entity_type = entity.get("type")
        if entity_type == "player":
            symbol = "U" if entity.get("hasBanner") else "P"
        elif entity_type == "mob":
            symbol = "M"
        else:
            symbol = "E"
        place_symbol(gx, gz, entity_plane_y, symbol)

    bot_position = snapshot.get("bot", {}).get("position")
    if bot_position is not None:
        bot_plane_y = math.floor(bot_position["y"]) - 1
        if not (bounds["min_y"] <= bot_plane_y <= bounds["max_y"]):
            return grid, {
                "min_x": bounds["min_x"],
                "min_z": bounds["min_z"],
                "plane_y": selected_plane_y,
                "use_projection": use_projection,
                "min_y": bounds["min_y"],
                "max_y": bounds["max_y"],
            }
        gx = math.floor(bot_position["x"]) - bounds["min_x"]
        gz = math.floor(bot_position["z"]) - bounds["min_z"]
        bot_symbol = "U" if snapshot.get("bot", {}).get("hasBanner") else "P"
        place_symbol(gx, gz, bot_plane_y, bot_symbol)

    return grid, {
        "min_x": bounds["min_x"],
        "min_z": bounds["min_z"],
        "plane_y": selected_plane_y,
        "use_projection": use_projection,
        "min_y": bounds["min_y"],
        "max_y": bounds["max_y"],
    }


def render_ascii_slice(
    snapshot: dict[str, Any],
    plane_y: int | None = None,
    *,
    no_space: bool = False,
    display_all_layers: bool = False,
) -> str:
    def axis_label(value: int) -> str:
        return str(abs(value))

    def axis_range(start: int, end: int) -> str:
        return f"{axis_label(start)}..{axis_label(end)}"

    grid, meta = build_plane_grid(snapshot, plane_y, display_all_layers=display_all_layers)
    if meta["use_projection"]:
        header = (
            f"2D projection for y={meta['min_y']}..{meta['max_y']} "
            f"(object-priority view; x: {axis_range(meta['min_x'], meta['min_x'] + len(grid[0]) - 1)}, "
            f"z: {axis_range(meta['min_z'], meta['min_z'] + len(grid) - 1)})"
        )
    else:
        header = (
            f"2D slice at y={meta['plane_y']} (x: {axis_range(meta['min_x'], meta['min_x'] + len(grid[0]) - 1)}, "
            f"z: {axis_range(meta['min_z'], meta['min_z'] + len(grid) - 1)})"
        )
    lines = [header]

    if no_space:
        x_axis = "".join(axis_label(meta["min_x"] + index)[-1] for index in range(len(grid[0])))
        lines.append(f"x {x_axis}")
        for z_index, row in enumerate(grid):
            z_value = meta["min_z"] + z_index
            lines.append(f"{axis_label(z_value)} {''.join(row)}")
        return "\n".join(lines)

    x_labels = [axis_label(meta["min_x"] + index) for index in range(len(grid[0]))]
    x_width = max(1, max(len(label) for label in x_labels))
    z_labels = [axis_label(meta["min_z"] + index) for index in range(len(grid))]
    z_width = max(1, max(len(label) for label in z_labels))
    axis_indent = " " * (z_width + 2)
    lines.append(f"{axis_indent}x " + " ".join(label.rjust(x_width) for label in x_labels))
    for z_index, row in enumerate(grid):
        z_value = meta["min_z"] + z_index
        row_text = " ".join(symbol.rjust(x_width) for symbol in row)
        lines.append(f"z {axis_label(z_value).rjust(z_width)} {row_text}")
    return "\n".join(lines)


def list_unknown_blocks(
    snapshot: dict[str, Any],
    plane_y: int | None = None,
    *,
    display_all_layers: bool = False,
) -> list[str]:
    use_projection, selected_plane_y = _resolve_display_mode(snapshot, plane_y, display_all_layers)
    unknown_blocks = {
        block["name"]
        for block in snapshot.get("blocks", [])
        if (
            (int(block["position"]["y"]) == 0 and block["name"] in VISIBLE_Y0_BLOCKS)
            or VISIBLE_BLOCK_MIN_Y <= int(block["position"]["y"]) <= VISIBLE_BLOCK_MAX_Y
        )
        and (use_projection or int(block["position"]["y"]) == selected_plane_y)
        and _block_symbol(block["name"], block.get("boundingBox")) == "?"
    }
    return sorted(unknown_blocks)


def render_matplotlib_slice(
    snapshot: dict[str, Any],
    plane_y: int | None = None,
    *,
    save_path: Path | None = None,
    display_all_layers: bool = False,
) -> None:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("matplotlib is not available in this environment.") from exc

    def pan_vertical(axis, step_cells: float) -> None:
        y0, y1 = axis.get_ylim()
        axis.set_ylim(y0 + step_cells, y1 + step_cells)

    def zoom(axis, scale_factor: float) -> None:
        x0, x1 = axis.get_xlim()
        y0, y1 = axis.get_ylim()
        x_center = (x0 + x1) / 2.0
        y_center = (y0 + y1) / 2.0
        new_width = max(2.0, (x1 - x0) * scale_factor)
        new_height = max(2.0, (y1 - y0) * scale_factor)
        axis.set_xlim(x_center - new_width / 2.0, x_center + new_width / 2.0)
        axis.set_ylim(y_center - new_height / 2.0, y_center + new_height / 2.0)

    def bind_navigation(fig, axis) -> None:
        # Mouse wheel scrolls vertically; Ctrl/Cmd + wheel zooms.
        def on_scroll(event) -> None:
            if event.inaxes != axis:
                return
            button = getattr(event, "button", "")
            modifier = getattr(event, "key", None)
            is_zoom = modifier in {"control", "ctrl", "cmd", "super", "meta"}
            direction = -1 if button == "up" else 1
            if is_zoom:
                zoom(axis, 0.85 if direction < 0 else 1.15)
            else:
                span = axis.get_ylim()[1] - axis.get_ylim()[0]
                pan_vertical(axis, direction * max(1.0, span * 0.1))
            fig.canvas.draw_idle()

        def on_key_press(event) -> None:
            if event.key in {"up", "k"}:
                span = axis.get_ylim()[1] - axis.get_ylim()[0]
                pan_vertical(axis, max(1.0, span * 0.1))
            elif event.key in {"down", "j"}:
                span = axis.get_ylim()[1] - axis.get_ylim()[0]
                pan_vertical(axis, -max(1.0, span * 0.1))
            elif event.key in {"+", "=", "i"}:
                zoom(axis, 0.85)
            elif event.key in {"-", "_", "o"}:
                zoom(axis, 1.15)
            else:
                return
            fig.canvas.draw_idle()

        fig.canvas.mpl_connect("scroll_event", on_scroll)
        fig.canvas.mpl_connect("key_press_event", on_key_press)

    grid, meta = build_plane_grid(snapshot, plane_y, display_all_layers=display_all_layers)
    color_map = {
        ".": "#f5f5f5",
        "#": "#4b5563",
        "~": "#60a5fa",
        "!": "#ef4444",
        "G": "#bfdbfe",
        "*": "#22c55e",
        "F": "#a16207",
        "D": "#b45309",
        "B": "#2563eb",
        "l": "#2563eb",
        "r": "#dc2626",
        "T": "#e5e7eb",
        "L": "#2563eb",
        "R": "#dc2626",
        "W": "#f97316",
        "S": "#64748b",
        "P": "#8b5cf6",
        "M": "#ec4899",
        "E": "#14b8a6",
        "?": "#9ca3af",
    }

    fig_width = max(6, len(grid[0]) * 0.4)
    fig_height = max(6, len(grid) * 0.4)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    for z_index, row in enumerate(grid):
        for x_index, symbol in enumerate(row):
            y_plot = len(grid) - 1 - z_index
            ax.add_patch(
                Rectangle(
                    (x_index, y_plot),
                    1,
                    1,
                    facecolor=color_map.get(symbol, color_map["?"]),
                    edgecolor="#d1d5db",
                    linewidth=0.5,
                )
            )
            if symbol != ".":
                ax.text(
                    x_index + 0.5,
                    y_plot + 0.5,
                    symbol,
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white" if symbol in {"#", "!", "B", "P", "M", "l", "r", "L", "R", "W"} else "black",
                )

    ax.set_xlim(0, len(grid[0]))
    ax.set_ylim(0, len(grid))
    ax.set_aspect("equal")
    ax.set_xticks(range(len(grid[0])))
    ax.set_yticks(range(len(grid)))
    ax.set_xticklabels([meta["min_x"] + index for index in range(len(grid[0]))], rotation=90)
    ax.set_yticklabels(
        [meta["min_z"] + index for index in range(len(grid) - 1, -1, -1)]
    )
    ax.set_xlabel("x")
    ax.set_ylabel("z")
    if meta["use_projection"]:
        ax.set_title(
            "2D object projection for "
            f"y={meta['min_y']}..{meta['max_y']} (object-priority view)\n"
            "Wheel: scroll vertically, Ctrl/Cmd+Wheel: zoom, +/-: zoom"
        )
    else:
        ax.set_title(
            f"2D object layout at y={meta['plane_y']}\n"
            "Wheel: scroll vertically, Ctrl/Cmd+Wheel: zoom, +/-: zoom"
        )
    ax.grid(False)
    plt.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=200, bbox_inches="tight")
    else:  # pragma: no cover - requires interactive backend
        bind_navigation(fig, ax)
        plt.show()
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost", help="Minecraft server hostname.")
    parser.add_argument("--port", type=int, default=25565, help="Minecraft server port.")
    parser.add_argument(
        "--username",
        default="map_export_bot",
        help="Mineflayer bot username used for the connection.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().with_name("map_snapshot.json"),
        help="Where to save the JSON snapshot.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Load an existing snapshot JSON instead of reconnecting to the server.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=16,
        help="If explicit bounds are not provided, scan a square centered on the bot.",
    )
    parser.add_argument(
        "--plane-y",
        type=int,
        default=1,
        help="Default slice height used for scanning and 2D visualization.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Seconds to wait after spawn so nearby chunks/objects can load.",
    )
    parser.add_argument("--min-x", type=int)
    parser.add_argument("--max-x", type=int)
    parser.add_argument("--min-y", type=int)
    parser.add_argument("--max-y", type=int)
    parser.add_argument("--min-z", type=int)
    parser.add_argument("--max-z", type=int)
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Render the y-slice with matplotlib in addition to ASCII output.",
    )
    parser.add_argument(
        "--figure",
        type=Path,
        help="Optional output image path for the matplotlib visualization.",
    )
    parser.add_argument(
        "--no-space",
        action="store_true",
        help="Print the ASCII slice without spaces between grid cells.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    display_all_layers = args.min_y is not None and args.max_y is not None

    if args.input is not None:
        snapshot = load_snapshot(args.input)
    else:
        snapshot = snapshot_world(
            args.host,
            args.port,
            args.username,
            radius=args.radius,
            plane_y=args.plane_y,
            settle_seconds=args.settle_seconds,
            min_x=args.min_x,
            max_x=args.max_x,
            min_y=args.min_y,
            max_y=args.max_y,
            min_z=args.min_z,
            max_z=args.max_z,
        )
        write_snapshot(snapshot, args.output)
        print(f"Wrote snapshot JSON to {args.output}")

    print(
        render_ascii_slice(
            snapshot,
            args.plane_y,
            no_space=args.no_space,
            display_all_layers=display_all_layers,
        )
    )
    unknown_blocks = list_unknown_blocks(
        snapshot,
        args.plane_y,
        display_all_layers=display_all_layers,
    )
    if unknown_blocks:
        print("Blocks rendered as '?':")
        for block_name in unknown_blocks:
            print(f"  - {block_name}")

    if args.plot or args.figure is not None:
        render_matplotlib_slice(
            snapshot,
            args.plane_y,
            save_path=args.figure,
            display_all_layers=display_all_layers,
        )
        if args.figure is not None:
            print(f"Saved figure to {args.figure}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

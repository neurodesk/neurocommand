#!/usr/bin/env python3
"""Sync recipe icons from NeuroDesk/neurocontainers into neurodesk/icons."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys


DATA_URI_RE = re.compile(
    r"^data:image/(?P<media_type>[a-zA-Z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=\s]+)$"
)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class RecipeIcon:
    name: str
    source: Path
    content: bytes


@dataclass
class SyncResult:
    matched_recipes: int = 0
    icons_found: int = 0
    unsupported_icons: list[Path] = field(default_factory=list)
    invalid_icons: list[Path] = field(default_factory=list)
    changed_icons: list[Path] = field(default_factory=list)
    written_icons: list[Path] = field(default_factory=list)


def _parse_top_level_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return None

    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _load_app_names(apps_json_path: Path) -> set[str]:
    with apps_json_path.open() as apps_json_file:
        apps = json.load(apps_json_file)

    if not isinstance(apps, dict):
        raise ValueError(f"{apps_json_path} must contain a JSON object")

    return set(apps)


def _decode_png_data_uri(data_uri: str, source: Path) -> bytes | None:
    match = DATA_URI_RE.match(data_uri)
    if not match:
        return None

    if match.group("media_type").lower() != "png":
        return None

    try:
        content = base64.b64decode(match.group("payload"), validate=True)
    except ValueError as error:
        raise ValueError(f"{source}: invalid base64 icon data: {error}") from error

    if not content.startswith(PNG_MAGIC):
        raise ValueError(f"{source}: decoded icon is not a PNG")

    return content


def _iter_build_files(neurocontainers_path: Path) -> list[Path]:
    recipes_path = neurocontainers_path / "recipes"
    if not recipes_path.is_dir():
        raise FileNotFoundError(f"{recipes_path} does not exist")

    build_files = list(recipes_path.glob("*/build.yaml"))
    build_files.extend(recipes_path.glob("*/build.yml"))
    return sorted(build_files)


def collect_recipe_icons(
    neurocontainers_path: Path,
    app_names: set[str],
    result: SyncResult,
) -> list[RecipeIcon]:
    icons: list[RecipeIcon] = []

    for build_file in _iter_build_files(neurocontainers_path):
        text = build_file.read_text()
        declared_name = _parse_top_level_scalar(text, "name")
        recipe_name = build_file.parent.name

        if recipe_name in app_names:
            icon_name = recipe_name
        elif declared_name in app_names:
            icon_name = declared_name
        else:
            continue

        result.matched_recipes += 1
        icon_value = _parse_top_level_scalar(text, "icon")
        if not icon_value:
            continue

        result.icons_found += 1
        try:
            content = _decode_png_data_uri(icon_value, build_file)
        except ValueError:
            result.invalid_icons.append(build_file)
            raise

        if content is None:
            result.unsupported_icons.append(build_file)
            continue

        icons.append(RecipeIcon(name=icon_name, source=build_file, content=content))

    return icons


def sync_icons(
    *,
    neurocontainers_path: Path,
    icons_dir: Path,
    apps_json_path: Path,
    check: bool = False,
) -> SyncResult:
    result = SyncResult()
    app_names = _load_app_names(apps_json_path)
    recipe_icons = collect_recipe_icons(neurocontainers_path, app_names, result)

    if not check:
        icons_dir.mkdir(parents=True, exist_ok=True)

    for recipe_icon in recipe_icons:
        target = icons_dir / f"{recipe_icon.name}.png"
        existing_content = target.read_bytes() if target.exists() else None
        if existing_content == recipe_icon.content:
            continue

        result.changed_icons.append(target)
        if not check:
            target.write_bytes(recipe_icon.content)
            result.written_icons.append(target)

    return result


def _print_result(result: SyncResult, *, check: bool) -> None:
    action = "would update" if check else "updated"
    print(
        "Matched "
        f"{result.matched_recipes} neurocommand apps; found "
        f"{result.icons_found} upstream icon fields; {action} "
        f"{len(result.changed_icons)} icon file(s)."
    )

    for icon_path in result.changed_icons:
        print(f"  {icon_path}")

    for build_file in result.unsupported_icons:
        print(f"warning: unsupported icon data URI in {build_file}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decode recipe icons from neurocontainers build.yaml files."
    )
    parser.add_argument(
        "--neurocontainers-path",
        type=Path,
        default=Path("../neurocontainers"),
        help="Path to a checkout of NeuroDesk/neurocontainers.",
    )
    parser.add_argument(
        "--apps-json",
        type=Path,
        default=Path("neurodesk/apps.json"),
        help="Path to neurocommand's apps.json.",
    )
    parser.add_argument(
        "--icons-dir",
        type=Path,
        default=Path("neurodesk/icons"),
        help="Directory where decoded PNG icons are stored.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write files; exit non-zero if icons are out of sync.",
    )

    args = parser.parse_args(argv)

    result = sync_icons(
        neurocontainers_path=args.neurocontainers_path,
        icons_dir=args.icons_dir,
        apps_json_path=args.apps_json,
        check=args.check,
    )
    _print_result(result, check=args.check)

    if args.check and result.changed_icons:
        print("error: neurodesk icons are out of sync", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

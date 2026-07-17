#!/usr/bin/env python3
"""Keep apps.json categories wired into every maintained menu location."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as et


NEURODESK_MENU_END = "\t</Menu> <!-- End Neurodesk -->"
BUILD_DIRECTORY_CALLS_RE = re.compile(
    r"(?P<calls>(?:        write_directory_file\([^\n]+\)\n)+)"
    r"(?=\n    appsjson = Path\()"
)
DIRECTORY_NAMES_RE = re.compile(
    r"(?P<prefix>DIRECTORY_NAMES = \[\n)"
    r"(?P<entries>(?:    \"[^\n]+\",\n)*)"
    r"(?P<suffix>\])"
)
WRITE_DIRECTORY_NAME_RE = re.compile(r'write_directory_file\("([^"]+)"')
QUOTED_LIST_ENTRY_RE = re.compile(r'^    "([^"]+)",$', re.MULTILINE)


@dataclass
class SyncResult:
    missing_menu_categories: list[str] = field(default_factory=list)
    missing_build_directories: list[str] = field(default_factory=list)
    missing_icon_test_directories: list[str] = field(default_factory=list)
    missing_icons: list[Path] = field(default_factory=list)
    changed_files: list[Path] = field(default_factory=list)

    @property
    def changes_needed(self) -> bool:
        return any(
            (
                self.missing_menu_categories,
                self.missing_build_directories,
                self.missing_icon_test_directories,
                self.missing_icons,
            )
        )


def category_slug(category: str) -> str:
    """Return the category identifier used by freedesktop menu entries."""
    return "-".join(category.strip().lower().split())


def category_display_name(category: str) -> str:
    """Create the human-facing menu name used for a new category."""
    initialisms = {
        "bids": "BIDS",
        "eeg": "EEG",
        "em": "EM",
        "fmri": "fMRI",
        "meg": "MEG",
        "mri": "MRI",
        "pet": "PET",
    }
    return " ".join(
        initialisms.get(word.lower(), word.capitalize())
        for word in category.strip().split()
    )


def load_apps_categories(apps_json_path: Path) -> dict[str, str]:
    with apps_json_path.open() as apps_json_file:
        apps = json.load(apps_json_file)

    if not isinstance(apps, dict):
        raise ValueError(f"{apps_json_path} must contain a JSON object")

    categories: dict[str, str] = {}
    for menu_name, menu_data in apps.items():
        if not isinstance(menu_data, dict):
            raise ValueError(f"{apps_json_path}: {menu_name!r} must contain an object")
        menu_categories = menu_data.get("categories") or []
        if not isinstance(menu_categories, list):
            raise ValueError(
                f"{apps_json_path}: categories for {menu_name!r} must be a list"
            )
        for category in menu_categories:
            if not isinstance(category, str) or not category.strip():
                raise ValueError(
                    f"{apps_json_path}: categories for {menu_name!r} must be "
                    "non-empty strings"
                )
            categories.setdefault(category_slug(category), category.strip())
    return categories


def load_menu_categories(menu_path: Path) -> dict[str, str]:
    root = et.parse(menu_path).getroot()
    neurodesk_menu = next(
        (
            menu
            for menu in root.findall("Menu")
            if menu.findtext("Name") == "Neurodesk"
        ),
        None,
    )
    if neurodesk_menu is None:
        raise ValueError(f"{menu_path}: Neurodesk menu was not found")

    categories: dict[str, str] = {}
    for submenu in neurodesk_menu.findall("Menu"):
        category = submenu.findtext("./Include/And/Category")
        name = submenu.findtext("Name")
        if category and name:
            categories[category] = name
    return categories


def _menu_block(name: str, slug: str) -> str:
    return (
        "\t\t<Menu>\n"
        f"\t\t\t<Name>{name}</Name>\n"
        f"\t\t\t<Directory>neurodesk/{slug}.directory</Directory>\n"
        "\t\t\t<Include>\n"
        "\t\t\t\t<And>\n"
        f"\t\t\t\t\t<Category>{slug}</Category>\n"
        "\t\t\t\t</And>\n"
        "\t\t\t</Include>\n"
        "\t\t</Menu>\n"
    )


def _add_menu_categories(menu_path: Path, categories: list[tuple[str, str]]) -> None:
    text = menu_path.read_text()
    if text.count(NEURODESK_MENU_END) != 1:
        raise ValueError(
            f"{menu_path}: expected one {NEURODESK_MENU_END!r} insertion marker"
        )
    blocks = "".join(_menu_block(name, slug) for slug, name in categories)
    menu_path.write_text(text.replace(NEURODESK_MENU_END, blocks + NEURODESK_MENU_END))
    et.parse(menu_path)


def _build_directory_names(build_menu_path: Path) -> set[str]:
    match = BUILD_DIRECTORY_CALLS_RE.search(build_menu_path.read_text())
    if not match:
        raise ValueError(f"{build_menu_path}: directory creation block was not found")
    return set(WRITE_DIRECTORY_NAME_RE.findall(match.group("calls")))


def _add_build_directories(build_menu_path: Path, names: list[str]) -> None:
    text = build_menu_path.read_text()
    match = BUILD_DIRECTORY_CALLS_RE.search(text)
    if not match:
        raise ValueError(f"{build_menu_path}: directory creation block was not found")
    additions = "".join(
        f'        write_directory_file("{name}", directories_path, icon_dir)\n'
        for name in names
    )
    updated_calls = match.group("calls") + additions
    build_menu_path.write_text(text[: match.start("calls")] + updated_calls + text[match.end("calls") :])


def _icon_test_directory_names(icon_test_path: Path) -> set[str]:
    match = DIRECTORY_NAMES_RE.search(icon_test_path.read_text())
    if not match:
        raise ValueError(f"{icon_test_path}: DIRECTORY_NAMES list was not found")
    return set(QUOTED_LIST_ENTRY_RE.findall(match.group("entries")))


def _add_icon_test_directories(icon_test_path: Path, names: list[str]) -> None:
    text = icon_test_path.read_text()
    match = DIRECTORY_NAMES_RE.search(text)
    if not match:
        raise ValueError(f"{icon_test_path}: DIRECTORY_NAMES list was not found")
    additions = "".join(f'    "{name}",\n' for name in names)
    updated = match.group("prefix") + match.group("entries") + additions + match.group("suffix")
    icon_test_path.write_text(text[: match.start()] + updated + text[match.end() :])


def _category_icon_path(icons_dir: Path, display_name: str) -> Path:
    return icons_dir / f"{display_name.lower().split()[0]}.png"


def sync_categories(
    *,
    apps_json_path: Path,
    menu_path: Path,
    build_menu_path: Path,
    icon_test_path: Path,
    icons_dir: Path,
    check: bool = False,
) -> SyncResult:
    required_categories = load_apps_categories(apps_json_path)
    menu_categories = load_menu_categories(menu_path)

    display_names = {
        slug: menu_categories.get(slug, category_display_name(category))
        for slug, category in required_categories.items()
    }
    missing_menu = sorted(set(required_categories) - set(menu_categories))
    build_names = _build_directory_names(build_menu_path)
    icon_test_names = _icon_test_directory_names(icon_test_path)
    missing_build = sorted(
        (name for name in display_names.values() if name not in build_names),
        key=str.casefold,
    )
    missing_icon_test = sorted(
        (name for name in display_names.values() if name not in icon_test_names),
        key=str.casefold,
    )
    missing_icons = sorted(
        {
            _category_icon_path(icons_dir, name)
            for name in display_names.values()
            if not _category_icon_path(icons_dir, name).exists()
        }
    )

    result = SyncResult(
        missing_menu_categories=missing_menu,
        missing_build_directories=missing_build,
        missing_icon_test_directories=missing_icon_test,
        missing_icons=missing_icons,
    )
    if check or not result.changes_needed:
        return result

    if missing_menu:
        _add_menu_categories(
            menu_path,
            [(slug, display_names[slug]) for slug in missing_menu],
        )
        result.changed_files.append(menu_path)
    if missing_build:
        _add_build_directories(build_menu_path, missing_build)
        result.changed_files.append(build_menu_path)
    if missing_icon_test:
        _add_icon_test_directories(icon_test_path, missing_icon_test)
        result.changed_files.append(icon_test_path)
    if missing_icons:
        fallback_icon = icons_dir / "neurodesk.png"
        if not fallback_icon.is_file():
            raise FileNotFoundError(f"fallback category icon {fallback_icon} does not exist")
        for icon_path in missing_icons:
            shutil.copyfile(fallback_icon, icon_path)
            result.changed_files.append(icon_path)

    return result


def _print_result(result: SyncResult, *, check: bool) -> None:
    if not result.changes_needed:
        print("All apps.json categories are fully defined in the menu.")
        return

    action = "Need" if check else "Added"
    if result.missing_menu_categories:
        print(f"{action} menu categories: {', '.join(result.missing_menu_categories)}")
    if result.missing_build_directories:
        print(f"{action} build directories: {', '.join(result.missing_build_directories)}")
    if result.missing_icon_test_directories:
        print(
            f"{action} icon-test directories: "
            f"{', '.join(result.missing_icon_test_directories)}"
        )
    if result.missing_icons:
        print(f"{action} category icons:")
        for icon_path in result.missing_icons:
            print(f"  {icon_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add missing apps.json categories to every maintained menu location."
    )
    parser.add_argument("--apps-json", type=Path, default=Path("neurodesk/apps.json"))
    parser.add_argument(
        "--menu", type=Path, default=Path("neurodesk/neurodesk-applications.menu")
    )
    parser.add_argument(
        "--build-menu", type=Path, default=Path("neurodesk/build_menu.py")
    )
    parser.add_argument(
        "--icon-test", type=Path, default=Path("test/test_icon_coverage.py")
    )
    parser.add_argument("--icons-dir", type=Path, default=Path("neurodesk/icons"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="report missing category wiring without changing files",
    )
    args = parser.parse_args(argv)

    result = sync_categories(
        apps_json_path=args.apps_json,
        menu_path=args.menu,
        build_menu_path=args.build_menu,
        icon_test_path=args.icon_test,
        icons_dir=args.icons_dir,
        check=args.check,
    )
    _print_result(result, check=args.check)
    return 1 if args.check and result.changes_needed else 0


if __name__ == "__main__":
    sys.exit(main())

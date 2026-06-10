#!/usr/bin/env python3
"""Reconcile CVMFS modulefiles with the latest kept container builds."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Optional


@dataclass(frozen=True)
class ContainerEntry:
    image: str
    tool: str
    version: str
    builddate: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    content: Optional[str]
    reason: str


def parse_image_name(image: str) -> tuple[str, str, str]:
    parts = image.split("_")
    if len(parts) < 3:
        raise ValueError(f"invalid container image name in log.txt: {image}")
    return parts[0], parts[1], parts[2]


def normalize_category(category: str) -> str:
    return category.strip().replace(" ", "_")


def parse_log(log_path: Path) -> list[ContainerEntry]:
    entries: list[ContainerEntry] = []
    for line_number, raw_line in enumerate(log_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        image = line.split(maxsplit=1)[0]
        tool, version, builddate = parse_image_name(image)
        categories_text = line.split("categories:", 1)[1] if "categories:" in line else ""
        categories = tuple(
            category
            for category in (normalize_category(item) for item in categories_text.split(","))
            if category
        )
        entries.append(
            ContainerEntry(
                image=image,
                tool=tool,
                version=version,
                builddate=builddate,
                categories=categories,
            )
        )

    return entries


def latest_existing_kept_entries(
    repo_root: Path, entries: list[ContainerEntry]
) -> dict[tuple[str, str], ContainerEntry]:
    latest: dict[tuple[str, str], ContainerEntry] = {}
    containers_root = repo_root / "containers"

    for entry in entries:
        container_path = containers_root / entry.image
        if not (container_path / "commands.txt").is_file():
            continue

        key = (entry.tool, entry.version)
        if key not in latest or entry.builddate > latest[key].builddate:
            latest[key] = entry

    return latest


def categories_by_key(entries: list[ContainerEntry]) -> dict[tuple[str, str], tuple[str, ...]]:
    categories: dict[tuple[str, str], list[str]] = {}

    for entry in entries:
        key = (entry.tool, entry.version)
        categories.setdefault(key, [])
        for category in entry.categories:
            if category not in categories[key]:
                categories[key].append(category)

    return {key: tuple(value) for key, value in categories.items()}


def update_module_content(
    content: str,
    *,
    tool: str,
    version: str,
    latest_name: str,
    latest_dir: Path,
) -> str:
    container_pattern = rf"{re.escape(tool)}_{re.escape(version)}_[0-9]+"
    latest_dir_text = str(latest_dir)

    content = sanitize_module_help_content(content)
    content = re.sub(
        rf'prepend_path\("PATH", "[^"]*{container_pattern}"\)',
        f'prepend_path("PATH", "{latest_dir_text}")',
        content,
    )
    content = re.sub(
        rf'whatis\("{container_pattern}"\)',
        f'whatis("{latest_name}")',
        content,
    )
    return re.sub(container_pattern, latest_name, content)


def sanitize_help_text(text: str) -> str:
    return text.replace("]]", "] ]")


def sanitize_module_help_content(content: str) -> str:
    return re.sub(
        r"(help\(\[===\[)(.*?)(\]===\]\))",
        lambda match: match.group(1) + sanitize_help_text(match.group(2)) + match.group(3),
        content,
        flags=re.DOTALL,
    )


def existing_public_module_candidates(
    public_modules_root: Path, tool: str, version: str
) -> list[Path]:
    candidates: list[Path] = []
    if not public_modules_root.is_dir():
        return candidates

    for category_dir in sorted(path for path in public_modules_root.iterdir() if path.is_dir()):
        module_dir = category_dir / tool
        candidates.extend((module_dir / f"{version}.lua", module_dir / version))

    return [candidate for candidate in candidates if candidate.is_file()]


def public_module_category(public_modules_root: Path, module_file: Path) -> Optional[str]:
    try:
        return module_file.relative_to(public_modules_root).parts[0]
    except (IndexError, ValueError):
        return None


def add_change(
    changes: dict[Path, PlannedChange],
    path: Path,
    content: str,
    reason: str,
) -> None:
    if path.exists() and path.read_text() == content:
        return
    changes[path] = PlannedChange(path=path, content=content, reason=reason)


def add_delete(changes: dict[Path, PlannedChange], path: Path, reason: str) -> None:
    if path.exists():
        changes[path] = PlannedChange(path=path, content=None, reason=reason)


def plan_module_reconciliation(repo_root: Path, log_path: Path) -> list[PlannedChange]:
    entries = parse_log(log_path)
    latest_by_key = latest_existing_kept_entries(repo_root, entries)
    categories = categories_by_key(entries)
    containers_root = repo_root / "containers"
    canonical_modules_root = containers_root / "modules"
    public_modules_root = repo_root / "neurodesk-modules"
    changes: dict[Path, PlannedChange] = {}

    for (tool, version), entry in sorted(latest_by_key.items()):
        latest_name = entry.image
        latest_dir = containers_root / latest_name
        expected_public_categories = set(categories.get((tool, version), ()))

        canonical_contents: dict[str, str] = {}
        canonical_candidates = (
            canonical_modules_root / tool / f"{version}.lua",
            canonical_modules_root / tool / version,
        )
        for module_file in canonical_candidates:
            if not module_file.is_file():
                continue

            updated = update_module_content(
                module_file.read_text(),
                tool=tool,
                version=version,
                latest_name=latest_name,
                latest_dir=latest_dir,
            )
            canonical_contents[module_file.name] = updated
            add_change(
                changes,
                module_file,
                updated,
                f"point canonical {tool}/{version} at {latest_name}",
            )

        for module_file in existing_public_module_candidates(public_modules_root, tool, version):
            module_category = public_module_category(public_modules_root, module_file)
            if module_category not in expected_public_categories:
                add_delete(
                    changes,
                    module_file,
                    f"remove stale public {module_category}/{tool}/{module_file.name}",
                )
                continue

            updated = update_module_content(
                module_file.read_text(),
                tool=tool,
                version=version,
                latest_name=latest_name,
                latest_dir=latest_dir,
            )
            add_change(
                changes,
                module_file,
                updated,
                f"point existing public {tool}/{version} at {latest_name}",
            )

        for category in categories.get((tool, version), ()):
            for filename, content in canonical_contents.items():
                target = public_modules_root / category / tool / filename
                add_change(
                    changes,
                    target,
                    content,
                    f"sync public {category}/{tool}/{filename} from canonical module",
                )

    return [changes[path] for path in sorted(changes)]


def apply_changes(changes: list[PlannedChange]) -> None:
    for change in changes:
        if change.content is None:
            change.path.unlink(missing_ok=True)
        else:
            change.path.parent.mkdir(parents=True, exist_ok=True)
            change.path.write_text(change.content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update CVMFS modulefiles to the latest kept container builds."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("/cvmfs/neurodesk.ardc.edu.au"),
        help="Mounted neurodesk CVMFS repository root.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        required=True,
        help="Path to cvmfs/log.txt.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report whether changes are needed. Exits 1 when changes are needed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    changes = plan_module_reconciliation(args.repo_root, args.log)

    for change in changes:
        print(f"[INFO] {change.reason}: {change.path}")

    if args.check:
        if changes:
            print(f"[INFO] Module reconciliation would change {len(changes)} file(s).")
            return 1
        print("[INFO] Module reconciliation is already up to date.")
        return 0

    apply_changes(changes)
    print(f"[INFO] Module reconciliation changed {len(changes)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

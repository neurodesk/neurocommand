#!/usr/bin/env python3
"""Reconcile CVMFS modulefiles with the latest kept container builds."""

from __future__ import annotations

import argparse
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
import re
import sys
from typing import Optional


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "neurodesk/transparent-singularity"))
from artifact_renderer import read_container_inventory, render_module, managed_module_content, safe_command, write_artifact

EXPOSED_COMMANDS_MARKER = "neurodesk-exposed-commands"
MANUAL_MODULE_BEGIN = "-- neurodesk-manual-module-begin"
DEFAULT_MANUAL_MODULE_DIR = (
    Path(__file__).resolve().parents[1]
    / "neurodesk/transparent-singularity/manual_module_files"
)
LEGACY_MANUAL_HEADERS = {
    "freesurfer": "Append custom paths",
    "matlab": "Append custom license paths so that a license can be stored outside the container",
}
EXPOSED_COMMANDS_BLOCK = re.compile(
    rf"(?m)^(?:--|#) {re.escape(EXPOSED_COMMANDS_MARKER)}\r?\n"
    r"(?:"
    r'(?P<whatis>whatis\("Commands: [^\r\n]*\)\r?\n?'
    r'|module-whatis "Commands: [^\r\n]*\r?\n?'
    r")"
    r"|extensions[^\r\n]*\r?\n?"
    r'|if type\(extensions\) == "function" then\r?\n'
    r"[ \t]+extensions\([^\r\n]*\)\r?\n"
    r"end\r?\n?"
    r"|if \{\[llength \[info commands extensions\]\] > 0\} \{\r?\n"
    r"[ \t]+extensions[^\r\n]*\r?\n"
    r"\}\r?\n?"
    r")"
)


@dataclass(frozen=True)
class ContainerEntry:
    image: str
    tool: str
    version: str
    builddate: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    ctime_ns: int
    content: bytes


def read_snapshot(path: Path) -> FileSnapshot | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Module target is not a regular file: {path}")
        content = source.read()
        after = os.fstat(source.fileno())
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size",
                     "st_mtime_ns", "st_ctime_ns")
    if (any(getattr(before, field) != getattr(after, field) for field in stable_fields)
            or len(content) != after.st_size):
        raise ValueError(f"Module changed while reading: {path}")
    return FileSnapshot(after.st_dev, after.st_ino, after.st_mode, after.st_uid,
                        after.st_gid, after.st_size, after.st_mtime_ns,
                        after.st_ctime_ns, content)


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    content: Optional[str]
    reason: str
    before: FileSnapshot | None


def parse_image_name(image: str) -> tuple[str, str, str]:
    stem = image.removesuffix(".simg")
    try:
        name_and_version, builddate = stem.rsplit("_", 1)
        tool, version = name_and_version.rsplit("_", 1)
    except ValueError:
        raise ValueError(f"invalid container image name in log.txt: {image}") from None
    if not tool or not version or not re.fullmatch(r"[0-9]{8}", builddate):
        raise ValueError(f"invalid container image name in log.txt: {image}")
    return tool, version, builddate


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
        if (container_path / "env.txt").is_file() and not any(
            safe_command(command)
            for command in (container_path / "commands.txt").read_text().splitlines()
        ):
            print(
                f"[WARN] Skipping incomplete container inventory {container_path}: "
                "commands.txt contains no usable commands",
                file=sys.stderr,
            )
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


def exposed_commands(commands_path: Path) -> tuple[str, ...]:
    """Return discoverable commands, excluding hidden files and shared libraries."""
    # Keep this filter in sync with ts_command_metadata.sh.
    library_suffix = re.compile(r"\.(?:so(?:\..*)?|dylib|dll)$", re.IGNORECASE)
    commands = {
        command
        for raw_command in commands_path.read_text().splitlines()
        if (command := raw_command.rstrip("\r"))
        and not command.startswith(".")
        and not library_suffix.search(command)
        and not any(character.isspace() for character in command)
        and "/" not in command
    }
    return tuple(sorted(commands))


def lua_double_quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def tcl_double_quoted(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("$", "\\$").replace("[", "\\[").replace("]", "\\]")
    return f'"{escaped}"'


def tcl_render_quoted(text: str) -> str:
    """Quote like ts_render_artifacts.sh's tcl_quote."""
    for raw, escaped in (
        ("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("[", "\\["), ("]", "\\]"),
        ("{", "\\{"), ("}", "\\}"), ("\n", "\\n"), ("\r", "\\r"),
    ):
        text = text.replace(raw, escaped)
    return f'"{text}"'


def inventory_lines(path: Path) -> list[str]:
    with path.open(newline="") as inventory:
        lines = inventory.read().split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def render_tcl_module(container_dir: Path) -> str:
    return render_module(read_container_inventory(container_dir), format="tcl").decode()


def render_exposed_commands(commands_path: Path, *, is_lua: bool = True) -> str:
    commands = exposed_commands(commands_path)
    if not commands:
        return ""

    description = "Commands: " + ", ".join(commands)
    if is_lua:
        return f"-- {EXPOSED_COMMANDS_MARKER}\nwhatis({lua_double_quoted(description)})"
    return f"# {EXPOSED_COMMANDS_MARKER}\nmodule-whatis {tcl_double_quoted(description)}"


def mask_lua_help(content: str) -> str:
    """Hide README text while preserving positions for executable Lua matches."""
    return re.sub(
        r"help\(\[===\[.*?\]===\]\)",
        lambda match: re.sub(r"[^\r\n]", " ", match[0]),
        content,
        flags=re.DOTALL,
    )


def update_exposed_commands(
    content: str, commands_path: Path, *, is_lua: bool
) -> str:
    block = render_exposed_commands(commands_path, is_lua=is_lua)
    content = EXPOSED_COMMANDS_BLOCK.sub("", content)

    if not block:
        return content

    whatis_pattern = (
        r'(?m)^whatis\([^\r\n]*\)\r?$'
        if is_lua
        else r"(?m)^module-whatis(?:[ \t]+[^\r\n]*)?\r?$"
    )
    search_content = content
    if is_lua:
        search_content = mask_lua_help(content)
        manual_start = re.search(
            r"(?m)^(?:"
            + "|".join(re.escape(header) for header in (MANUAL_MODULE_BEGIN, *(f"-- {header}" for header in LEGACY_MANUAL_HEADERS.values())))
            + r")\r?$",
            search_content,
        )
        if manual_start:
            search_content = search_content[:manual_start.start()]
    whatis_lines = list(re.finditer(whatis_pattern, search_content))
    if whatis_lines:
        insertion_point = whatis_lines[-1].end()
        return content[:insertion_point] + f"\n{block}" + content[insertion_point:]

    if not is_lua:
        module_header = re.search(r"(?m)^#%Module[^\r\n]*\r?$", content)
        if module_header:
            insertion_point = module_header.end()
            return content[:insertion_point] + f"\n{block}" + content[insertion_point:]

    return f"{block}\n{content}"


def update_module_content(
    content: str,
    *,
    tool: str,
    version: str,
    latest_name: str,
    latest_dir: Path,
    is_lua: bool,
) -> str:
    if (latest_dir / "env.txt").is_file():
        updated = managed_module_content(content, read_container_inventory(latest_dir), format="lua" if is_lua else "tcl", containers_root=latest_dir.parent)
        if updated is None:
            print(f"[WARN] Preserving customized module {tool}/{version}", file=sys.stderr)
            return content
        return updated.decode()
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
    if not is_lua:
        content = re.sub(
            rf'(?m)^prepend-path PATH "(?:\\.|[^"\\])*{container_pattern}"$',
            lambda match: f"prepend-path PATH {tcl_double_quoted(latest_dir_text)}",
            content,
        )
    content = re.sub(container_pattern, lambda match: latest_name, content)
    return update_exposed_commands(
        content, latest_dir / "commands.txt", is_lua=is_lua
    )


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
    before = changes[path].before if path in changes else read_snapshot(path)
    if before is not None and before.content == content.encode():
        changes.pop(path, None)
        return
    changes[path] = PlannedChange(path=path, content=content, reason=reason, before=before)


def add_delete(changes: dict[Path, PlannedChange], path: Path, reason: str) -> None:
    before = changes[path].before if path in changes else read_snapshot(path)
    if before is not None:
        changes[path] = PlannedChange(path=path, content=None, reason=reason, before=before)


def update_manual_module(content: str, *, tool: str, version: str, snippet: str, is_lua: bool = True) -> str:
    comment = "--" if is_lua else "#"
    begin = f"{comment} neurodesk-manual-module-begin"
    end_marker = f"{comment} neurodesk-manual-module-end"
    executable_content = mask_lua_help(content) if is_lua else content
    starts = list(re.finditer(rf"(?m)^{re.escape(begin)}\r?$", executable_content))
    ends = list(re.finditer(rf"(?m)^{re.escape(end_marker)}\r?$", executable_content))
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0].start() >= ends[0].start():
            raise ValueError(f"malformed manual module markers in {tool}/{version}")
        end = ends[0].end()
        if content[end:end + 1] == "\n":
            end += 1
        content = content[:starts[0].start()] + content[end:]
    elif tool in LEGACY_MANUAL_HEADERS:
        header = re.search(
            rf"(?m)^{re.escape(f'{comment} {LEGACY_MANUAL_HEADERS[tool]}')}\r?$",
            executable_content,
        )
        if header:
            content = content[:header.start()]

    if not snippet:
        return content
    if content and not content.endswith("\n"):
        content += "\n"
    rendered = snippet.replace("toolVersion", version).rstrip("\n")
    return f"{content}{begin}\n{rendered}\n{end_marker}\n"


def plan_module_reconciliation(
    repo_root: Path,
    log_path: Path,
    manual_module_dir: Path = DEFAULT_MANUAL_MODULE_DIR,
) -> list[PlannedChange]:
    if not manual_module_dir.is_dir():
        raise ValueError(f"manual module directory does not exist: {manual_module_dir}")
    snippets = {
        path.name: path.read_text()
        for path in manual_module_dir.iterdir()
        if path.is_file()
    }
    tcl_dir = manual_module_dir / "tcl"
    tcl_snippets = {
        path.name: path.read_text()
        for path in tcl_dir.iterdir()
        if path.is_file()
    } if tcl_dir.is_dir() else {}
    entries = parse_log(log_path)
    latest_by_key = latest_existing_kept_entries(repo_root, entries)
    categories = categories_by_key(entries)
    containers_root = repo_root / "containers"
    canonical_modules_root = containers_root / "modules"
    public_modules_root = repo_root / "neurodesk-modules"
    changes: dict[Path, PlannedChange] = {}
    protected: set[Path] = set()
    snapshots: dict[Path, FileSnapshot] = {}

    def read_module(path: Path) -> str:
        if path not in snapshots:
            snapshot = read_snapshot(path)
            if snapshot is None:
                raise FileNotFoundError(f"Module disappeared while planning: {path}")
            snapshots[path] = snapshot
        return snapshots[path].content.decode()

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
            if not module_file.is_file() or module_file.is_symlink():
                continue

            if (latest_dir / "env.txt").is_file() and managed_module_content(read_module(module_file), read_container_inventory(latest_dir), format="lua" if module_file.suffix == ".lua" else "tcl", containers_root=latest_dir.parent) is None:
                protected.add(module_file)
            updated = update_module_content(
                read_module(module_file),
                tool=tool,
                version=version,
                latest_name=latest_name,
                latest_dir=latest_dir,
                is_lua=module_file.suffix == ".lua",
            )
            canonical_contents[module_file.name] = updated
            add_change(
                changes,
                module_file,
                updated,
                f"point canonical {tool}/{version} at {latest_name}",
            )

        # Containers deployed before Tcl support only have Lua modulefiles.
        tcl_module = canonical_modules_root / tool / version
        if (
            version not in canonical_contents
            and not tcl_module.is_symlink()
            and f"{version}.lua" in canonical_contents
            and (latest_dir / "env.txt").is_file()
        ):
            canonical_contents[version] = render_tcl_module(latest_dir)
            add_change(
                changes,
                tcl_module,
                canonical_contents[version],
                f"generate canonical Tcl {tool}/{version} from {latest_name} inventories",
            )

        for module_file in existing_public_module_candidates(public_modules_root, tool, version):
            if module_file.is_symlink():
                continue
            module_category = public_module_category(public_modules_root, module_file)
            if module_category not in expected_public_categories:
                if "neurodesk-bundle-v1" in read_module(module_file) or ((latest_dir / "env.txt").is_file() and managed_module_content(read_module(module_file), read_container_inventory(latest_dir), format="lua" if module_file.suffix == ".lua" else "tcl", containers_root=latest_dir.parent) is None):
                    protected.add(module_file)
                    continue
                add_delete(
                    changes,
                    module_file,
                    f"remove stale public {module_category}/{tool}/{module_file.name}",
                )
                continue

            if (latest_dir / "env.txt").is_file() and managed_module_content(read_module(module_file), read_container_inventory(latest_dir), format="lua" if module_file.suffix == ".lua" else "tcl", containers_root=latest_dir.parent) is None:
                protected.add(module_file)
            updated = update_module_content(
                read_module(module_file),
                tool=tool,
                version=version,
                latest_name=latest_name,
                latest_dir=latest_dir,
                is_lua=module_file.suffix == ".lua",
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
                if target in protected or target.is_symlink():
                    continue
                add_change(
                    changes,
                    target,
                    content,
                    f"sync public {category}/{tool}/{filename} from canonical module",
                )

    # Retired versions and containers without inventories are skipped above.
    for root, pattern in (
        (canonical_modules_root, "*/*"),
        (public_modules_root, "*/*/*"),
    ):
        for module_file in root.glob(pattern):
            if module_file in changes or module_file in protected or module_file.is_symlink() or not module_file.is_file() or module_file.name.startswith("."):
                continue
            content = read_module(module_file)
            if "neurodesk-bundle-v1" in content:
                continue
            cleaned = EXPOSED_COMMANDS_BLOCK.sub(
                lambda match: match[0] if match["whatis"] else "", content
            )
            add_change(
                changes,
                module_file,
                cleaned,
                "remove obsolete generated command extensions",
            )

    module_files = set(canonical_modules_root.glob("*/*"))
    module_files.update(public_modules_root.glob("*/*/*"))
    module_files.update(changes)
    for module_file in sorted(module_files):
        if module_file in protected or module_file.name.startswith(".") or module_file.is_symlink():
            continue
        planned = changes.get(module_file)
        if planned is not None and planned.content is None:
            continue
        if planned is None and not module_file.is_file():
            continue
        content = planned.content if planned is not None else read_module(module_file)
        if "neurodesk-bundle-v1" in content:
            continue
        tool = module_file.parent.name
        if "neurodesk-artifact-v2" in content and content.startswith(("-- -*- lua -*-", "#%Module1.0")):
            entry = latest_by_key.get((tool, module_file.stem if module_file.suffix == ".lua" else module_file.name))
            if entry is not None:
                spec = read_container_inventory(containers_root / entry.image)
                spec = replace(spec, manual_lua=snippets.get(tool, ""), manual_tcl=tcl_snippets.get(tool, ""))
                updated = render_module(spec, format="lua" if module_file.suffix == ".lua" else "tcl").decode()
                if updated != content:
                    add_change(changes, module_file, updated, "refresh generated manual module snippets")
                continue
        updated = update_manual_module(
            content,
            tool=tool,
            version=module_file.stem if module_file.suffix == ".lua" else module_file.name,
            snippet=(snippets if module_file.suffix == ".lua" else tcl_snippets).get(tool, ""),
            is_lua=module_file.suffix == ".lua",
        )
        if updated != content:
            add_change(
                changes,
                path=module_file,
                content=updated,
                reason=f"refresh manual module snippet for {tool}/{module_file.stem}",
            )

    return [replace(changes[path], before=snapshots.get(path, changes[path].before))
            for path in sorted(changes)]


def verify_change(change: PlannedChange) -> None:
    try:
        current = read_snapshot(change.path)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Module changed since planning: {change.path}: {error}") from error
    if current != change.before:
        raise RuntimeError(f"Module changed since planning: {change.path}")


def apply_changes(changes: list[PlannedChange]) -> None:
    """Apply the plan with exclusive access to the deployment files.

    Snapshot checks are not atomic with writes. Failures leave earlier writes applied.
    """
    for change in changes:
        verify_change(change)
    for applied, change in enumerate(changes):
        try:
            verify_change(change)
            if change.content is None:
                change.path.unlink()
            else:
                write_artifact(change.path, change.content.encode())
        except (OSError, ValueError, RuntimeError) as error:
            raise RuntimeError(
                f"Module reconciliation stopped after {applied} applied change(s) "
                f"at {change.path}: {error}"
            ) from error


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
        "--manual-module-dir",
        type=Path,
        default=DEFAULT_MANUAL_MODULE_DIR,
        help="Directory containing current Lua snippets and a tcl subdirectory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report whether changes are needed. Exits 1 when changes are needed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        changes = plan_module_reconciliation(args.repo_root, args.log, args.manual_module_dir)
    except (OSError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2

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

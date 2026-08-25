#!/usr/bin/env python3
"""Add XAUTHORITY forwarding to pre-fix generated CVMFS wrappers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import stat
import sys


XAUTHORITY_BLOCK = (
    b"xauthority_opts=()\n"
    b'if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then\n'
    b'  xauthority_opts=(--bind "$XAUTHORITY:$XAUTHORITY:ro" '
    b'--env "XAUTHORITY=$XAUTHORITY")\n'
    b"fi\n"
)
XAUTHORITY_ARGUMENT = b'"${xauthority_opts[@]}" '
XAUTHORITY_MARKERS = (b"xauthority_opts", b"XAUTHORITY")
DISABLED_NOTICE = b"This container was disabled due to a known bug or vulnerability."
DISABLED_PULL_HINT = b"apptainer pull docker://vnmd/"
GENERATED_BIND_OPTIONS = (
    "",
    "--bind $TMP:/tmp",
    "--bind $TMPDIR:/tmp",
    "--bind $TEMP:/tmp",
    "--bind $TEMPDIR:/tmp",
)


class WrapperState(Enum):
    LEGACY = "legacy"
    FIXED = "fixed"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Diagnostic:
    path: Path
    message: str


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    content: bytes


@dataclass(frozen=True)
class PlannedRewrite:
    path: Path
    before: FileSnapshot
    replacement: bytes


@dataclass(frozen=True)
class ReconciliationPlan:
    rewrites: tuple[PlannedRewrite, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def is_clean(self) -> bool:
        return not self.rewrites and not self.diagnostics


def _safe_command(raw_command: str) -> bool:
    return bool(raw_command) and not (
        raw_command in {".", ".."}
        or Path(raw_command).is_absolute()
        or "/" in raw_command
        or "\\" in raw_command
        or any(character.isspace() for character in raw_command)
        or "\x00" in raw_command
    )


def _parse_commands(commands_path: Path) -> tuple[tuple[str, ...], tuple[Diagnostic, ...]]:
    try:
        snapshot = _read_regular_file(commands_path)
        text = snapshot.content.decode("utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError) as error:
        return (), (Diagnostic(commands_path, f"cannot read command inventory: {error}"),)

    commands: list[str] = []
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for line_number, raw_command in enumerate(text.splitlines(), start=1):
        if not raw_command:
            continue
        # Some inventories also name executables by their absolute path inside
        # the container. They do not correspond to top-level wrapper files.
        if Path(raw_command).is_absolute():
            continue
        if not _safe_command(raw_command):
            diagnostics.append(
                Diagnostic(
                    commands_path,
                    f"unsafe command name on line {line_number}: {raw_command!r}",
                )
            )
            continue
        if raw_command not in seen:
            seen.add(raw_command)
            commands.append(raw_command)

    return tuple(commands), tuple(diagnostics)


def _read_from_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _snapshot_from_descriptor(descriptor: int) -> FileSnapshot:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("not a regular file")

    content = _read_from_descriptor(descriptor)
    after = os.fstat(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != after.st_size
    ):
        raise RuntimeError("file changed while it was being read")

    return FileSnapshot(
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        uid=after.st_uid,
        gid=after.st_gid,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        content=content,
    )


def _open_no_follow(path: Path, flags: int) -> int:
    return os.open(
        path,
        flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )


def _read_regular_file(path: Path) -> FileSnapshot:
    descriptor = _open_no_follow(path, os.O_RDONLY)
    try:
        return _snapshot_from_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _legacy_wrapper(container_dir: Path, command: str, bind_option: str) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        f"{bind_option} $neurodesk_singularity_opts --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")


def _legacy_wrapper_without_display(
    container_dir: Path, command: str, bind_option: str
) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        f"singularity --silent exec {bind_option} $neurodesk_singularity_opts "
        f"--pwd \"$PWD\" {container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")


def _legacy_wrapper_with_duplicate_display(container_dir: Path, command: str) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        "--env DISPLAY=$DISPLAY $neurodesk_singularity_opts --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")


def _legacy_wrapper_with_trailing_bind_slot(container_dir: Path, command: str) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        "$neurodesk_singularity_opts  --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")


def _legacy_wrapper_candidates(container_dir: Path, command: str) -> tuple[bytes, ...]:
    candidates = [
        _legacy_wrapper(container_dir, command, bind_option)
        for bind_option in GENERATED_BIND_OPTIONS
    ]
    candidates.extend(
        _legacy_wrapper_without_display(container_dir, command, bind_option)
        for bind_option in GENERATED_BIND_OPTIONS
    )

    # Wrappers generated before temporary-directory binding was introduced do
    # not contain the empty bind-variable slot (and therefore have one space at
    # the insertion point instead of two).
    candidates.append(
        _legacy_wrapper(container_dir, command, "").replace(
            b"DISPLAY=$DISPLAY  ", b"DISPLAY=$DISPLAY ", 1
        )
    )
    candidates.append(
        _legacy_wrapper_without_display(container_dir, command, "").replace(
            b"singularity --silent exec  ", b"singularity --silent exec ", 1
        )
    )
    candidates.append(_legacy_wrapper_with_duplicate_display(container_dir, command))
    candidates.append(_legacy_wrapper_with_trailing_bind_slot(container_dir, command))
    return tuple(dict.fromkeys(candidates))


def _fixed_wrapper(legacy: bytes) -> bytes:
    pwd_line = b"export PWD=`pwd -P`\n"
    display_argument = b"--env DISPLAY=$DISPLAY "
    replacement = legacy.replace(pwd_line, pwd_line + XAUTHORITY_BLOCK, 1)
    if display_argument in replacement:
        return replacement.replace(
            display_argument,
            display_argument + XAUTHORITY_ARGUMENT,
            1,
        )
    return replacement.replace(
        b"singularity --silent exec ",
        b"singularity --silent exec " + XAUTHORITY_ARGUMENT,
        1,
    )


def _classify_wrapper(
    container_dir: Path, command: str, content: bytes
) -> tuple[WrapperState, bytes | None]:
    if (
        content.startswith(b"#!/usr/bin/env bash\n")
        and DISABLED_NOTICE in content
        and DISABLED_PULL_HINT in content
    ):
        return WrapperState.DISABLED, None

    for legacy in _legacy_wrapper_candidates(container_dir, command):
        if content == legacy:
            return WrapperState.LEGACY, _fixed_wrapper(legacy)
        if content == _fixed_wrapper(legacy):
            return WrapperState.FIXED, None

    return WrapperState.UNKNOWN, None


def _same_snapshot(left: FileSnapshot, right: FileSnapshot) -> bool:
    return left == right


def _container_directories(containers_root: Path) -> tuple[Path, ...]:
    directories: list[Path] = []
    for path in containers_root.iterdir():
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISDIR(mode):
            directories.append(path)
    return tuple(sorted(directories))


def plan_wrapper_reconciliation(repo_root: Path) -> ReconciliationPlan:
    repo_root = repo_root.absolute()
    containers_root = repo_root / "containers"
    rewrites: list[PlannedRewrite] = []
    diagnostics: list[Diagnostic] = []

    try:
        container_dirs = _container_directories(containers_root)
    except OSError as error:
        return ReconciliationPlan(
            rewrites=(),
            diagnostics=(
                Diagnostic(containers_root, f"cannot scan container directories: {error}"),
            ),
        )

    for container_dir in container_dirs:
        commands_path = container_dir / "commands.txt"
        try:
            commands_mode = commands_path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            diagnostics.append(Diagnostic(commands_path, f"cannot inspect inventory: {error}"))
            continue
        if not stat.S_ISREG(commands_mode):
            diagnostics.append(Diagnostic(commands_path, "command inventory is not a regular file"))
            continue

        commands, command_diagnostics = _parse_commands(commands_path)
        diagnostics.extend(command_diagnostics)

        for command in commands:
            wrapper_path = container_dir / command
            try:
                wrapper_mode = wrapper_path.lstat().st_mode
            except FileNotFoundError:
                continue
            except OSError as error:
                diagnostics.append(Diagnostic(wrapper_path, f"cannot inspect wrapper: {error}"))
                continue

            if stat.S_ISLNK(wrapper_mode):
                diagnostics.append(Diagnostic(wrapper_path, "wrapper is a symbolic link"))
                continue
            if not stat.S_ISREG(wrapper_mode) or not wrapper_mode & 0o111:
                continue

            try:
                snapshot = _read_regular_file(wrapper_path)
            except (OSError, RuntimeError) as error:
                diagnostics.append(Diagnostic(wrapper_path, f"cannot read wrapper: {error}"))
                continue

            state, replacement = _classify_wrapper(
                container_dir, command, snapshot.content
            )
            if state is WrapperState.LEGACY:
                if replacement is None:
                    raise AssertionError("legacy wrapper has no replacement")
                rewrites.append(
                    PlannedRewrite(
                        path=wrapper_path,
                        before=snapshot,
                        replacement=replacement,
                    )
                )
            elif state is WrapperState.UNKNOWN:
                detail = (
                    "wrapper has a partial XAUTHORITY edit"
                    if any(marker in snapshot.content for marker in XAUTHORITY_MARKERS)
                    else "executable does not match a generated wrapper"
                )
                diagnostics.append(Diagnostic(wrapper_path, detail))

    return ReconciliationPlan(
        rewrites=tuple(sorted(rewrites, key=lambda rewrite: rewrite.path)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.path, item.message))),
    )


def _verify_rewrite(rewrite: PlannedRewrite) -> None:
    try:
        current = _read_regular_file(rewrite.path)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"cannot recheck {rewrite.path}: {error}") from error
    if not _same_snapshot(current, rewrite.before):
        raise RuntimeError(f"wrapper changed since planning: {rewrite.path}")


def _write_rewrite(rewrite: PlannedRewrite) -> None:
    try:
        descriptor = _open_no_follow(rewrite.path, os.O_RDWR)
    except OSError as error:
        raise RuntimeError(f"cannot open wrapper for writing: {rewrite.path}: {error}") from error

    try:
        current = _snapshot_from_descriptor(descriptor)
        if not _same_snapshot(current, rewrite.before):
            raise RuntimeError(f"wrapper changed since planning: {rewrite.path}")

        os.lseek(descriptor, 0, os.SEEK_SET)
        view = memoryview(rewrite.replacement)
        written = 0
        while written < len(view):
            bytes_written = os.write(descriptor, view[written:])
            if bytes_written == 0:
                raise RuntimeError(f"short write while updating wrapper: {rewrite.path}")
            written += bytes_written
        os.ftruncate(descriptor, len(rewrite.replacement))
        os.fsync(descriptor)
    except OSError as error:
        raise RuntimeError(f"cannot update wrapper: {rewrite.path}: {error}") from error
    finally:
        os.close(descriptor)


def apply_wrapper_plan(plan: ReconciliationPlan) -> int:
    if plan.diagnostics:
        raise ValueError("wrapper reconciliation plan contains errors")

    for rewrite in plan.rewrites:
        _verify_rewrite(rewrite)
    for rewrite in plan.rewrites:
        _write_rewrite(rewrite)
    return len(plan.rewrites)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add XAUTHORITY forwarding to pre-fix generated CVMFS wrappers."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("/cvmfs/neurodesk.ardc.edu.au"),
        help="Mounted Neurodesk CVMFS repository root.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without writing. Exits 1 when safe changes are needed.",
    )
    return parser


def _report(plan: ReconciliationPlan) -> None:
    for rewrite in plan.rewrites:
        print(f"[INFO] add XAUTHORITY forwarding: {rewrite.path}")
    for diagnostic in plan.diagnostics:
        print(f"[ERROR] {diagnostic.message}: {diagnostic.path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = plan_wrapper_reconciliation(args.repo_root)
    _report(plan)

    if plan.diagnostics:
        print(
            f"[ERROR] Wrapper reconciliation found {len(plan.diagnostics)} error(s); "
            "no files changed.",
            file=sys.stderr,
        )
        return 2

    if args.check:
        if plan.rewrites:
            print(
                f"[INFO] Wrapper reconciliation would change {len(plan.rewrites)} file(s)."
            )
            return 1
        print("[INFO] Wrapper reconciliation is already up to date.")
        return 0

    try:
        changed = apply_wrapper_plan(plan)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[ERROR] Wrapper reconciliation failed: {error}", file=sys.stderr)
        return 2

    print(f"[INFO] Wrapper reconciliation changed {changed} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

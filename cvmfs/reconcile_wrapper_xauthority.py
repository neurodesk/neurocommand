#!/usr/bin/env python3
"""Reconcile XAUTHORITY forwarding and NVIDIA defaults in generated CVMFS wrappers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
import sys

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "neurodesk/transparent-singularity")
)
from artifact_renderer import render_wrapper, write_artifact
from wrapper_legacy import classify_relocated_wrapper
from wrapper_legacy import (
    DISABLED_NOTICE,
    DISABLED_PULL_HINT,
    GENERATED_BIND_OPTIONS,
    NVIDIA_BLOCK,
    WRAPPER_SETUP_MARKERS,
    WrapperState,
    XAUTHORITY_ARGUMENT,
    XAUTHORITY_BLOCK,
    _fixed_wrapper,
    _inventory_wrapper,
    _legacy_wrapper,
    _legacy_wrapper_candidates,
    _legacy_wrapper_with_duplicate_display,
    _legacy_wrapper_with_trailing_bind_slot,
    _legacy_wrapper_without_display,
    _xauthority_wrapper,
)


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
    helpers: tuple[tuple[Path, FileSnapshot | None, bytes], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.rewrites and not self.diagnostics and not self.helpers


def _safe_command(raw_command: str) -> bool:
    return bool(raw_command) and not (
        raw_command in {".", ".."}
        or Path(raw_command).is_absolute()
        or "/" in raw_command
        or "\\" in raw_command
        or any(character.isspace() for character in raw_command)
        or "\x00" in raw_command
    )


def _parse_commands(
    commands_path: Path,
) -> tuple[tuple[str, ...], tuple[Diagnostic, ...]]:
    try:
        snapshot = _read_regular_file(commands_path)
        text = snapshot.content.decode("utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError) as error:
        return (), (
            Diagnostic(commands_path, f"cannot read command inventory: {error}"),
        )

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


def _classify_wrapper(
    container_dir: Path, command: str, content: bytes
) -> tuple[WrapperState, bytes | None]:
    if content in (
        render_wrapper(container_dir.name + ".simg", command),
        render_wrapper(container_dir.name + ".simg", command, legacy=True),
    ):
        return WrapperState.FIXED, None
    state, _ = classify_relocated_wrapper(container_dir, command, content)
    if state in {WrapperState.LEGACY, WrapperState.FIXED}:
        return WrapperState.LEGACY, render_wrapper(
            container_dir.name + ".simg", command
        )
    return state, None


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
    helpers = []
    helper_dir = (
        Path(__file__).resolve().parents[1] / "neurodesk/transparent-singularity"
    )

    try:
        container_dirs = _container_directories(containers_root)
    except OSError as error:
        return ReconciliationPlan(
            rewrites=(),
            diagnostics=(
                Diagnostic(
                    containers_root, f"cannot scan container directories: {error}"
                ),
            ),
        )

    for container_dir in container_dirs:
        commands_path = container_dir / "commands.txt"
        try:
            commands_mode = commands_path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            diagnostics.append(
                Diagnostic(commands_path, f"cannot inspect inventory: {error}")
            )
            continue
        if not stat.S_ISREG(commands_mode):
            diagnostics.append(
                Diagnostic(commands_path, "command inventory is not a regular file")
            )
            continue

        commands, command_diagnostics = _parse_commands(commands_path)
        diagnostics.extend(command_diagnostics)

        generated = False
        for command in commands:
            wrapper_path = container_dir / command
            try:
                wrapper_mode = wrapper_path.lstat().st_mode
            except FileNotFoundError:
                continue
            except OSError as error:
                diagnostics.append(
                    Diagnostic(wrapper_path, f"cannot inspect wrapper: {error}")
                )
                continue

            if stat.S_ISLNK(wrapper_mode):
                diagnostics.append(
                    Diagnostic(wrapper_path, "wrapper is a symbolic link")
                )
                continue
            if not stat.S_ISREG(wrapper_mode) or not wrapper_mode & 0o111:
                continue

            try:
                snapshot = _read_regular_file(wrapper_path)
            except (OSError, RuntimeError) as error:
                diagnostics.append(
                    Diagnostic(wrapper_path, f"cannot read wrapper: {error}")
                )
                continue

            state, replacement = _classify_wrapper(
                container_dir, command, snapshot.content
            )
            generated = generated or state in {WrapperState.LEGACY, WrapperState.FIXED}
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
                    "wrapper has an unrecognized XAUTHORITY or NVIDIA setup edit"
                    if any(
                        marker in snapshot.content for marker in WRAPPER_SETUP_MARKERS
                    )
                    else "executable does not match a generated wrapper"
                )
                diagnostics.append(Diagnostic(wrapper_path, detail))

        if generated:
            for name in (
                "container_runtime.sh",
                "artifact_renderer.py",
                "wrapper_legacy.py",
                "ts_render_artifacts.sh",
                "run_transparent_singularity.sh",
            ):
                path = container_dir / name
                expected = (helper_dir / name).read_bytes()
                try:
                    before = (
                        _read_regular_file(path)
                        if path.exists() or path.is_symlink()
                        else None
                    )
                    if before is None or before.content != expected:
                        helpers.append((path, before, expected))
                except (OSError, RuntimeError) as error:
                    diagnostics.append(
                        Diagnostic(path, f"cannot stage deployment helper: {error}")
                    )

    return ReconciliationPlan(
        rewrites=tuple(sorted(rewrites, key=lambda rewrite: rewrite.path)),
        diagnostics=tuple(
            sorted(diagnostics, key=lambda item: (item.path, item.message))
        ),
        helpers=tuple(helpers),
    )


def _verify_rewrite(rewrite: PlannedRewrite) -> None:
    try:
        current = _read_regular_file(rewrite.path)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"cannot recheck {rewrite.path}: {error}") from error
    if not _same_snapshot(current, rewrite.before):
        raise RuntimeError(f"wrapper changed since planning: {rewrite.path}")


def _write_rewrite(rewrite: PlannedRewrite) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=rewrite.path.parent, delete=False
        ) as staged:
            temporary = Path(staged.name)
            staged.write(rewrite.replacement)
            staged.flush()
            os.fsync(staged.fileno())
        os.chmod(temporary, stat.S_IMODE(rewrite.before.mode))
        current = temporary.stat()
        if (current.st_uid, current.st_gid) != (rewrite.before.uid, rewrite.before.gid):
            os.chown(temporary, rewrite.before.uid, rewrite.before.gid)
        _verify_rewrite(rewrite)
        os.replace(temporary, rewrite.path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_wrapper_plan(plan: ReconciliationPlan) -> int:
    """Apply the plan with exclusive access to the deployment files.

    Snapshot checks are not atomic with writes. Failures leave earlier writes applied.
    Return the number of rewritten wrappers and helpers.
    """
    if plan.diagnostics:
        raise ValueError("wrapper reconciliation plan contains errors")

    for rewrite in plan.rewrites:
        _verify_rewrite(rewrite)
    for path, before, _ in plan.helpers:
        current = (
            _read_regular_file(path) if path.exists() or path.is_symlink() else None
        )
        if current != before:
            raise RuntimeError(f"Helper changed since planning: {path}")
    for path, before, content in plan.helpers:
        current = (
            _read_regular_file(path) if path.exists() or path.is_symlink() else None
        )
        if current != before:
            raise RuntimeError(f"Helper changed since planning: {path}")
        write_artifact(path, content, path.suffix == ".sh")
    for rewrite in plan.rewrites:
        _write_rewrite(rewrite)
    return len(plan.rewrites) + len(plan.helpers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile XAUTHORITY forwarding and NVIDIA defaults in generated CVMFS wrappers."
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
    for path, _, _ in plan.helpers:
        print(f"[INFO] stage deployment helper: {path}")
    for rewrite in plan.rewrites:
        print(
            f"[INFO] update XAUTHORITY forwarding and NVIDIA defaults: {rewrite.path}"
        )
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
        if plan.rewrites or plan.helpers:
            print(
                f"[INFO] Wrapper reconciliation would change {len(plan.rewrites) + len(plan.helpers)} file(s)."
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

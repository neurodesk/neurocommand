#!/usr/bin/env python3
"""Publish a CVMFS startup file bundle for FSL 6.0.7.23."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "neurodesk/transparent-singularity")
)
from artifact_renderer import (
    CVMFS_STARTUP_MODULE,
    CVMFS_STARTUP_TRIGGER,
    read_container_inventory,
    write_artifact,
)
from cvmfs.reconcile_module_files import parse_log


MAX_BUNDLE_BYTES = 1024 * 1024 * 1024


def profiling_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("PATH", os.defpath)
    required = ("xvfb-run", "Xvfb", "xauth", "xwininfo")
    if "NEURODESK_PREFETCH_TOOLS" in environment or not all(
        shutil.which(name, path=environment["PATH"]) for name in required
    ):
        prefix = Path(
            environment.get("NEURODESK_PREFETCH_TOOLS", "/opt/neurodesk-prefetch")
        )
        environment["PATH"] = str(prefix / "bin") + os.pathsep + environment["PATH"]
    missing = [
        name for name in required if not shutil.which(name, path=environment["PATH"])
    ]
    if missing:
        raise ValueError(
            "Missing FSL profiling tools: "
            + ", ".join(missing)
            + "; run sudo bash cvmfs/install_prefetch_tools.sh on the publisher"
        )
    return environment


def image_path(image: Path, path: str | Path) -> Path:
    """Resolve container symlinks without following absolute links onto the host."""
    pending = deque(PurePosixPath(path).parts)
    resolved: list[str] = []
    links = 0
    while pending:
        part = pending.popleft()
        if part in {"/", "."}:
            continue
        if part == "..":
            if not resolved:
                raise ValueError(f"Path escapes image: {path}")
            resolved.pop()
            continue
        candidate = image.joinpath(*resolved, part)
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise ValueError(f"Symlink loop in image: {path}")
            target = candidate.readlink()
            if target.is_absolute():
                resolved.clear()
            pending.extendleft(reversed(target.parts))
        else:
            resolved.append(part)
    return image.joinpath(*resolved)


def trace_dependencies(image: Path, traces: Path) -> set[Path]:
    dependencies = set()
    for command in ("fsl", "fsleyes"):
        trace = traces / (command + ".trace")
        opened = set()
        for line in trace.read_text().splitlines():
            # -yy annotates successful descriptors with their resolved path.
            match = re.search(r"= \d+<(/[^>]+)>", line) or re.search(
                r'execve\("(/[^\"]+)".*\)\s+= 0', line
            )
            if not match:
                continue
            value = match[1]
            if value.startswith(str(image) + "/"):
                value = value[len(str(image)) :]
            if any(
                part
                in {
                    "dev",
                    "proc",
                    "sys",
                    "tmp",
                    "run",
                    "home",
                    "root",
                    "neurodesk-prefetch-profile",
                }
                for part in PurePosixPath(value).parts[1:2]
            ):
                continue
            path = image_path(image, value)
            if path.is_file():
                opened.add(path)
        if not opened:
            raise ValueError(f"No image files recorded during {command} startup")
        dependencies.update(opened)
    return dependencies


def stop_process(process: subprocess.Popen, *, grace: int = 5) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def record_guis(image: Path, traces: Path) -> None:
    """Run under xvfb-run and require both application windows to appear."""
    runtime = (
        os.environ.get("NEURODESK_CONTAINER_RUNTIME")
        or shutil.which("apptainer")
        or shutil.which("singularity")
    )
    if not runtime:
        raise ValueError("Install Apptainer or Singularity to profile FSL startup")
    authority = traces / "Xauthority"
    shutil.copyfile(os.environ["XAUTHORITY"], authority)
    for command in ("fsl", "fsleyes"):
        with (traces / (command + ".log")).open("w+") as output:
            process = subprocess.Popen(
                [
                    runtime,
                    "exec",
                    "--cleanenv",
                    "--containall",
                    "--bind",
                    f"{traces}:/neurodesk-prefetch-profile",
                    "--bind",
                    "/tmp/.X11-unix:/tmp/.X11-unix:ro",
                    "--env",
                    "DISPLAY=" + os.environ["DISPLAY"],
                    "--env",
                    "XAUTHORITY=/neurodesk-prefetch-profile/Xauthority",
                    "--env",
                    "LIBGL_ALWAYS_SOFTWARE=1",
                    str(image),
                    "strace",
                    "-f",
                    "-qq",
                    "-yy",
                    "-s",
                    "4096",
                    "-e",
                    "trace=open,openat,openat2,execve",
                    "-o",
                    f"/neurodesk-prefetch-profile/{command}.trace",
                    command,
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    windows = subprocess.run(
                        ["xwininfo", "-root", "-tree"],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    ).stdout
                    title = "FSLeyes" if command == "fsleyes" else "FSL"
                    names = re.findall(
                        r'^\s+0x[0-9a-f]+\s+"([^\"]*)"',
                        windows,
                        re.MULTILINE | re.IGNORECASE,
                    )
                    if any(
                        re.match(title + r"(?:$|[ :\-])", name, re.IGNORECASE)
                        and not re.search(
                            r"\b(error|warning|failed)\b", name, re.IGNORECASE
                        )
                        for name in names
                    ):
                        # Allow the initial canvas and widgets to finish loading.
                        time.sleep(2)
                        if process.poll() is None:
                            break
                    if process.poll() is not None:
                        break
                    time.sleep(0.25)
                else:
                    output.seek(0)
                    raise ValueError(
                        f"Timed out waiting for the {command} window: {output.read()[-4000:]}"
                    )
                if process.poll() is not None:
                    output.seek(0)
                    raise ValueError(
                        f"{command} exited before GUI startup: {output.read()[-4000:]}"
                    )
            finally:
                stop_process(process)


def capture_startup(image: Path) -> set[Path]:
    environment = profiling_environment()
    with tempfile.TemporaryDirectory(prefix="neurodesk-fsl-profile-") as temporary:
        traces = Path(temporary)
        process = subprocess.Popen(
            [
                "xvfb-run",
                "--auto-servernum",
                "--error-file=" + str(traces / "xvfb.log"),
                # Window probes disconnect before the GUI connects; keep Xvfb
                # from resetting during those gaps and rejecting new clients.
                "--server-args=-screen 0 1280x1024x24 +extension GLX -noreset",
                sys.executable,
                str(Path(__file__).resolve()),
                "--record-guis",
                str(image),
                str(traces),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=environment,
        )
        try:
            stdout, stderr = process.communicate(timeout=120)
            if process.returncode:
                display_log = traces / "xvfb.log"
                display_error = (
                    display_log.read_text()[-1000:] if display_log.exists() else ""
                )
                raise ValueError(
                    f"FSL GUI profiling failed: {(stdout + stderr)[-4000:]}\n{display_error}"
                )
        finally:
            stop_process(process, grace=10)
        return trace_dependencies(image, traces)


def bundle_dependencies(repo: Path, directory: Path, traces: Path | None) -> list[str]:
    spec = read_container_inventory(directory)
    image = directory / spec.image_basename
    if not image.is_dir() or image.is_symlink():
        raise ValueError(f"Startup bundles require an unpacked image: {image}")
    dependencies = (
        trace_dependencies(image, traces) if traces else capture_startup(image)
    )
    dependencies.update(
        directory / name for name in ("container_runtime.sh", "fsl", "fsleyes")
    )
    # The kernel opens script interpreters and ELF loaders during execve.
    for path in tuple(dependencies):
        with path.open("rb") as source:
            header = source.readline(4096)
        if header.startswith(b"#!"):
            interpreter = header[2:].decode().split()[0]
            dependencies.add(image_path(image, interpreter))
    for name in (
        "lib64/ld-linux-x86-64.so.2",
        "lib/ld-linux-aarch64.so.1",
        ".singularity.d/actions/exec",
    ):
        path = image_path(image, name)
        if path.is_file():
            dependencies.add(path)
    # Apptainer reads these before the traced application starts.
    environment = image_path(image, ".singularity.d/env")
    dependencies.update(
        image_path(image, path.relative_to(image)) for path in environment.glob("*.sh")
    )
    return validate_dependencies(repo, directory, dependencies)


def validate_dependencies(
    repo: Path, directory: Path, dependencies: set[Path]
) -> list[str]:
    for path in dependencies:
        path.relative_to(directory)
        if not stat.S_ISREG(path.stat().st_mode) or path.resolve() != path:
            raise ValueError(f"Bundle dependency is not a regular file: {path}")
    total = sum(path.stat().st_size for path in dependencies)
    if total > MAX_BUNDLE_BYTES:
        raise ValueError(f"Startup bundle exceeds {MAX_BUNDLE_BYTES} bytes: {total}")
    return sorted("/" + path.relative_to(repo).as_posix() for path in dependencies)


def publish(
    repo: Path,
    log: Path,
    *,
    check: bool = False,
    refresh: bool = False,
    traces: Path | None = None,
) -> bool:
    entries = [
        entry
        for entry in parse_log(log)
        if (entry.tool, entry.version)
        == (CVMFS_STARTUP_MODULE.name, CVMFS_STARTUP_MODULE.version)
    ]
    if not entries:
        return False
    entry = max(entries, key=lambda entry: entry.builddate)
    directory = repo / "containers" / entry.image
    if not (directory / "commands.txt").is_file():
        print(
            f"[WARN] Startup bundle deferred until {entry.image} is installed",
            file=sys.stderr,
        )
        return False
    trigger = directory / CVMFS_STARTUP_TRIGGER
    manifest = directory / (".cvmfsbundle-" + CVMFS_STARTUP_TRIGGER)
    content = f"neurodesk-fsl-gui-startup-v1\n{entry.image}.simg\n".encode()
    for path in (trigger, manifest):
        if path.is_symlink():
            raise ValueError(f"Refusing to replace a symlink: {path}")
    schema = {"name": "CVMFS_BUNDLE", "version": "1.0.0", "encoding": "UTF-8"}
    if (
        not refresh
        and trigger.exists()
        and manifest.exists()
        and trigger.read_bytes() == content
    ):
        try:
            previous = json.loads(manifest.read_text())
            values = previous["dependencies"]
            if not isinstance(values, list) or not values:
                raise ValueError("Empty startup bundle")
            if any(
                not isinstance(value, str)
                or not value.startswith("/")
                or ".." in PurePosixPath(value).parts
                for value in values
            ):
                raise ValueError("Invalid repository path in startup bundle")
            paths = {repo / value.lstrip("/") for value in values}
            expected = {
                **schema,
                "dependencies": validate_dependencies(repo, directory, paths),
            }
            required = {
                "/" + (directory / name).relative_to(repo).as_posix()
                for name in ("fsl", "fsleyes", "container_runtime.sh")
            }
            if previous == expected and required.issubset(values):
                return False
        except (ValueError, KeyError, TypeError, OSError):
            pass
    if check:
        return True
    dependencies = bundle_dependencies(repo, directory, traces)
    write_artifact(
        manifest,
        (
            json.dumps({**schema, "dependencies": dependencies}, indent=2) + "\n"
        ).encode(),
    )
    write_artifact(trigger, content)
    print(f"[INFO] Published FSL GUI startup bundle with {len(dependencies)} files")
    return True


def main() -> int:
    if sys.argv[1:2] == ["--record-guis"]:

        def interrupted(signum, frame):
            raise SystemExit(2)

        signal.signal(signal.SIGTERM, interrupted)
        try:
            record_guis(Path(sys.argv[2]), Path(sys.argv[3]))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(error, file=sys.stderr)
            return 2
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--refresh", action="store_true", help="Record GUI startup again for this build"
    )
    parser.add_argument(
        "--traces",
        type=Path,
        help="Use existing strace -yy GUI traces instead of launching the GUIs",
    )
    args = parser.parse_args()
    try:
        drift = publish(
            args.repo_root.resolve(),
            args.log,
            check=args.check,
            refresh=args.refresh,
            traces=args.traces,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"[ERROR] Cannot publish startup bundle: {error}", file=sys.stderr)
        return 2
    return int(args.check and drift)


if __name__ == "__main__":
    raise SystemExit(main())

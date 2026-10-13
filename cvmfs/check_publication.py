#!/usr/bin/env python3
"""Check candidate inventory against a fresh, signature-verified CVMFS view."""

import argparse
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = "neurodesk.ardc.edu.au"
ORIGIN = f"http://stratum0.neurodesk.cloud.edu.au/cvmfs/{REPOSITORY}"
PUBLIC_KEY = Path(__file__).with_name(f"{REPOSITORY}.pub")
MAX_LOG_BYTES = 4 * 1024 * 1024
PROBE_SECONDS = 180


class PublicationUnavailable(Exception):
    """The public committed inventory could not be established."""


@dataclass(frozen=True)
class PublishedInventory:
    revision: str
    root_hash: str
    missing: tuple[str, ...]


def inventory_ids(text: str) -> frozenset[str]:
    if not text.strip() or len(text.encode()) > MAX_LOG_BYTES:
        raise PublicationUnavailable("Inventory is empty or too large")
    ids = set()
    for line in text.splitlines():
        fields = line.split(maxsplit=1)
        if (
            len(fields) != 2
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,254}", fields[0])
            or not fields[1].startswith("categories:")
        ):
            raise PublicationUnavailable(f"Invalid inventory entry: {line[:256]!r}")
        ids.add(fields[0])
    return frozenset(ids)


def _inspect_snapshot(root: Path, ids: frozenset[str]) -> tuple[str, ...]:
    missing = []
    if not stat.S_ISDIR((root / "containers").lstat().st_mode):
        raise PublicationUnavailable("Public containers path is not a directory")
    for name in sorted(ids):
        container = root / "containers" / name
        try:
            if not stat.S_ISDIR(container.lstat().st_mode) or not stat.S_ISREG(
                (container / "commands.txt").lstat().st_mode
            ):
                missing.append(name)
        except FileNotFoundError:
            missing.append(name)
    return tuple(missing)


def _identity(root: Path) -> tuple[str, str]:
    revision = os.getxattr(root, "user.revision").decode("ascii")
    root_hash = os.getxattr(root, "user.root_hash").decode("ascii")
    if not revision.isdecimal() or not re.fullmatch(r"[a-f0-9]{40,64}", root_hash):
        raise PublicationUnavailable("Invalid CVMFS snapshot identity")
    if os.getxattr(root, "user.fqrn").decode("ascii") != REPOSITORY:
        raise PublicationUnavailable("Unexpected CVMFS repository")
    if os.getxattr(root, "user.host").decode("ascii") != ORIGIN:
        raise PublicationUnavailable("Unexpected CVMFS origin")
    return revision, root_hash


@contextmanager
def _workspace():
    scratch = Path(tempfile.mkdtemp(prefix="cvmfs-publication-"))
    try:
        yield scratch
    finally:
        if os.path.ismount(scratch / "mount"):
            raise PublicationUnavailable(
                f"Client mount cleanup failed; retained scratch at {scratch}"
            )
        shutil.rmtree(scratch)


def inspect_publication(log_path: Path) -> PublishedInventory:
    with log_path.open("rb") as source:
        ids = inventory_ids(source.read(MAX_LOG_BYTES + 1).decode("utf-8"))
    with _workspace() as scratch:
        root = scratch / "mount"
        cache = scratch / "cache"
        root.mkdir()
        cache.mkdir()
        config = scratch / "client.conf"
        settings = {
            "CVMFS_SERVER_URL": ORIGIN,
            "CVMFS_HTTP_PROXY": "DIRECT",
            "CVMFS_PUBLIC_KEY": str(PUBLIC_KEY.resolve()),
            "CVMFS_CACHE_BASE": str(cache),
            "CVMFS_WORKSPACE": str(cache),
            "CVMFS_RELOAD_SOCKETS": str(scratch),
            "CVMFS_SHARED_CACHE": "no",
            "CVMFS_CLIENT_PROFILE": "single",
            "CVMFS_USE_GEOAPI": "no",
            "CVMFS_AUTO_UPDATE": "no",
            "CVMFS_IGNORE_SIGNATURE": "no",
            "CVMFS_TIMEOUT": "10",
            "CVMFS_TIMEOUT_DIRECT": "10",
            "CVMFS_MAX_RETRIES": "1",
            "CVMFS_QUOTA_LIMIT": "512",
        }
        config.write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in settings.items())
        )
        process = None

        def timeout(_signal, _frame):
            raise PublicationUnavailable("CVMFS publication probe timed out")

        def cancelled(_signal, _frame):
            raise PublicationUnavailable("CVMFS publication probe cancelled")

        previous_handlers = {
            signum: signal.signal(signum, handler)
            for signum, handler in (
                (signal.SIGALRM, timeout),
                (signal.SIGTERM, cancelled),
                (signal.SIGINT, cancelled),
            )
        }
        signal.alarm(PROBE_SECONDS)
        try:
            with (scratch / "client.log").open("wb") as output:
                process = subprocess.Popen(
                    ["cvmfs2", "-f", "-o", f"config={config}", REPOSITORY, str(root)],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                while not os.path.ismount(root):
                    if process.poll() is not None:
                        raise PublicationUnavailable(
                            f"CVMFS client failed (exit {process.returncode})"
                        )
                    time.sleep(0.1)
                before = _identity(root)
                missing = _inspect_snapshot(root, ids)
                if before != _identity(root):
                    raise PublicationUnavailable(
                        "CVMFS snapshot changed during inspection"
                    )
                return PublishedInventory(*before, missing)
        finally:
            signal.alarm(0)
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            try:
                if os.path.ismount(root):
                    subprocess.run(
                        ["fusermount", "-u", str(root)],
                        check=True,
                        timeout=15,
                        capture_output=True,
                    )
            finally:
                if process is not None:
                    if process.poll() is None:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            with suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_publication(args.log)
    except (
        PublicationUnavailable,
        OSError,
        UnicodeError,
        subprocess.SubprocessError,
    ) as error:
        report = f"[ERROR] Cannot establish public CVMFS readiness: {error}\n"
        status = 2
    else:
        report = (
            f"[INFO] Public CVMFS revision {result.revision}, root {result.root_hash}\n"
        )
        report += "".join(
            f"[PENDING] Missing public container: {name}\n" for name in result.missing
        )
        status = 3 if result.missing else 0
    print(report, end="")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as output:
            output.write(report + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

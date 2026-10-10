#!/usr/bin/env python3
"""Run repository checks with the current Python environment."""

import argparse
from pathlib import Path
import subprocess
import sys


def run(command, root):
    result = subprocess.run(command, cwd=root)
    if result.returncode:
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "check", choices=["quality", "tests", "all"], default="all", nargs="?"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.check in {"quality", "all"}:
        for command in (
            [sys.executable, "-m", "ruff", "check", "."],
            [sys.executable, "-m", "ruff", "format", "--check", "."],
        ):
            run(command, root)
        sources = (
            subprocess.check_output(["git", "ls-files", "-z", "*.sh"], cwd=root)
            .decode()
            .split("\0")
        )
        run(["shellcheck", *filter(None, sources)], root)
        run(["actionlint"], root)
        run([sys.executable, "-m", "codespell_lib"], root)
    if args.check in {"tests", "all"}:
        run([sys.executable, "-m", "pytest", "-q", "-rs"], root)


if __name__ == "__main__":
    main()

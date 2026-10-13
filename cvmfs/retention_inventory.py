#!/usr/bin/env python3
"""Read retirement protection from committed public and desired inventories."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cvmfs.check_publication import PublicationUnavailable, inventory_ids  # noqa: E402
from cvmfs.generate_metadata import log_lines  # noqa: E402


def retained_ids(repo: Path, revision: str) -> frozenset[str]:
    def committed(path):
        return subprocess.run(
            ["git", "-C", str(repo), "show", f"{revision}:{path}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    public = inventory_ids(committed("cvmfs/log.txt"))
    desired = inventory_ids(
        "\n".join(log_lines(json.loads(committed("neurodesk/apps.json"))))
    )
    return public | desired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        retained = retained_ids(args.repo, args.revision)
    except (
        PublicationUnavailable,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(
            f"[ERROR] Cannot establish retirement protection: {error}", file=sys.stderr
        )
        return 2
    print("\n".join(sorted(retained)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

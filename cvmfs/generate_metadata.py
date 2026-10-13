#!/usr/bin/env python3
"""Generate the published container inventory from neurodesk/apps.json.

cvmfs/log.txt lists one ``<container>_<version>_<builddate> categories:...``
entry per primary container app. cvmfs/applist.json is derived from it for the
website. The upload workflow publishes both files only after every listed image
is available in object storage; other tools use the same generator to compute
the inventory that apps.json currently describes.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmfs.json_gen import process_text_to_json  # noqa: E402


def is_primary_container_app(menu_name: str, app_name: str) -> bool:
    """Return whether an app entry represents the container itself.

    GUI sub-apps conventionally use ``<label>-<container> <version>``. Concrete
    container names may themselves contain hyphens, so a dash alone cannot
    distinguish a sub-app from a primary entry.
    """
    if "-" not in app_name:
        return True
    container_and_version = app_name.rsplit(" ", 1)
    return len(container_and_version) == 2 and container_and_version[0] == menu_name


def log_lines(menu_entries: dict) -> List[str]:
    lines = []
    for menu_name, menu_data in menu_entries.items():
        categories = "".join(
            f"{category}," for category in menu_data.get("categories") or []
        )
        for app_name, app_data in menu_data.get("apps", {}).items():
            if not is_primary_container_app(menu_name, app_name):
                continue
            entry = f"{app_name.replace(' ', '_')}_{app_data['version']}"
            lines.append(f"{entry} categories:{categories}")
    return lines


def write_log(apps_json: Path, log_path: Path) -> List[str]:
    lines = log_lines(json.loads(apps_json.read_text(encoding="utf-8")))
    log_path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return lines


def write_metadata(
    apps_json: Path, log_path: Path, applist_path: Optional[Path] = None
) -> List[str]:
    lines = write_log(apps_json, log_path)
    if applist_path is not None:
        process_text_to_json(
            log_path=log_path, output_path=applist_path, apps_json_path=apps_json
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apps-json", type=Path, default=ROOT / "neurodesk" / "apps.json"
    )
    parser.add_argument("--log", type=Path, default=ROOT / "cvmfs" / "log.txt")
    parser.add_argument(
        "--applist",
        type=Path,
        help="Also write the website applist derived from the log.",
    )
    args = parser.parse_args()
    write_metadata(args.apps_json, args.log, args.applist)


if __name__ == "__main__":
    main()

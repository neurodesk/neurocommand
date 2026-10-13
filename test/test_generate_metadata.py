import json
import subprocess
import sys
from pathlib import Path

from cvmfs.generate_metadata import is_primary_container_app

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "cvmfs" / "generate_metadata.py"


def test_hyphenated_named_variant_is_a_primary_container_app():
    assert is_primary_container_app(
        "neurodesktop-lite_arm64", "neurodesktop-lite_arm64 20260428"
    )


def test_gui_sub_app_is_not_a_primary_container_app():
    assert not is_primary_container_app("fsl", "fsleyesGUI-fsl 6.0.7.22")


def _apps_json(tmp_path):
    apps_json = tmp_path / "apps.json"
    apps_json.write_text(
        json.dumps(
            {
                "fsl": {
                    "apps": {
                        "fsl 6.0.7.22": {"version": "20261010"},
                        "fsleyesGUI-fsl 6.0.7.22": {"version": "20261010"},
                    },
                    "categories": ["functional imaging", "structural imaging"],
                },
                "hidden": {
                    "show_in_applist": False,
                    "apps": {"hidden 1.0": {"version": "20261011"}},
                },
            }
        )
    )
    return apps_json


def test_cli_replaces_existing_log_and_writes_applist(tmp_path):
    log = tmp_path / "log.txt"
    applist = tmp_path / "applist.json"
    log.write_text("stale_1.0_20200101 categories:old,\n")

    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--apps-json",
            str(_apps_json(tmp_path)),
            "--log",
            str(log),
            "--applist",
            str(applist),
        ],
        check=True,
        cwd=tmp_path,
    )

    assert log.read_text() == (
        "fsl_6.0.7.22_20261010 categories:functional imaging,structural imaging,\n"
        "hidden_1.0_20261011 categories:\n"
    )
    assert json.loads(applist.read_text()) == {
        "list": [
            {
                "application": "fsl_6.0.7.22_20261010",
                "categories": ["functional imaging", "structural imaging"],
            }
        ]
    }


def test_checked_in_log_entries_are_generated_format():
    log = ROOT / "cvmfs" / "log.txt"
    for line in log.read_text().splitlines():
        entry, _, categories = line.partition(" ")
        assert entry and "[" not in line and line == line.strip()
        assert categories.startswith("categories:")

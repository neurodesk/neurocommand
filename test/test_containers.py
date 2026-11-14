import json
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from test.support.shell import clean_env, write_executable

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("mne", "1.13.2"),
        ("mne_arm64", "1.13.2"),
        ("kul-vbg", "0.61.0_23112021_beta"),
        ("mne_arm64", "0.61.0_23112021_beta"),
        ("spinalcordtoolbox_gpu", "7.3.4"),
        ("spinalcordtoolbox_lite", "7.3.4"),
        ("retired_tool", "1.2.3_20260101_rc1"),
        ("retired_tool", "v1.2.3"),
    ],
)
@pytest.mark.parametrize("pattern_kind", ["all", "matching", "nonmatching"])
@pytest.mark.parametrize("install", [False, True], ids=["list", "install"])
@pytest.mark.parametrize("install_dir", ["installed", "installed tools"])
@pytest.mark.parametrize("catalogued", [True, False], ids=["catalogued", "removed"])
def test_containers_inventory(
    tmp_path, name, version, pattern_kind, install, install_dir, catalogued
):
    shutil.copy(ROOT / "containers.sh", tmp_path)
    (tmp_path / "neurodesk").mkdir()
    shutil.copy(ROOT / "neurodesk/configparser.sh", tmp_path / "neurodesk")
    catalog = {
        "mne": {},
        "mne_arm64": {},
        "kul-vbg": {},
        "spinalcordtoolbox": {},
        "spinalcordtoolbox_gpu": {},
        "spinalcordtoolbox_lite": {},
    }
    if catalogued:
        catalog.setdefault(name, {})
    else:
        catalog.pop(name, None)
    (tmp_path / "neurodesk/apps.json").write_text(json.dumps(catalog))
    destination = tmp_path / install_dir
    destination.mkdir()
    (tmp_path / "config.ini").write_text(f"installdir={destination}\n")
    (tmp_path / "cvmfs").mkdir()
    (tmp_path / "cvmfs/log.txt").write_text(
        f"{name}_{version}_20261014 categories:electrophysiology,image reconstruction,\n"
    )
    calls = tmp_path / "calls.log"
    write_executable(
        destination / "fetch_containers.sh",
        '#!/bin/bash\nprintf "%s\\0" "$0" "$@" >> "$CALLS"\n',
    )
    pattern = {"all": "all", "matching": name, "nonmatching": "absent"}[pattern_kind]

    result = subprocess.run(
        ["bash", "containers.sh", f"--{pattern}" if install else pattern],
        cwd=tmp_path,
        # arm64 hosts list both kinds of build
        env=clean_env(CALLS=str(calls), NEURODESK_ARCH="aarch64"),
        capture_output=True,
        text=True,
        check=True,
    )

    rows = [line for line in result.stdout.splitlines() if line.endswith(" | Run:")]
    matches = pattern_kind != "nonmatching"
    assert rows == (
        [
            f"| {name} | {version} | 20261014 | "
            "electrophysiology,image reconstruction | Run:"
        ]
        if matches
        else []
    )
    if matches:
        commands = [
            line for line in result.stdout.splitlines() if "fetch_containers.sh" in line
        ]
        assert len(commands) == 1
        assert shlex.split(commands[0]) == [
            str(destination / "fetch_containers.sh"),
            name,
            version,
            "20261014",
        ]
    if install and matches:
        assert calls.exists(), result.stdout + result.stderr
        assert calls.read_bytes().split(b"\0") == [
            str(destination / "fetch_containers.sh").encode(),
            name.encode(),
            version.encode(),
            b"20261014",
            b"",
        ]
        assert f"| SUCCESS | {name} {version} 20261014 |" in result.stdout
    else:
        assert not calls.exists()


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", ["| ants | 2.6.0 |", "| ants | 2.6.5 |"]),
        (
            "aarch64",
            ["| ants | 2.6.0 |", "| ants | 2.6.5 |", "| ants_arm64 | 2.6.5 |"],
        ),
    ],
)
@pytest.mark.parametrize("catalogued", [True, False], ids=["catalogued", "removed"])
def test_containers_lists_the_builds_the_host_runs(
    tmp_path, machine, expected, catalogued
):
    shutil.copy(ROOT / "containers.sh", tmp_path)
    (tmp_path / "neurodesk").mkdir()
    shutil.copy(ROOT / "neurodesk/configparser.sh", tmp_path / "neurodesk")
    catalog = {"ants": {}}
    if catalogued:
        catalog["ants_arm64"] = {}
    (tmp_path / "neurodesk/apps.json").write_text(json.dumps(catalog))
    destination = tmp_path / "installed"
    destination.mkdir()
    (tmp_path / "config.ini").write_text(f"installdir={destination}\n")
    (tmp_path / "cvmfs").mkdir()
    (tmp_path / "cvmfs/log.txt").write_text(
        "ants_2.6.0_20250424 categories:structural imaging,\n"
        "ants_2.6.5_20261012 categories:structural imaging,\n"
        "ants_arm64_2.6.5_20261012 categories:structural imaging,\n"
    )
    calls = tmp_path / "calls.log"
    write_executable(
        destination / "fetch_containers.sh",
        '#!/bin/bash\nprintf "%s\\n" "$1" >> "$CALLS"\n',
    )

    result = subprocess.run(
        ["bash", "containers.sh", "--all"],
        cwd=tmp_path,
        env=clean_env(CALLS=str(calls), NEURODESK_ARCH=machine),
        capture_output=True,
        text=True,
        check=True,
    )

    rows = [line for line in result.stdout.splitlines() if line.endswith(" | Run:")]
    assert [row[: row.index(" | 20")] + " |" for row in rows] == expected
    assert calls.read_text().split() == [row.split()[1] for row in expected]

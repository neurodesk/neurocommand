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
    ],
)
@pytest.mark.parametrize("pattern_kind", ["all", "matching", "nonmatching"])
@pytest.mark.parametrize("install", [False, True], ids=["list", "install"])
@pytest.mark.parametrize("install_dir", ["installed", "installed tools"])
def test_containers_inventory(
    tmp_path, name, version, pattern_kind, install, install_dir
):
    shutil.copy(ROOT / "containers.sh", tmp_path)
    (tmp_path / "neurodesk").mkdir()
    shutil.copy(ROOT / "neurodesk/configparser.sh", tmp_path / "neurodesk")
    shutil.copy(ROOT / "neurodesk/apps.json", tmp_path / "neurodesk")
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
        env=clean_env(CALLS=str(calls)),
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

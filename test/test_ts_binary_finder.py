import os
import shutil
import subprocess

from test.support.paths import TRANSPARENT_SINGULARITY


def find_commands(tmp_path, deploy_path="", deploy_bins=""):
    for name in ("ts_binaryFinder.sh", "ts_binaryFinderExcludes.txt"):
        shutil.copy(TRANSPARENT_SINGULARITY / name, tmp_path / name)
    env = {**os.environ, "DEPLOY_PATH": deploy_path, "DEPLOY_BINS": deploy_bins}
    result = subprocess.run(
        ["bash", "ts_binaryFinder.sh"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "commands.txt").read_text().splitlines()


def test_declared_bins_survive_system_command_excludes(tmp_path):
    # condaenvs deploys only conda, which is also an excluded system command.
    assert find_commands(tmp_path, deploy_bins="conda") == ["conda"]


def test_excludes_still_filter_deploy_path_discoveries(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("conda", "mytool"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)

    assert find_commands(tmp_path, deploy_path=str(bin_dir), deploy_bins="other") == [
        "mytool",
        "other",
    ]

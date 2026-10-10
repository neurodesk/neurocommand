from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

"""Exercise the build command's argument boundaries with a spaced install path."""


ROOT = Path(__file__).resolve().parents[1]


def test_cli_build_preserves_separate_options_and_spaced_install_path(tmp_path):
    repo = tmp_path / "repository with spaces"
    (repo / "neurodesk").mkdir(parents=True)
    for relative in ("build.sh", "neurodesk/configparser.sh"):
        shutil.copy2(ROOT / relative, repo / relative)
    (repo / "local").mkdir()
    (repo / "config.ini").write_text("[neurodesk]\ndeskenv=cli\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output = tmp_path / "arguments.json"
    stub = bin_dir / "python3"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['BUILD_ARGUMENTS'], 'w') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n"
    )
    stub.chmod(0o755)
    result = subprocess.run(
        ["bash", "build.sh", "--cli"],
        cwd=repo,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "BUILD_ARGUMENTS": str(output)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text()) == [
        "-m", "neurodesk", f"--installdir={repo / 'local'}", "--deskenv=cli"
    ]

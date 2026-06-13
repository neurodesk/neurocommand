import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command, *, cwd, env=None):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def make_update_fixture(tmp_path):
    seed = tmp_path / "seed"
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    upstream = tmp_path / "upstream"

    (seed / "neurodesk").mkdir(parents=True)
    for relative_path in (
        "build.sh",
        "neurodesk/configparser.sh",
        "neurodesk/fetch_and_run.sh",
    ):
        source = ROOT / relative_path
        destination = seed / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    run(["git", "init", "--quiet", "--initial-branch=main"], cwd=seed)
    run(["git", "add", "."], cwd=seed)
    run(
        [
            "git",
            "-c",
            "user.name=Neurocommand Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "seed update fixture",
        ],
        cwd=seed,
    )

    run(["git", "clone", "--quiet", "--bare", str(seed), str(origin)], cwd=tmp_path)
    run(["git", "clone", "--quiet", str(origin), str(work)], cwd=tmp_path)
    run(["git", "clone", "--quiet", str(origin), str(upstream)], cwd=tmp_path)

    return origin, work, upstream


def push_upstream_fetch_and_run_update(upstream):
    upstream_script = upstream / "neurodesk" / "fetch_and_run.sh"
    upstream_script.write_text(upstream_script.read_text() + "\n# remote update regression\n")
    run(["git", "add", "neurodesk/fetch_and_run.sh"], cwd=upstream)
    run(
        [
            "git",
            "-c",
            "user.name=Neurocommand Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "remote update regression",
        ],
        cwd=upstream,
    )
    run(["git", "push", "--quiet", "origin", "HEAD:main"], cwd=upstream)


def dirty_local_fetch_and_run(work):
    local_script = work / "neurodesk" / "fetch_and_run.sh"
    local_script.write_text(
        local_script.read_text().replace(
            'export CONTAINER_PATH="${LOCAL_CONTAINERS_PATH}"',
            'export CONTAINER_PATH="${LOCAL_CONTAINERS_PATH}"\n# local dirty regression',
            1,
        )
    )
    return local_script


def python_noop_env(tmp_path):
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    python_stub = stub_bin / "python3"
    python_stub.write_text("#!/bin/sh\nexit 0\n")
    python_stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    return env


def test_update_autostashes_dirty_tracked_files(tmp_path):
    _, work, upstream = make_update_fixture(tmp_path)
    push_upstream_fetch_and_run_update(upstream)
    local_script = dirty_local_fetch_and_run(work)
    env = python_noop_env(tmp_path)

    result = subprocess.run(
        ["bash", "build.sh", "--update", "--cli"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert run(["git", "rev-parse", "HEAD"], cwd=work).stdout == run(
        ["git", "rev-parse", "origin/main"], cwd=work
    ).stdout
    assert "# local dirty regression" in local_script.read_text()


def test_update_handles_detached_head_checkout(tmp_path):
    _, work, upstream = make_update_fixture(tmp_path)
    push_upstream_fetch_and_run_update(upstream)
    local_script = dirty_local_fetch_and_run(work)
    run(["git", "checkout", "--quiet", "--detach", "HEAD"], cwd=work)
    env = python_noop_env(tmp_path)

    result = subprocess.run(
        ["bash", "build.sh", "--update", "--cli"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert run(["git", "rev-parse", "HEAD"], cwd=work).stdout == run(
        ["git", "rev-parse", "origin/main"], cwd=work
    ).stdout
    assert "# local dirty regression" in local_script.read_text()


def test_image_build_does_not_patch_tracked_neurocommand_scripts():
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text()
    fetch_script_rewrites = [
        line
        for line in dockerfile.splitlines()
        if "sed -i" in line and "neurodesk/fetch_" in line
    ]

    assert fetch_script_rewrites == []

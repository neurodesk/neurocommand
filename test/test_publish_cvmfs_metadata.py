import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "scripts" / "publish_cvmfs_metadata.sh"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$1 $2" in
    "pr list") cat "$GH_STATE/open" 2>/dev/null || true ;;
    "pr create") echo 17 > "$GH_STATE/open" ;;
    "pr close") rm -f "$GH_STATE/open" ;;
    "pr merge")
        # Branch protection rejects the first attempt while checks run.
        if [[ ! -f "$GH_STATE/pending" ]]; then
            touch "$GH_STATE/pending"
            exit 1
        fi
        ;;
esac
"""


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _apps(version):
    return {"tool": {"apps": {"tool 1.0": {"version": version}}, "categories": ["a"]}}


def _write_apps(repo, version):
    (repo / "neurodesk" / "apps.json").write_text(json.dumps(_apps(version)))


def _fixture(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(seed))
    (seed / "neurodesk").mkdir()
    (seed / "cvmfs").mkdir()
    for name in ("generate_metadata.py", "json_gen.py"):
        shutil.copy(ROOT / "cvmfs" / name, seed / "cvmfs" / name)
    _write_apps(seed, "20250628")
    subprocess.run(
        ["python3", "cvmfs/generate_metadata.py", "--applist", "cvmfs/applist.json"],
        cwd=seed,
        check=True,
    )
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "published 20250628")
    _git(seed, "push", "origin", "HEAD:main")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    state = tmp_path / "gh-state"
    state.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_LOG": str(tmp_path / "gh.log"),
        "GH_STATE": str(state),
        "CVMFS_METADATA_MERGE_RETRY_DELAY": "0",
    }
    return origin, seed, state, env


def _publish(seed, sha, env):
    return subprocess.run(
        ["bash", str(SCRIPT), sha],
        cwd=seed,
        env=env,
        capture_output=True,
        text=True,
    )


def test_publishes_uploaded_revision_and_merges_the_exact_head(tmp_path):
    origin, seed, state, env = _fixture(tmp_path)
    _write_apps(seed, "20261010")
    _git(seed, "commit", "-am", "apps.json bump")
    uploaded = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "origin", "HEAD:main")
    # main moves on to an image the upload job has not handled yet.
    _write_apps(seed, "20261012")
    _git(seed, "commit", "-am", "newer apps.json bump")
    _git(seed, "push", "origin", "HEAD:main")

    result = _publish(seed, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    head = _git(origin, "rev-parse", "bot/cvmfs-metadata")
    published = _git(origin, "show", "bot/cvmfs-metadata:cvmfs/log.txt")
    assert published == "tool_1.0_20261010 categories:a,"
    applist = json.loads(_git(origin, "show", "bot/cvmfs-metadata:cvmfs/applist.json"))
    assert applist["list"][0]["application"] == "tool_1.0_20261010"
    assert _git(origin, "rev-parse", "bot/cvmfs-metadata^") == _git(
        origin, "rev-parse", "main"
    )
    calls = (tmp_path / "gh.log").read_text().splitlines()
    merges = [call for call in calls if call.startswith("pr merge")]
    assert merges == [f"pr merge 17 --squash --match-head-commit {head}"] * 2


def test_unmerged_metadata_pr_fails_the_job(tmp_path):
    origin, seed, state, env = _fixture(tmp_path)
    _write_apps(seed, "20261010")
    _git(seed, "commit", "-am", "apps.json bump")
    _git(seed, "push", "origin", "HEAD:main")

    result = _publish(
        seed,
        _git(seed, "rev-parse", "HEAD"),
        {**env, "CVMFS_METADATA_MERGE_ATTEMPTS": "1"},
    )

    assert result.returncode == 1
    assert "was not merged" in result.stdout


def test_up_to_date_metadata_closes_a_stale_pr_without_pushing(tmp_path):
    origin, seed, state, env = _fixture(tmp_path)
    (state / "open").write_text("17\n")

    result = _publish(seed, _git(seed, "rev-parse", "HEAD"), env)

    assert result.returncode == 0, result.stderr + result.stdout
    calls = (tmp_path / "gh.log").read_text()
    assert "pr close 17" in calls
    assert "pr merge" not in calls
    assert _git(origin, "branch", "--list", "bot/cvmfs-metadata") == ""

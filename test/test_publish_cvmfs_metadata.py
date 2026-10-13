import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "scripts" / "publish_cvmfs_metadata.sh"

# Merging pushes the matched head to main. Optional state files make the first
# merge attempt fail while checks run, fail after main advances, or report
# success without merging (as gh does for an already-merged PR).
FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$1 $2" in
    "pr list") cat "$GH_STATE/open" 2>/dev/null || true ;;
    "pr create")
        echo 17 > "$GH_STATE/open"
        # Another publisher created the PR first.
        if [[ -f "$GH_STATE/create_race" ]]; then
            rm "$GH_STATE/create_race"
            exit 1
        fi
        ;;
    "pr close") rm -f "$GH_STATE/open" ;;
    "pr merge")
        head="$6"
        for mode in pending advance advance_apps stale clobber withdrawn; do
            if [[ -f "$GH_STATE/$mode" ]]; then
                rm "$GH_STATE/$mode"
                case "$mode" in
                    pending) exit 1 ;;
                    withdrawn) echo 3 > "$GH_STATE/publication-status"; exit 1 ;;
                    advance|advance_apps)
                        if [[ "$mode" == advance_apps ]]; then
                            python3 -c 'import json, pathlib, sys; p = pathlib.Path(sys.argv[1]); data = json.loads(p.read_text()); data["tool"]["apps"]["tool 1.0"]["version"] = "20261012"; p.write_text(json.dumps(data))' "$GH_SEED/neurodesk/apps.json"
                        fi
                        git -C "$GH_SEED" commit --quiet --allow-empty -am docs
                        git -C "$GH_SEED" push --quiet origin HEAD:main
                        exit 1
                        ;;
                    stale) exit 0 ;;
                    clobber)
                        git -C "$GH_SEED" push --quiet --force origin HEAD:refs/heads/bot/cvmfs-metadata
                        exit 1
                        ;;
                esac
            fi
        done
        # Like GitHub, refuse a head that is no longer the PR's head.
        [[ "$(git -C "$GH_ORIGIN" rev-parse bot/cvmfs-metadata)" == "$head" ]] || exit 1
        git push --quiet "$GH_ORIGIN" "$head:refs/heads/main"
        rm -f "$GH_STATE/open"
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


def _write_apps(repo, version):
    apps = {"tool": {"apps": {"tool 1.0": {"version": version}}, "categories": ["a"]}}
    (repo / "neurodesk" / "apps.json").write_text(json.dumps(apps))


def _upload(seed, version):
    for store in ("nectar", "aws"):
        (seed.parent / store / f"tool_1.0_{version}.simg").write_text("image")


def _bump(seed, version, uploaded=True):
    if uploaded:
        _upload(seed, version)
    _git(seed, "pull", "--quiet", "--rebase", "origin", "main")
    _write_apps(seed, version)
    _git(seed, "commit", "-am", f"apps.json {version}")
    _git(seed, "push", "origin", "HEAD:main")
    return _git(seed, "rev-parse", "HEAD")


def _fixture(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(seed))
    (seed / "neurodesk").mkdir()
    (seed / "cvmfs").mkdir()
    for name in ("generate_metadata.py", "json_gen.py"):
        shutil.copy(ROOT / "cvmfs" / name, seed / "cvmfs" / name)
    (seed / "cvmfs/check_publication.py").write_text(
        "import os, pathlib, sys\n"
        "state = pathlib.Path(os.environ['GH_STATE'])\n"
        "with (state/'probes').open('a') as output: output.write(pathlib.Path('cvmfs/log.txt').read_text())\n"
        "reject = state/'reject-id'\n"
        "if reject.exists() and reject.read_text() in pathlib.Path('cvmfs/log.txt').read_text(): sys.exit(3)\n"
        "status = state/'publication-status'\n"
        "sys.exit(int(status.read_text()) if status.exists() else 0)\n"
    )
    for store in ("nectar", "aws"):
        (tmp_path / store).mkdir()
    _upload(seed, "20250628")
    _write_apps(seed, "20250628")
    subprocess.run(
        ["python3", "cvmfs/generate_metadata.py", "--applist", "cvmfs/applist.json"],
        cwd=seed,
        check=True,
    )
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "published 20250628")
    _git(seed, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(work))

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
        "GH_ORIGIN": str(origin),
        "GH_SEED": str(seed),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "CVMFS_METADATA_MERGE_RETRY_DELAY": "0",
        "CVMFS_METADATA_OBJECT_STORES": f"file://{tmp_path / 'nectar'} file://{tmp_path / 'aws'}",
    }
    return origin, seed, work, state, env


def _publish(work, sha, env):
    return subprocess.run(
        ["bash", str(SCRIPT), sha],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


def _main_log(origin):
    return _git(origin, "show", "main:cvmfs/log.txt")


def _merges(tmp_path):
    log = tmp_path / "gh.log"
    calls = log.read_text().splitlines() if log.exists() else []
    return [call for call in calls if call.startswith("pr merge")]


def test_publishes_main_inventory_once_checks_pass(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "pending").touch()

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"
    applist = json.loads(_git(origin, "show", "main:cvmfs/applist.json"))
    assert applist["list"][0]["application"] == "tool_1.0_20261010"
    head = _git(origin, "rev-parse", "bot/cvmfs-metadata")
    assert all(f"--match-head-commit {head}" in call for call in _merges(tmp_path))
    assert len(_merges(tmp_path)) == 2


def test_rebuilds_on_main_when_main_advances_during_the_wait(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "advance").touch()

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"
    assert _git(origin, "log", "-1", "--format=%s", "main^") == "docs"
    first, second = (
        re.search(r"--match-head-commit (\S+)", call)[1] for call in _merges(tmp_path)
    )
    assert first != second


def test_restores_the_branch_after_another_publisher_replaces_it(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "clobber").touch()

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"
    assert len(_merges(tmp_path)) == 2


def test_identical_publications_build_the_same_commit(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    first_upload = _bump(seed, "20261010")
    # A workflow change uploads again with the same apps.json.
    (seed / "workflow.txt").write_text("changed")
    _git(seed, "add", "workflow.txt")
    _git(seed, "commit", "-m", "workflow change")
    _git(seed, "push", "origin", "HEAD:main")
    second_upload = _git(seed, "rev-parse", "HEAD")
    heads = []
    for second, uploaded in enumerate((first_upload, second_upload)):
        (state / "pending").touch()
        clock = f"2026-10-13T00:00:0{second}Z"
        result = _publish(
            work,
            uploaded,
            {
                **env,
                "CVMFS_METADATA_MERGE_ATTEMPTS": "1",
                "GIT_AUTHOR_DATE": clock,
                "GIT_COMMITTER_DATE": clock,
            },
        )
        assert result.returncode == 1, result.stderr + result.stdout
        heads.append(_git(origin, "rev-parse", "bot/cvmfs-metadata"))

    assert heads[0] == heads[1]
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"


def test_uses_the_pr_another_publisher_created_first(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "create_race").touch()

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"
    (merge,) = _merges(tmp_path)
    assert merge.startswith("pr merge 17 ")


def test_retries_after_a_concurrent_push_rejects_the_branch(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/usr/bin/env bash\n"
        f"marker={state / 'rejected'}\n"
        'grep -q bot/cvmfs-metadata && [[ ! -f "$marker" ]] && touch "$marker" && exit 1\n'
        "exit 0\n"
    )
    hook.chmod(0o755)

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert (state / "rejected").exists()
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"


def test_merge_reported_for_a_stale_pr_is_not_success(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "stale").touch()

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(_merges(tmp_path)) == 2
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"


def test_newer_apps_json_still_uploading_is_left_to_its_run(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    # main moves on to an image the upload job has not handled yet.
    _bump(seed, "20261012", uploaded=False)

    result = _publish(work, uploaded, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "its upload run publishes it" in result.stdout
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"
    assert _merges(tmp_path) == []


def test_incomplete_upload_of_the_triggering_inventory_fails(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010", uploaded=False)

    result = _publish(work, uploaded, env)

    assert result.returncode == 1
    assert "nectar/tool_1.0_20261010.simg" in result.stdout
    assert "not fully uploaded" in result.stdout
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"
    assert _merges(tmp_path) == []


def test_rerun_of_an_older_run_publishes_the_current_inventory(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    older = _bump(seed, "20261010")
    _bump(seed, "20261012")

    result = _publish(work, older, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _main_log(origin) == "tool_1.0_20261012 categories:a,"


def test_rerun_of_an_older_upload_does_not_roll_back(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    older = _bump(seed, "20261010")
    # A later upload restores the published inventory, so it publishes nothing.
    restored = _bump(seed, "20250628")
    assert _publish(work, restored, env).returncode == 0

    result = _publish(work, older, env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"
    assert _merges(tmp_path) == []


def test_unmerged_metadata_pr_fails_the_job(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "pending").touch()

    result = _publish(work, uploaded, {**env, "CVMFS_METADATA_MERGE_ATTEMPTS": "1"})

    assert result.returncode == 1
    assert "was not merged" in result.stdout
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"


def test_up_to_date_metadata_closes_a_stale_pr_without_pushing(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    (state / "open").write_text("17\n")

    result = _publish(work, _git(seed, "rev-parse", "HEAD"), env)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "pr close 17" in (tmp_path / "gh.log").read_text()
    assert _merges(tmp_path) == []
    assert _git(origin, "branch", "--list", "bot/cvmfs-metadata") == ""


def test_publication_pending_does_not_push_then_retry_promotes(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "publication-status").write_text("3")
    result = _publish(work, uploaded, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "publication deferred" in result.stdout
    assert _git(origin, "branch", "--list", "bot/cvmfs-metadata") == ""
    assert _merges(tmp_path) == []
    (state / "publication-status").unlink()
    result = _publish(work, "", env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _main_log(origin) == "tool_1.0_20261010 categories:a,"


def test_publication_error_and_prolonged_lag_fail_without_pushing(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    for status in (2, 3):
        (state / "publication-status").write_text(str(status))
        result = _publish(work, uploaded, {**env, "CVMFS_METADATA_LAG_SECONDS": "0"})
        assert result.returncode == 1, result.stdout + result.stderr
        assert _git(origin, "branch", "--list", "bot/cvmfs-metadata") == ""
    assert _merges(tmp_path) == []


def test_every_merge_and_rebuild_rechecks_publication(tmp_path):
    _, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "advance").touch()
    result = _publish(work, uploaded, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "probes").read_text().splitlines() == [
        "tool_1.0_20261010 categories:a,"
    ] * 4


def test_ready_snapshot_is_not_reused_while_waiting_for_checks(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    (state / "withdrawn").touch()
    result = _publish(work, uploaded, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_merges(tmp_path)) == 1
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"
    assert (state / "probes").read_text().splitlines() == [
        "tool_1.0_20261010 categories:a,"
    ] * 3


def test_new_candidate_after_base_race_must_pass_its_own_probe(tmp_path):
    origin, seed, work, state, env = _fixture(tmp_path)
    uploaded = _bump(seed, "20261010")
    _upload(seed, "20261012")
    (state / "advance_apps").touch()
    (state / "reject-id").write_text("tool_1.0_20261012")
    result = _publish(work, uploaded, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _main_log(origin) == "tool_1.0_20250628 categories:a,"
    assert (state / "probes").read_text().splitlines() == [
        "tool_1.0_20261010 categories:a,",
        "tool_1.0_20261010 categories:a,",
        "tool_1.0_20261012 categories:a,",
    ]
    assert len(_merges(tmp_path)) == 1

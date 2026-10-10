from pathlib import Path
import copy

import pytest

from test.support.scripts import load_script

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/scripts/consolidate_appsjson_queue.py"
)

queue = load_script("consolidate_appsjson_plan_tests", SCRIPT)

TARGET = "neurodesk/apps.json"
ICONS = "neurodesk/icons/"


def snapshot(number, before, after, date="2026-07-03T06:59:31Z", files=None):
    pr = queue.PullRequest(
        number, date, "source", "https://example.invalid", files or [TARGET]
    )
    source = queue.QueueSource(date, f"pr/{number}", f"#{number}", "source", pr)
    return queue.SourceSnapshot(source, before, after)


def plan(base, snapshots=(), existing=None, has_existing_pr=False, stray_files=False):
    return queue.build_consolidation_plan(
        base,
        base if existing is None else existing,
        snapshots,
        has_existing_pr,
        stray_files,
        TARGET,
        ICONS,
    )


def test_later_sources_override_tools_and_delete_without_replacing_unchanged_tools():
    base = {"keep": {"version": "main"}, "change": {"version": "1"}, "delete": {}}
    existing = {**base, "pending": {"version": "saved"}}
    earlier = snapshot(
        9, base, {**base, "change": {"version": "2"}}, "2026-07-03T06:59:30Z"
    )
    later = snapshot(10, base, {"keep": base["keep"], "change": {"version": "3"}})
    inputs = copy.deepcopy((base, existing, earlier, later))

    result = plan(base, [later, earlier], existing, has_existing_pr=True)

    assert result.payload == {
        "keep": {"version": "main"},
        "pending": {"version": "saved"},
        "change": {"version": "3"},
    }
    assert [source.label for source in result.sources] == ["#9", "#10"]
    assert result.winners == {"change": "#10", "delete": "#10"}
    assert result.source_prs == (earlier.source.pr, later.source.pr)
    assert result.should_have_pr and result.needs_branch_push
    assert (base, existing, earlier, later) == inputs
    result.payload["change"]["version"] = "output mutation"
    assert later.after["change"]["version"] == "3"


def test_equal_utc_timestamps_use_label_order_and_branch_can_win():
    base = {"tool": 1}
    ten = snapshot(10, base, {"tool": 10})
    nine = snapshot(9, base, {"tool": 9}, "2026-07-03T16:59:31+10:00")
    branch_source = queue.QueueSource(
        ten.source.created_at, "bot", "branch `bot`", "bot"
    )
    branch = queue.SourceSnapshot(branch_source, base, {"tool": "branch"})
    result = plan(base, [branch, nine, ten])
    assert [source.label for source in result.sources] == ["#10", "#9", "branch `bot`"]
    assert result.payload == {"tool": "branch"}
    assert result.winners == {"tool": "branch `bot`"}
    assert result.source_prs == (ten.source.pr, nine.source.pr)


def test_merge_base_unchanged_tool_does_not_revert_newer_main_or_existing_change():
    before = {"tool": 1, "edited": 1}
    base = {"tool": 2, "edited": 1}
    existing = {"tool": 3, "edited": 1}
    result = plan(base, [snapshot(1, before, {"tool": 1, "edited": 2})], existing, True)
    assert result.payload == {"tool": 3, "edited": 2}
    assert result.winners == {"edited": "#1"}


@pytest.mark.parametrize(
    "has_existing_pr,existing,stray_files,active,has_pr,push",
    [
        (False, {"tool": 2}, False, False, False, False),
        (True, {"tool": 1}, False, True, False, False),
        (True, {"tool": 2}, False, True, True, False),
        (True, {"tool": 2}, True, True, True, True),
    ],
)
def test_existing_queue_and_empty_queue_branch_decisions(
    has_existing_pr, existing, stray_files, active, has_pr, push
):
    result = plan(
        {"tool": 1},
        existing=existing,
        has_existing_pr=has_existing_pr,
        stray_files=stray_files,
    )
    assert (result.active, result.should_have_pr, result.needs_branch_push) == (
        active,
        has_pr,
        push,
    )
    assert result.payload == (existing if active else {"tool": 1})


def test_single_source_requires_consolidated_branch_even_with_icon_files():
    source = snapshot(1, {"tool": 1}, {"tool": 2}, files=[TARGET, ICONS + "tool.png"])
    result = plan({"tool": 1}, [source])
    assert result.should_have_pr and result.needs_branch_push
    assert result.source_prs == (source.source.pr,)


def test_closure_candidates_exclude_no_changes_and_other_files_even_when_overwritten():
    base = {"tool": 1}
    unchanged = snapshot(1, base, base)
    mixed = snapshot(2, base, {"tool": 2}, files=[TARGET, "README.md"])
    overwritten = snapshot(3, base, {"tool": 3})
    winner = snapshot(4, base, {"tool": 4})
    result = plan(base, [unchanged, mixed, overwritten, winner])
    assert result.source_prs == (overwritten.source.pr, winner.source.pr)
    assert result.winners == {"tool": "#4"}


@pytest.mark.parametrize(
    "status", [None, "blocked (unit tests failed)", "failed (HTTP 405)"]
)
def test_sources_wait_for_merge(status):
    result = plan({"tool": 1}, [snapshot(1, {"tool": 1}, {"tool": 2})])
    assert not result.sources_landed(status)
    assert result.sources_landed("merged")


def test_sources_already_in_main_can_close_without_a_merge():
    result = plan({"tool": 2}, [snapshot(1, {"tool": 1}, {"tool": 2})])
    assert result.active and not result.should_have_pr
    assert result.source_prs
    assert result.sources_landed(None)
    assert not plan({"tool": 2}).sources_landed(None)


def test_missing_and_null_tools_keep_existing_changed_tools_semantics():
    result = plan({}, [snapshot(1, {}, {"null": None})])
    assert result.payload == {}
    assert result.winners == {}
    assert result.source_prs == ()


@pytest.mark.parametrize(
    "test_failure,merge_result,should_close,exit_code",
    [
        ("failing output", "merged", False, 1),
        (None, "failed (HTTP 405)", False, 0),
        (None, "merged", True, 0),
    ],
)
def test_main_keeps_merge_gate_and_notifications(
    test_failure, merge_result, should_close, exit_code, tmp_path, monkeypatch
):
    from argparse import Namespace
    import subprocess

    args = Namespace(
        repo="owner/repo",
        api_url="https://example.invalid",
        base_ref="main",
        target_file=TARGET,
        consolidated_branch="bot/consolidated",
        source_branch="",
        icons_dir=Path("neurodesk/icons"),
        neurocontainers_path=Path("recipes"),
        skip_icon_sync=False,
        merge_consolidated=True,
        merge_method="squash",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("MERGE_TOKEN", "merge-token")
    monkeypatch.setattr(queue, "parse_args", lambda: args)
    monkeypatch.setattr(
        queue,
        "list_open_pull_requests",
        lambda *a: [
            {
                "number": 1,
                "created_at": "2026-07-03T06:59:31Z",
                "title": "update",
                "html_url": "https://example.invalid/1",
            }
        ],
    )
    monkeypatch.setattr(queue, "list_pull_request_files", lambda *a: [TARGET])
    monkeypatch.setattr(queue, "find_open_head_pr", lambda *a: None)
    monkeypatch.setattr(
        queue,
        "run_git",
        lambda a, **kw: subprocess.CompletedProcess(a, 0, "base\n", ""),
    )
    monkeypatch.setattr(
        queue,
        "read_json_from_git",
        lambda ref, path: {"tool": 2 if ref.endswith("pr/1") else 1},
    )
    events = []
    monkeypatch.setattr(
        queue,
        "sync_consolidated_icons",
        lambda **kw: events.append("icons") or ["neurodesk/icons/tool.png"],
    )
    monkeypatch.setattr(
        queue,
        "stage_and_push_branch",
        lambda **kw: events.append(("push", kw["files"])) or True,
    )
    monkeypatch.setattr(
        queue,
        "upsert_consolidated_pr",
        lambda **kw: events.append(("pr", kw["should_exist"], kw["body"])) or 700,
    )
    monkeypatch.setattr(
        queue,
        "post_consolidated_diff_comment",
        lambda **kw: events.append("diff-comment") or "created",
    )
    monkeypatch.setattr(
        queue,
        "run_unit_tests_on_ref",
        lambda ref: events.append(("test", ref)) or test_failure,
    )
    monkeypatch.setattr(
        queue,
        "merge_pull_request",
        lambda **kw: events.append(("merge", kw["token"])) or merge_result,
    )
    monkeypatch.setattr(
        queue,
        "upsert_marker_comment",
        lambda **kw: events.append(("failure-comment", kw["comment_body"])),
    )
    monkeypatch.setattr(
        queue,
        "close_consolidated_source_prs",
        lambda **kw: events.append(("close", [pr.number for pr in kw["source_prs"]])),
    )

    assert queue.main() == exit_code
    assert events[0:2] == ["icons", ("push", [TARGET, "neurodesk/icons/tool.png"])]
    assert events[2][0:2] == ("pr", True)
    assert "`tool` -> #1" in events[2][2]
    assert events[3:5] == [
        "diff-comment",
        ("test", "refs/remotes/origin/bot/consolidated"),
    ]
    assert (
        any(event[0] == "close" for event in events if isinstance(event, tuple))
        == should_close
    )
    if test_failure:
        assert events[5][0] == "failure-comment"
        assert queue.UNIT_TEST_COMMENT_MARKER in events[5][1]
        assert test_failure in events[5][1]
        assert not any(
            event[0] == "merge" for event in events if isinstance(event, tuple)
        )
    else:
        assert events[5] == ("merge", "merge-token")


def test_stale_branch_matching_main_does_not_need_a_pr_or_push():
    result = queue.build_consolidation_plan(
        {"tool": 1},
        {"tool": 1},
        (),
        True,
        False,
        TARGET,
        ICONS,
        existing_branch_outdated=True,
    )
    assert not result.should_have_pr
    assert not result.needs_branch_push


def test_branch_refresh_comparison_reports_git_errors(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        queue,
        "run_git",
        lambda *a, **kw: subprocess.CompletedProcess([], 128, "", "bad revision"),
    )
    with pytest.raises(RuntimeError, match="bad revision"):
        queue.branch_needs_refresh("main", "missing")


def test_stale_consolidated_branch_rebuilds_on_current_main_and_then_reuses_it(
    tmp_path, monkeypatch
):
    from argparse import Namespace
    import json
    import subprocess

    def git(*args, cwd=tmp_path):
        return subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
        ).stdout.strip()

    remote = tmp_path / "remote.git"
    git("init", "--bare", str(remote))
    repo = tmp_path / "checkout"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    git("config", "user.name", "test", cwd=repo)
    git("config", "user.email", "test@example.invalid", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    (repo / "neurodesk").mkdir()
    old_main = {
        "tool": 1,
        "updated_in_main": 1,
        "deleted_in_main": 1,
        "deleted_in_queue": 1,
    }
    (repo / TARGET).write_text(json.dumps(old_main))
    git("add", ".", cwd=repo)
    git("commit", "-m", "old main", cwd=repo)
    git("push", "origin", "main", cwd=repo)
    git("checkout", "-b", "bot/consolidated", cwd=repo)
    queued = {**old_main, "tool": 2}
    del queued["deleted_in_queue"]
    (repo / TARGET).write_text(json.dumps(queued))
    git("commit", "-am", "queued tool", cwd=repo)
    git("push", "origin", "bot/consolidated", cwd=repo)
    git("checkout", "main", cwd=repo)
    current_main = {
        "tool": 3,
        "updated_in_main": 2,
        "added_in_main": 1,
        "deleted_in_queue": 2,
    }
    (repo / TARGET).write_text(json.dumps(current_main))
    (repo / "maintenance").mkdir()
    (repo / "maintenance/check.py").write_text("new shared entrypoint\n")
    workflows = repo / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "test-neurocommand.yml").write_text("new required checks\n")
    git("add", ".", cwd=repo)
    git("commit", "-m", "current CI", cwd=repo)
    git("push", "origin", "main", cwd=repo)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        queue,
        "parse_args",
        lambda: Namespace(
            repo="owner/repo",
            api_url="https://example.invalid",
            base_ref="main",
            target_file=TARGET,
            consolidated_branch="bot/consolidated",
            source_branch="",
            icons_dir=Path("neurodesk/icons"),
            neurocontainers_path=Path("recipes"),
            skip_icon_sync=True,
            merge_consolidated=False,
            merge_method="squash",
        ),
    )
    monkeypatch.setattr(queue, "list_open_pull_requests", lambda *a: [])
    monkeypatch.setattr(queue, "find_open_head_pr", lambda *a: {"number": 700})
    monkeypatch.setattr(queue, "list_pull_request_files", lambda *a: [TARGET])
    monkeypatch.setattr(queue, "upsert_consolidated_pr", lambda **kw: 700)
    monkeypatch.setattr(queue, "post_consolidated_diff_comment", lambda **kw: "updated")
    pushed = []
    real_push = queue.stage_and_push_branch

    def push(**kwargs):
        pushed.append(kwargs)
        return real_push(**kwargs)

    monkeypatch.setattr(queue, "stage_and_push_branch", push)
    assert queue.branch_needs_refresh("main", "bot/consolidated")
    assert queue.main() == 0
    assert len(pushed) == 1
    assert not queue.branch_needs_refresh("main", "bot/consolidated")
    assert (
        git("show", "bot/consolidated:maintenance/check.py", cwd=repo)
        == "new shared entrypoint"
    )
    assert (
        git(
            "show", "bot/consolidated:.github/workflows/test-neurocommand.yml", cwd=repo
        )
        == "new required checks"
    )
    assert json.loads(git("show", f"bot/consolidated:{TARGET}", cwd=repo)) == {
        "tool": 2,
        "updated_in_main": 2,
        "added_in_main": 1,
    }
    assert queue.main() == 0
    assert len(pushed) == 1


def test_incoming_source_overrides_rebased_queue_without_mutating_catalog_inputs():
    before = {"queued": 1, "main_update": 1, "main_delete": 1, "queue_delete": 1}
    base = {"queued": 3, "main_update": 2, "main_added": 1, "queue_delete": 2}
    existing = {"queued": 2, "main_update": 1, "main_delete": 1}
    inputs = copy.deepcopy((base, before, existing))
    rebased = queue.rebase_tool_delta(base, before, existing)
    assert rebased == {"queued": 2, "main_update": 2, "main_added": 1}
    incoming = snapshot(1, before, {**before, "queued": 4})
    result = plan(base, [incoming], existing=rebased, has_existing_pr=True)
    assert result.payload == {"queued": 4, "main_update": 2, "main_added": 1}
    assert result.winners == {"queued": "#1"}
    assert (base, before, existing) == inputs

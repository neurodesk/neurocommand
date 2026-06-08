import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "workflows" / "scripts"
SCRIPT = SCRIPTS / "consolidate_appsjson_queue.py"

# The consolidation script imports sync_neurocontainer_icons from its own
# directory, so make that importable before loading it.
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("consolidate_appsjson_queue", SCRIPT)
consolidate_appsjson_queue = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = consolidate_appsjson_queue
spec.loader.exec_module(consolidate_appsjson_queue)


def test_diff_comment_updates_existing_marker_comment(monkeypatch):
    calls = []

    def fake_paginated_get(api_url, path, token):
        assert path == "/repos/neurodesk/neurocommand/issues/700/comments"
        return [
            {"id": 10, "body": "unrelated comment"},
            {
                "id": 20,
                "body": consolidate_appsjson_queue.DIFF_COMMENT_MARKER + "\nold diff",
            },
        ]

    def fake_github_request(method, api_url, path, token, query=None, payload=None):
        calls.append((method, path, payload))

    monkeypatch.setattr(
        consolidate_appsjson_queue,
        "github_paginated_get",
        fake_paginated_get,
    )
    monkeypatch.setattr(consolidate_appsjson_queue, "github_request", fake_github_request)

    status = consolidate_appsjson_queue.post_consolidated_diff_comment(
        api_url="https://api.github.com",
        repo="neurodesk/neurocommand",
        token="token",
        consolidated_pr_number=700,
        target_file="neurodesk/apps.json",
        appsjson_diff="+new diff",
    )

    assert status == "updated"
    assert calls == [
        (
            "PATCH",
            "/repos/neurodesk/neurocommand/issues/comments/20",
            {
                "body": (
                    consolidate_appsjson_queue.DIFF_COMMENT_MARKER
                    + "\n### Proposed `apps.json` changes\n\n"
                    + "Latest queue consolidation diff for `neurodesk/apps.json`:\n\n"
                    + "```diff\n+new diff\n```"
                )
            },
        )
    ]


def test_diff_comment_unchanged_makes_no_write_request(monkeypatch):
    calls = []

    existing_body = (
        consolidate_appsjson_queue.DIFF_COMMENT_MARKER
        + "\n### Proposed `apps.json` changes\n\n"
        + "Latest queue consolidation diff for `neurodesk/apps.json`:\n\n"
        + "```diff\n+new diff\n```"
    )

    monkeypatch.setattr(
        consolidate_appsjson_queue,
        "github_paginated_get",
        lambda api_url, path, token: [{"id": 20, "body": existing_body}],
    )
    monkeypatch.setattr(
        consolidate_appsjson_queue,
        "github_request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    status = consolidate_appsjson_queue.post_consolidated_diff_comment(
        api_url="https://api.github.com",
        repo="neurodesk/neurocommand",
        token="token",
        consolidated_pr_number=700,
        target_file="neurodesk/apps.json",
        appsjson_diff="+new diff",
    )

    assert status == "unchanged"
    assert calls == []


def test_source_pr_closure_does_not_post_comments(monkeypatch):
    calls = []

    def fake_github_request(method, api_url, path, token, query=None, payload=None):
        calls.append((method, path, payload))

    monkeypatch.setattr(consolidate_appsjson_queue, "github_request", fake_github_request)

    consolidate_appsjson_queue.close_consolidated_source_prs(
        api_url="https://api.github.com",
        repo="neurodesk/neurocommand",
        token="token",
        source_prs=[
            consolidate_appsjson_queue.PullRequest(
                number=699,
                created_at="2026-06-08T15:03:42Z",
                title="Update apps.json from neurocontainers releases",
                html_url="https://github.com/neurodesk/neurocommand/pull/699",
                files=["neurodesk/apps.json"],
            )
        ],
    )

    assert calls == [
        (
            "PATCH",
            "/repos/neurodesk/neurocommand/pulls/699",
            {"state": "closed"},
        )
    ]


def test_closing_empty_queue_pr_does_not_post_comment(monkeypatch):
    calls = []

    monkeypatch.setattr(
        consolidate_appsjson_queue,
        "find_open_head_pr",
        lambda api_url, repo, owner, head_branch, base_ref, token: {"number": 700},
    )

    def fake_github_request(method, api_url, path, token, query=None, payload=None):
        calls.append((method, path, payload))

    monkeypatch.setattr(consolidate_appsjson_queue, "github_request", fake_github_request)

    result = consolidate_appsjson_queue.upsert_consolidated_pr(
        api_url="https://api.github.com",
        repo="neurodesk/neurocommand",
        owner="neurodesk",
        token="token",
        base_ref="main",
        head_branch="bot/appsjson-consolidated",
        should_exist=False,
        title="Consolidate pending apps.json updates",
        body="",
    )

    assert result is None
    assert calls == [
        (
            "PATCH",
            "/repos/neurodesk/neurocommand/pulls/700",
            {"state": "closed"},
        )
    ]

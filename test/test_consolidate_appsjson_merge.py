import importlib.util
import io
import json
from pathlib import Path
import sys
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "workflows" / "scripts"
SCRIPT = SCRIPTS / "consolidate_appsjson_queue.py"

# The consolidation script imports sync_neurocontainer_icons from its own
# directory, so make that importable before loading it.
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("consolidate_appsjson_queue_merge", SCRIPT)
consolidate_appsjson_queue = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = consolidate_appsjson_queue
spec.loader.exec_module(consolidate_appsjson_queue)


def _pr(files):
    return consolidate_appsjson_queue.PullRequest(
        number=740,
        created_at="2026-07-03T06:59:31Z",
        title="Update apps.json from neurocontainers releases",
        html_url="https://example.invalid/pr/740",
        files=files,
    )


def test_pr_with_apps_json_and_icons_is_consolidatable():
    # Generator PRs ship icon PNGs alongside apps.json; they must still be
    # closeable by the consolidation (this is what left the queue stuck).
    pr = _pr(["neurodesk/apps.json", "neurodesk/icons/spm12bi.png"])
    assert pr.consolidatable("neurodesk/apps.json", "neurodesk/icons/")


def test_pr_with_other_files_is_not_consolidatable():
    pr = _pr(["neurodesk/apps.json", "neurodesk/write_log.py"])
    assert not pr.consolidatable("neurodesk/apps.json", "neurodesk/icons/")


def test_parse_timestamp_orders_api_and_git_formats_consistently():
    parse = consolidate_appsjson_queue.parse_timestamp
    api_style = parse("2026-07-03T06:59:31Z")
    git_style = parse("2026-07-03T16:59:31+10:00")
    assert api_style == git_style


def _http_error(code, body=b"{}"):
    return HTTPError(
        url="https://example.invalid/merge",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body),
    )


def test_merge_pull_request_retries_405_then_merges(monkeypatch):
    calls = []

    def fake_request(method, api_url, path, token, query=None, payload=None):
        calls.append((method, path, payload))
        if len(calls) == 1:
            raise _http_error(405, b'{"message": "Base branch was modified"}')
        return {"merged": True}

    monkeypatch.setattr(consolidate_appsjson_queue, "github_request", fake_request)
    monkeypatch.setattr(consolidate_appsjson_queue.time, "sleep", lambda seconds: None)

    status = consolidate_appsjson_queue.merge_pull_request(
        api_url="https://api.example.invalid",
        repo="neurodesk/neurocommand",
        token="tok",
        pr_number=739,
        merge_method="squash",
    )

    assert status == "merged"
    assert len(calls) == 2
    assert all(method == "PUT" and path.endswith("/pulls/739/merge") for method, path, _ in calls)
    assert calls[0][2] == {"merge_method": "squash"}


def test_merge_pull_request_gives_up_after_retries(monkeypatch):
    calls = []

    def fake_request(method, api_url, path, token, query=None, payload=None):
        calls.append(path)
        raise _http_error(405, b'{"message": "not mergeable"}')

    monkeypatch.setattr(consolidate_appsjson_queue, "github_request", fake_request)
    monkeypatch.setattr(consolidate_appsjson_queue.time, "sleep", lambda seconds: None)

    status = consolidate_appsjson_queue.merge_pull_request(
        api_url="https://api.example.invalid",
        repo="neurodesk/neurocommand",
        token="tok",
        pr_number=739,
        merge_method="squash",
        attempts=3,
    )

    assert status.startswith("failed (HTTP 405")
    assert len(calls) == 3


def test_merge_pull_request_does_not_retry_permission_errors(monkeypatch):
    calls = []

    def fake_request(method, api_url, path, token, query=None, payload=None):
        calls.append(path)
        raise _http_error(403, b'{"message": "forbidden"}')

    monkeypatch.setattr(consolidate_appsjson_queue, "github_request", fake_request)

    status = consolidate_appsjson_queue.merge_pull_request(
        api_url="https://api.example.invalid",
        repo="neurodesk/neurocommand",
        token="tok",
        pr_number=739,
        merge_method="squash",
    )

    assert status.startswith("failed (HTTP 403")
    assert len(calls) == 1

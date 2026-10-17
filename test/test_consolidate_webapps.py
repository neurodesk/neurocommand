import copy
from pathlib import Path

from test.support.scripts import load_script


queue = load_script(
    "consolidate_webapps_tests",
    Path(__file__).resolve().parents[1]
    / ".github/workflows/scripts/consolidate_appsjson_queue.py",
)


def webapps(version, **settings):
    return {
        "version": "1.0",
        "webapps": {
            "dicompare": {"module": "dicompare", "version": version, **settings}
        },
    }


def catalog(*versions):
    return {"dicompare": {"apps": {f"dicompare {version}": {} for version in versions}}}


def plan(base, current, generated, *, existing=None, existing_apps=None, snapshots=()):
    return queue.build_consolidation_plan(
        base,
        base if existing_apps is None else existing_apps,
        snapshots,
        has_existing_pr=existing is not None,
        existing_has_non_target_files=False,
        target_file="neurodesk/apps.json",
        icons_prefix="neurodesk/icons/",
        base_webapps=current,
        existing_webapps=current if existing is None else existing,
        generated_webapps=generated,
    )


def test_unavailable_version_preserves_complete_working_configuration_until_ready():
    current = webapps("0.15.3", startup_command="old start", port=3001)
    generated = webapps("0.15.4", startup_command="new start", port=3002)
    inputs = copy.deepcopy((current, generated))

    waiting = plan(catalog("0.15.3"), current, generated)
    assert waiting.webapps == current
    assert not waiting.should_have_pr
    assert not waiting.needs_branch_push

    ready = plan(catalog("0.15.3", "0.15.4"), current, generated)
    assert ready.webapps == generated
    assert ready.should_have_pr and ready.needs_branch_push
    assert (current, generated) == inputs


def test_catalog_and_webapp_version_are_in_the_same_planned_update():
    base = catalog("0.15.3")
    updated = catalog("0.15.3", "0.15.4")
    source = queue.QueueSource("2026-10-17T10:34:57Z", "source", "release", "release")
    result = plan(
        base,
        webapps("0.15.3"),
        webapps("0.15.4"),
        snapshots=[queue.SourceSnapshot(source, base, updated)],
    )
    assert result.payload == updated
    assert result.webapps == webapps("0.15.4")
    assert result.should_have_pr and result.needs_branch_push


def test_identical_events_reuse_pending_update_and_changed_content_refreshes_it():
    apps = catalog("0.15.3", "0.15.4")
    current = webapps("0.15.3")
    generated = webapps("0.15.4", startup_timeout=120)
    first = plan(apps, current, generated)
    repeated = plan(apps, current, generated, existing=first.webapps)
    assert repeated.should_have_pr
    assert not repeated.needs_branch_push

    changed = webapps("0.15.4", startup_timeout=180)
    refreshed = plan(apps, current, changed, existing=first.webapps)
    assert refreshed.webapps == changed
    assert refreshed.should_have_pr and refreshed.needs_branch_push

    landed = plan(apps, changed, changed)
    assert not landed.should_have_pr and not landed.needs_branch_push


def test_new_unavailable_webapp_is_omitted_and_removed_recipe_is_removed():
    current = {
        "version": "1.0",
        "webapps": {"removed": {"module": "gone", "version": "1"}},
    }
    result = plan({}, current, webapps("0.15.4"))
    assert result.webapps == {"version": "1.0", "webapps": {}}
    assert result.should_have_pr


def test_fallback_version_removed_from_catalog_is_not_published():
    result = plan({}, webapps("0.15.3"), webapps("0.15.4"))
    assert result.webapps == {"version": "1.0", "webapps": {}}


def test_release_events_create_one_pr_update_it_and_stop_after_merge(
    tmp_path, monkeypatch
):
    import json
    import subprocess
    import sys

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    repo = tmp_path / "checkout"
    repo.mkdir()
    monkeypatch.chdir(repo)
    git("init", "-b", "main")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    git("remote", "add", "origin", str(remote))
    queue.write_json("neurodesk/apps.json", catalog("0.15.3"))
    queue.write_json("neurodesk/webapps.json", webapps("0.15.3"), indent=2)
    git("add", ".")
    git("commit", "-m", "main")
    git("push", "origin", "main")

    upstream = tmp_path / "neurocontainers"
    (upstream / "tools").mkdir(parents=True)
    desired = upstream / "desired.json"
    desired.write_text(json.dumps(webapps("0.15.4")))
    (upstream / "tools/generate_webapps_json.py").write_text(
        "import argparse\nfrom pathlib import Path\n"
        "parser = argparse.ArgumentParser()\nparser.add_argument('--output')\n"
        "Path(parser.parse_args().output).write_bytes("
        "(Path(__file__).parents[1] / 'desired.json').read_bytes())\n"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "queue",
            "--repo",
            "owner/repo",
            "--skip-icon-sync",
            "--webapps-file",
            "neurodesk/webapps.json",
            "--neurocontainers-path",
            str(upstream),
        ],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    opened = []
    comments = []
    creates = []

    def github(method, api_url, path, token, query=None, payload=None):
        if path == "/repos/owner/repo/pulls":
            if method == "GET":
                return opened
            assert method == "POST" and not opened
            creates.append(payload)
            pr = {
                "number": 700,
                "created_at": "2026-10-17T10:34:57Z",
                "head": {"ref": payload["head"], "repo": {"full_name": "owner/repo"}},
                **payload,
            }
            pr["head"] = {"ref": payload["head"], "repo": {"full_name": "owner/repo"}}
            opened.append(pr)
            return pr
        if path == "/repos/owner/repo/pulls/700/files":
            return [
                {"filename": name}
                for name in git(
                    "diff", "--name-only", "main...bot/appsjson-consolidated"
                ).splitlines()
            ]
        if path == "/repos/owner/repo/pulls/700" and method == "PATCH":
            opened[0].update(payload)
            return opened[0]
        if path == "/repos/owner/repo/issues/700/comments":
            if method == "GET":
                return comments
            comments.append({"id": 800, **payload})
            return comments[-1]
        if path == "/repos/owner/repo/issues/comments/800" and method == "PATCH":
            comments[0].update(payload)
            return comments[0]
        raise AssertionError((method, path))

    monkeypatch.setattr(queue, "github_request", github)
    assert queue.main() == 0
    assert not creates

    git("checkout", "-b", "update-apps-json", "main")
    queue.write_json("neurodesk/apps.json", catalog("0.15.3", "0.15.4"))
    git("commit", "-am", "release")
    git("push", "origin", "update-apps-json")
    git("checkout", "main")
    assert queue.main() == 0
    assert len(creates) == 1
    assert creates[0]["head"] == "bot/appsjson-consolidated"
    assert json.loads(
        git("show", "bot/appsjson-consolidated:neurodesk/apps.json")
    ) == catalog("0.15.3", "0.15.4")
    assert json.loads(
        git("show", "bot/appsjson-consolidated:neurodesk/webapps.json")
    ) == webapps("0.15.4")
    first_head = git("rev-parse", "bot/appsjson-consolidated")

    git("checkout", "main")
    assert queue.main() == 0
    assert len(creates) == 1
    assert git("rev-parse", "bot/appsjson-consolidated") == first_head

    desired.write_text(json.dumps(webapps("0.15.4", startup_timeout=180)))
    assert queue.main() == 0
    assert len(creates) == 1
    assert git("rev-parse", "bot/appsjson-consolidated") != first_head
    assert json.loads(
        git("show", "bot/appsjson-consolidated:neurodesk/webapps.json")
    ) == webapps("0.15.4", startup_timeout=180)

    git("checkout", "main")
    git("merge", "--ff-only", "bot/appsjson-consolidated")
    git("push", "origin", "main")
    opened.clear()
    assert queue.main() == 0
    assert len(creates) == 1

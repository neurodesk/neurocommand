from pathlib import Path
import json

import pytest

from test.support.scripts import load_script

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "scripts" / "cleanup_stale_containers.py"


cleanup = load_script("cleanup_stale_containers", SCRIPT)


def test_release_metadata_protects_named_and_legacy_images(tmp_path):
    releases = tmp_path / "releases"

    named = releases / "workshopdemo_arm64" / "1.0.0.json"
    named.parent.mkdir(parents=True)
    named.write_text(
        json.dumps({"apps": {"workshopdemo_arm64 1.0.0": {"version": "20260721"}}})
    )

    legacy = releases / "amico" / "2.1.0-arm64.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "apps": {
                    "amico 2.1.0 arm64": {
                        "version": "20260512",
                        "image": "amico_2.1.0_arm64",
                    }
                }
            }
        )
    )

    assert cleanup.load_release_keys(releases) == {
        "workshopdemo_arm64_1.0.0_20260721.simg",
        "amico_2.1.0_arm64_20260512.simg",
    }


def test_empty_release_metadata_directory_aborts_cleanup(tmp_path):
    releases = tmp_path / "releases"
    releases.mkdir()

    with pytest.raises(SystemExit, match="No release metadata found"):
        cleanup.load_release_keys(releases)


@pytest.mark.parametrize(
    "release",
    [
        {},
        {"apps": []},
        {"apps": {"demo 1.0": []}},
        {"apps": {"demo 1.0": {"version": "latest"}}},
        {"apps": {"demo 1.0": {"version": "20260231"}}},
        {"apps": {"demo 1.0": {"version": "20260721", "image": ""}}},
        {"apps": {"demo 1.0": {"version": "20260721", "image": "foo.simg"}}},
        {"apps": {"demo 1.0": {"version": "20260721", "image": "dir/foo"}}},
        {"apps": {"demo 1.0": {"version": "20260721", "image": "dir\\foo"}}},
        {"apps": {"demo 1.0": {"version": "20260721", "image": " foo"}}},
    ],
)
def test_invalid_release_metadata_aborts_cleanup(tmp_path, release):
    release_path = tmp_path / "releases" / "demo" / "1.0.json"
    release_path.parent.mkdir(parents=True)
    release_path.write_text(json.dumps(release))

    with pytest.raises(SystemExit, match="Invalid release metadata"):
        cleanup.load_release_keys(tmp_path / "releases")


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("skip_generation", [False, True])
def test_main_preserves_every_referenced_generation(
    tmp_path, monkeypatch, capsys, apply, skip_generation
):
    published = tmp_path / "published.txt"
    published.write_text("batchheudiconv_1.0.0_20250628 categories:workflow,\n")
    monkeypatch.setattr(cleanup, "PUBLISHED_LOG", published, raising=False)
    current = tmp_path / "current.txt"
    current.write_text("batchheudiconv_1.0.0_20261010\n")
    release = tmp_path / "releases" / "demo" / "1.0.json"
    release.parent.mkdir(parents=True)
    release.write_text(json.dumps({"apps": {"demo": {"version": "20250101"}}}))
    keys = [
        "batchheudiconv_1.0.0_20250628.simg",
        "batchheudiconv_1.0.0_20261010.simg",
        "demo_1.0_20250101.simg",
        "obsolete_1.0_20200101.simg",
        "recent_1.0_20261011.simg",
        "undated_1.0_20200101.simg",
    ]
    dates = ["2000-01-01T00:00:00Z"] * 4 + ["2999-01-01T00:00:00Z", ""]
    calls = []

    def check_output(command, **kwargs):
        calls.append(command)
        if command[0] == "rclone":
            return json.dumps(
                [{"Path": key, "ModTime": date} for key, date in zip(keys, dates)]
            )
        assert command[:3] == ["aws", "s3api", "list-objects-v2"]
        return json.dumps(
            {
                "Contents": [
                    {"Key": key, "LastModified": date} for key, date in zip(keys, dates)
                ]
            }
        )

    monkeypatch.setattr(cleanup.subprocess, "check_output", check_output)
    monkeypatch.setattr(cleanup.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(cleanup, "check_orphaned_segments", lambda *args: None)
    generated = []

    def generate(path):
        generated.append(path)
        published.write_text("overwritten_1.0_20261010\n")

    monkeypatch.setattr(cleanup, "generate_log", generate)
    args = [
        "cleanup",
        "--s3-bucket",
        "fake",
        "--log-path",
        str(current),
        "--releases-dir",
        str(release.parents[1]),
    ]
    if apply:
        args.append("--apply")
    if skip_generation:
        args.append("--skip-log-generation")
    monkeypatch.setattr("sys.argv", args)
    cleanup.main()
    deletions = [cmd for cmd in calls if "deletefile" in cmd or "delete-object" in cmd]
    expected = [
        ["rclone", "deletefile", "nectar:/neurodesk/obsolete_1.0_20200101.simg"],
        [
            "aws",
            "s3api",
            "delete-object",
            "--bucket",
            "fake",
            "--key",
            "obsolete_1.0_20200101.simg",
            "--no-cli-pager",
        ],
    ]
    assert deletions == (expected if apply else [])
    output = capsys.readouterr().out
    if not apply:
        assert "Would delete 1 stale object(s) from Nectar" in output
        assert "Would delete 1 stale object(s) from AWS" in output
    assert generated == ([] if skip_generation else [current])


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        b"",
        b" \n\t",
        b"\xff",
        b"<html>Error</html>",
        b"valid_1.0_20250101\nbroken\n",
        b"bad_1.0_20260231\n",
        b"../bad_1.0_20250101",
        b"dir\\bad_1.0_20250101",
        b"good_1.0_20250101 categories:[broken",
        b"good_1.0_20250101 garbage",
    ],
)
@pytest.mark.parametrize("skip_generation", [False, True])
def test_invalid_published_snapshot_aborts_before_any_operation(
    tmp_path, monkeypatch, snapshot, skip_generation
):
    published = tmp_path / "published.txt"
    if snapshot is not None:
        published.write_bytes(snapshot)
    monkeypatch.setattr(cleanup, "PUBLISHED_LOG", published, raising=False)
    calls = []
    for name in [
        "generate_log",
        "list_nectar_objects",
        "list_s3_objects",
        "check_orphaned_segments",
    ]:
        monkeypatch.setattr(cleanup, name, lambda *args: calls.append(args))
    monkeypatch.setattr(
        cleanup.subprocess, "run", lambda *args, **kw: calls.append(args)
    )
    args = ["cleanup", "--s3-bucket", "fake", "--apply"]
    if skip_generation:
        args.append("--skip-log-generation")
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit, match="published inventory"):
        cleanup.main()
    assert calls == []


def test_published_inventory_accepts_supported_names_and_metadata(tmp_path):
    published = tmp_path / "published.txt"
    published.write_text(
        "\n  tool_arm64_1.0+rc-2_20250101.simg categories:image reconstruction,other,  \n"
        "tool_arm64_1.0+rc-2_20250101 categories:image reconstruction,\n"
        "another_1.0_20240229\n"
    )
    assert cleanup.load_published_keys(published) == {
        "tool_arm64_1.0+rc-2_20250101.simg",
        "another_1.0_20240229.simg",
    }


def test_committed_published_inventory_is_valid():
    assert cleanup.PUBLISHED_LOG == ROOT / "cvmfs" / "log.txt"
    assert cleanup.load_published_keys(cleanup.PUBLISHED_LOG)


def test_unreadable_published_inventory_aborts_before_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "PUBLISHED_LOG", tmp_path)
    monkeypatch.setattr(
        cleanup, "generate_log", lambda path: pytest.fail("generated log")
    )
    with pytest.raises(SystemExit, match="Cannot read published inventory"):
        cleanup.load_protected_keys(tmp_path / "current", None, generate=True)


def test_invalid_configured_releases_abort_before_generation(tmp_path, monkeypatch):
    published = tmp_path / "published.txt"
    published.write_text("demo_1.0_20250101\n")
    monkeypatch.setattr(cleanup, "PUBLISHED_LOG", published)
    monkeypatch.setattr(
        cleanup, "generate_log", lambda path: pytest.fail("generated log")
    )
    with pytest.raises(SystemExit, match="Releases directory not found"):
        cleanup.load_protected_keys(
            tmp_path / "current", tmp_path / "missing", generate=True
        )


def test_aliased_generated_log_keeps_published_snapshot(tmp_path, monkeypatch):
    published = tmp_path / "published.txt"
    published.write_text("old_1.0_20250101\n")
    monkeypatch.setattr(cleanup, "PUBLISHED_LOG", published)
    monkeypatch.setattr(
        cleanup, "generate_log", lambda path: path.write_text("new_1.0_20261010\n")
    )
    assert cleanup.load_protected_keys(published, None, generate=True) == {
        "old_1.0_20250101.simg",
        "new_1.0_20261010.simg",
    }


def test_cleanup_workflow_requires_success_or_manual_dispatch():
    workflow = (ROOT / ".github/workflows/cleanup_neurocontainers.yml").read_text()
    assert (
        "  cleanup_containers_simg:\n"
        "    if: ${{ github.event_name == 'workflow_dispatch' || "
        "github.event.workflow_run.conclusion == 'success' }}\n"
    ) in workflow

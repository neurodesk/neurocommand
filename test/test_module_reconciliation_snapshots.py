from pathlib import Path
import os

import pytest

from test.support.scripts import load_script

SCRIPT = Path(__file__).resolve().parents[1] / "cvmfs/reconcile_module_files.py"

reconciler = load_script("module_snapshot_reconciler", SCRIPT)


def planned_change(path, *, delete=False):
    changes = {}
    if delete:
        reconciler.add_delete(changes, path, "retired module")
    else:
        reconciler.add_change(changes, path, "generated update\n", "new inventory")
    return changes[path]


@pytest.mark.parametrize("delete", [False, True])
@pytest.mark.parametrize(
    "mutation", ["content", "mode", "identity", "removed", "symlink"]
)
def test_changed_existing_target_refuses_entire_plan(tmp_path, delete, mutation):
    first = tmp_path / "first"
    first.write_text("untouched\n")
    target = tmp_path / "target"
    target.write_text("original\n")
    changes = [planned_change(first), planned_change(target, delete=delete)]
    if mutation == "content":
        target.write_text("site edit\n")
    elif mutation == "mode":
        target.chmod(0o400)
    elif mutation == "identity":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(target.read_bytes())
        os.replace(replacement, target)
    elif mutation == "removed":
        target.unlink()
    else:
        destination = tmp_path / "destination"
        destination.write_text("site destination\n")
        target.unlink()
        target.symlink_to(destination)
    with pytest.raises(RuntimeError, match="changed since planning"):
        reconciler.apply_changes(changes)
    assert first.read_text() == "untouched\n"
    if mutation == "content":
        assert target.read_text() == "site edit\n"
    if mutation == "symlink":
        assert target.is_symlink()
        assert target.read_text() == "site destination\n"


@pytest.mark.parametrize("kind", ["file", "dangling-symlink", "directory"])
def test_new_target_appearance_is_preserved(tmp_path, kind):
    target = tmp_path / "new"
    change = planned_change(target)
    if kind == "file":
        target.write_text("site module\n")
    elif kind == "dangling-symlink":
        target.symlink_to(tmp_path / "absent")
    else:
        target.mkdir()
    with pytest.raises(RuntimeError, match="changed since planning"):
        reconciler.apply_changes([change])
    if kind == "file":
        assert target.read_text() == "site module\n"
    elif kind == "dangling-symlink":
        assert target.is_symlink()
    else:
        assert target.is_dir()


def test_unchanged_plan_creates_updates_and_deletes_preserving_mode(tmp_path):
    update = tmp_path / "update"
    update.write_text("old\n")
    update.chmod(0o640)
    delete = tmp_path / "delete"
    delete.write_text("retired\n")
    create = tmp_path / "nested/new"
    changes = [
        planned_change(update),
        planned_change(delete, delete=True),
        planned_change(create),
    ]
    reconciler.apply_changes(changes)
    assert update.read_text() == create.read_text() == "generated update\n"
    assert update.stat().st_mode & 0o777 == 0o640
    assert not delete.exists()


def test_revising_planned_content_retains_original_snapshot(tmp_path):
    target = tmp_path / "module"
    target.write_text("original\n")
    changes = {}
    reconciler.add_change(changes, target, "first pass\n", "inventory")
    target.write_text("site edit\n")
    reconciler.add_change(changes, target, "second pass\n", "manual snippet")
    with pytest.raises(RuntimeError, match="changed since planning"):
        reconciler.apply_changes(list(changes.values()))
    assert target.read_text() == "site edit\n"


def test_edit_between_applications_reports_completed_changes(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("old first\n")
    second.write_text("old second\n")
    changes = [planned_change(first), planned_change(second)]
    write = reconciler.write_artifact

    def edit_next_target(path, content):
        write(path, content)
        second.write_text("concurrent edit\n")

    monkeypatch.setattr(reconciler, "write_artifact", edit_next_target)
    with pytest.raises(
        RuntimeError, match="after 1 applied change.*changed since planning"
    ):
        reconciler.apply_changes(changes)
    assert first.read_text() == "generated update\n"
    assert second.read_text() == "concurrent edit\n"


def test_io_failure_does_not_claim_rollback_or_success(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("old first\n")
    second.write_text("old second\n")
    changes = [planned_change(first), planned_change(second)]
    write = reconciler.write_artifact

    def fail_second(path, content):
        if path == second:
            raise OSError("disk full")
        write(path, content)

    monkeypatch.setattr(reconciler, "write_artifact", fail_second)
    with pytest.raises(
        RuntimeError, match="after 1 applied change.*disk full"
    ) as failure:
        reconciler.apply_changes(changes)
    assert isinstance(failure.value.__cause__, OSError)
    assert first.read_text() == "generated update\n"
    assert second.read_text() == "old second\n"


def test_plan_retains_snapshot_used_to_render_module(tmp_path, monkeypatch):
    image = "demo_1.0_20260629"
    container = tmp_path / "containers" / image
    container.mkdir(parents=True)
    (container / "commands.txt").write_text("demo\n")
    module = tmp_path / "containers/modules/demo/1.0.lua"
    module.parent.mkdir(parents=True)
    module.write_text('prepend_path("PATH", "/old/demo_1.0_20250101")\n')
    log = tmp_path / "log"
    log.write_text(image + " categories:data,\n")
    read = reconciler.read_snapshot
    edited = False

    def edit_after_read(path):
        nonlocal edited
        snapshot = read(path)
        if path == module and not edited:
            module.write_text('setenv("SITE", "concurrent")\n')
            edited = True
        return snapshot

    monkeypatch.setattr(reconciler, "read_snapshot", edit_after_read)
    changes = reconciler.plan_module_reconciliation(tmp_path, log)
    with pytest.raises(RuntimeError, match="changed since planning"):
        reconciler.apply_changes(changes)
    assert module.read_text() == 'setenv("SITE", "concurrent")\n'
    assert not (tmp_path / "neurodesk-modules").exists()


def test_read_does_not_treat_access_time_change_as_edit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    target = tmp_path / "module"
    target.write_text("unchanged\n")
    fstat = os.fstat
    calls = 0

    def update_atime(descriptor):
        nonlocal calls
        value = fstat(descriptor)
        fields = {
            field: getattr(value, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        calls += 1
        return SimpleNamespace(**fields, st_atime=calls)

    monkeypatch.setattr(reconciler.os, "fstat", update_atime)
    assert reconciler.read_snapshot(target).content == b"unchanged\n"
    assert calls == 2

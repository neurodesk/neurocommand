import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cvmfs" / "reconcile_module_files.py"

spec = importlib.util.spec_from_file_location("reconcile_module_files", SCRIPT)
reconcile_module_files = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reconcile_module_files
spec.loader.exec_module(reconcile_module_files)


def module_text(container_name):
    return "\n".join(
        [
            "-- -*- lua -*-",
            f'whatis("{container_name}")',
            f'prepend_path("PATH", "/cvmfs/neurodesk.ardc.edu.au/containers/{container_name}")',
            f'setenv("DATALAD_HOME", "/cvmfs/neurodesk.ardc.edu.au/containers/{container_name}/opt")',
            "",
        ]
    )


def make_container(repo_root, container_name, commands="datalad\n"):
    container = repo_root / "containers" / container_name
    container.mkdir(parents=True)
    (container / "commands.txt").write_text(commands)


def test_reconciles_current_public_modules_and_removes_stale_categories(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    old_container = "datalad_1.3.1_20260227"
    latest_container = "datalad_1.3.1_20260512"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, old_container)
    make_container(repo_root, latest_container)
    log_path.write_text(f"{latest_container} categories:data organisation,\n")

    canonical = repo_root / "containers" / "modules" / "datalad" / "1.3.1.lua"
    current_public = (
        repo_root / "neurodesk-modules" / "data_organisation" / "datalad" / "1.3.1.lua"
    )
    old_category_public = (
        repo_root / "neurodesk-modules" / "old_category" / "datalad" / "1.3.1.lua"
    )
    for module_file in (canonical, current_public, old_category_public):
        module_file.parent.mkdir(parents=True)
        module_file.write_text(module_text(old_container))

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    for module_file in (canonical, current_public):
        text = module_file.read_text()
        assert latest_container in text
        assert old_container not in text
        assert "-- neurodesk-exposed-commands" in text
        assert 'extensions("datalad/1.3.1")' in text

    assert current_public.read_text() == canonical.read_text()
    assert not old_category_public.exists()


def test_latest_build_comes_from_log_not_leftover_container_dirs(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    kept_container = "demo_1.0_20260512"
    leftover_newer_container = "demo_1.0_20260601"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, kept_container)
    make_container(repo_root, leftover_newer_container)
    log_path.write_text(f"{kept_container} categories:data organisation,\n")

    canonical = repo_root / "containers" / "modules" / "demo" / "1.0.lua"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(module_text("demo_1.0_20260227"))

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert kept_container in text
    assert leftover_newer_container not in text


def test_current_category_module_is_created_from_canonical(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    latest_container = "datalad_1.3.1_20260512"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, latest_container)
    log_path.write_text(f"{latest_container} categories:data organisation,\n")

    canonical = repo_root / "containers" / "modules" / "datalad" / "1.3.1.lua"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(module_text(latest_container))

    public = repo_root / "neurodesk-modules" / "data_organisation" / "datalad" / "1.3.1.lua"
    assert not public.exists()

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    assert public.read_text() == canonical.read_text()


def test_reconciliation_sanitizes_lmod_cache_delimiter_in_help_text(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    latest_container = "gigaconnectome_0.6.0_20250630"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, latest_container)
    log_path.write_text(f"{latest_container} categories:bids apps,\n")

    canonical = repo_root / "containers" / "modules" / "gigaconnectome" / "0.6.0.lua"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "\n".join(
            [
                "-- -*- lua -*-",
                "help([===[",
                "usage: giga_connectome [--participant-label LABEL [LABEL ...]]",
                "]===])",
                f'whatis("{latest_container}")',
                (
                    "prepend_path(\"PATH\", "
                    f'"/cvmfs/neurodesk.ardc.edu.au/containers/{latest_container}")'
                ),
                "",
            ]
        )
    )

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert "[LABEL ...] ]" in text
    assert "]]" not in text
    assert "]===])" in text


def test_reconciliation_updates_exposed_commands_and_preserves_other_extensions(
    tmp_path,
):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    latest_container = "demo_1.0_20260512"
    log_path = tmp_path / "log.txt"

    make_container(
        repo_root,
        latest_container,
        "zeta\nalpha\nalpha\nbad/name\nbad command\nbad,command\n",
    )
    log_path.write_text(f"{latest_container} categories:programming,\n")

    canonical = repo_root / "containers" / "modules" / "demo" / "1.0.lua"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        module_text(latest_container) + 'extensions("python-package/2.0")\n'
    )

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert text.count("-- neurodesk-exposed-commands") == 1
    assert 'extensions("alpha/1.0, zeta/1.0")' in text
    assert 'extensions("python-package/2.0")' in text

    (repo_root / "containers" / latest_container / "commands.txt").write_text(
        "beta\n"
    )
    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert text.count("-- neurodesk-exposed-commands") == 1
    assert 'extensions("beta/1.0")' in text
    assert "alpha/1.0" not in text
    assert "zeta/1.0" not in text
    assert 'extensions("python-package/2.0")' in text


def test_reconciliation_removes_managed_extensions_for_empty_inventory(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    latest_container = "demo_1.0_20260512"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, latest_container, "")
    log_path.write_text(f"{latest_container} categories:programming,\n")

    canonical = repo_root / "containers" / "modules" / "demo" / "1.0.lua"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        module_text(latest_container)
        + "-- neurodesk-exposed-commands\n"
        + 'extensions("old-command/1.0")\n'
    )

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert "neurodesk-exposed-commands" not in text
    assert "old-command/1.0" not in text


def test_check_mode_exit_status_distinguishes_drift(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    old_container = "datalad_1.3.1_20260227"
    latest_container = "datalad_1.3.1_20260512"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, latest_container)
    log_path.write_text(f"{latest_container} categories:data organisation,\n")

    canonical = repo_root / "containers" / "modules" / "datalad" / "1.3.1.lua"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(module_text(old_container))

    assert (
        reconcile_module_files.main(
            ["--repo-root", str(repo_root), "--log", str(log_path), "--check"]
        )
        == 1
    )

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    assert (
        reconcile_module_files.main(
            ["--repo-root", str(repo_root), "--log", str(log_path), "--check"]
        )
        == 0
    )

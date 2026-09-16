import importlib.util
from pathlib import Path
import sys

import pytest


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


def test_parse_image_name_supports_named_variants():
    assert reconcile_module_files.parse_image_name(
        "fsl_gpu_arm64_6.0.7_20260721.simg"
    ) == ("fsl_gpu_arm64", "6.0.7", "20260721")


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
        assert 'whatis("Commands: datalad")' in text

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
        module_text(latest_container)
        + "-- neurodesk-exposed-commands\n"
        + 'extensions("old-command/1.0")\n'
        + 'extensions("python-package/2.0")\n'
    )

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert text.count("-- neurodesk-exposed-commands") == 1
    assert 'if type(extensions) == "function" then' not in text
    assert 'whatis("Commands: alpha, bad,command, zeta")' in text
    assert "old-command/1.0" not in text
    assert 'extensions("python-package/2.0")' in text

    (repo_root / "containers" / latest_container / "commands.txt").write_text(
        "beta\n"
    )
    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert text.count("-- neurodesk-exposed-commands") == 1
    assert 'whatis("Commands: beta")' in text
    assert "alpha" not in text
    assert "zeta" not in text
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


@pytest.mark.parametrize(
    "filename, legacy_block, preserved",
    [
        (
            "2.1.3.lua",
            '-- neurodesk-exposed-commands\nextensions("panopticacli/2.1.3")\n',
            'whatis("Panoptica")\nextensions("unrelated/1.0")\n'
            '-- neurodesk-exposed-commands\nwhatis("Commands: existing")\n',
        ),
        (
            "2.1.3.lua",
            '-- neurodesk-exposed-commands\nif type(extensions) == "function" then\n'
            '    extensions("panopticacli/2.1.3")\nend\n',
            'whatis("Panoptica")\nprepend_path("PATH", "/retired/container")\n',
        ),
        (
            "2.1.3",
            '# neurodesk-exposed-commands\nextensions "panopticacli/2.1.3"\n',
            '#%Module\nmodule-whatis "Panoptica"\nextensions "unrelated/1.0"\n'
            '# neurodesk-exposed-commands\nmodule-whatis "Commands: existing"\n',
        ),
        (
            "2.1.3",
            '# neurodesk-exposed-commands\n'
            'if {[llength [info commands extensions]] > 0} {\n'
            '    extensions "panopticacli/2.1.3"\n}\n',
            '#%Module\nmodule-whatis "Panoptica"\nprepend-path PATH /retired/container\n',
        ),
    ],
)
@pytest.mark.parametrize("listed_without_inventory", [False, True])
def test_cleans_legacy_extensions_outside_active_containers(
    tmp_path, filename, legacy_block, preserved, listed_without_inventory
):
    repo_root = tmp_path / "cvmfs"
    log_path = tmp_path / "log.txt"
    log_path.write_text(
        "panoptica_2.1.3_20260728 categories:image segmentation,quality control,\n"
        if listed_without_inventory else ""
    )
    paths = [
        repo_root / "containers/modules/panoptica" / filename,
        repo_root / "neurodesk-modules/image_segmentation/panoptica" / filename,
        repo_root / "neurodesk-modules/quality_control/panoptica" / filename,
    ]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_text(preserved + legacy_block)

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    assert {change.path for change in changes} == set(paths)
    reconcile_module_files.apply_changes(changes)
    for path in paths:
        assert path.read_text() == preserved
    assert reconcile_module_files.plan_module_reconciliation(repo_root, log_path) == []


def test_reconciliation_uses_tcl_whatis_for_legacy_modulefiles(tmp_path):
    repo_root = tmp_path / "cvmfs" / "neurodesk.ardc.edu.au"
    latest_container = "vesselboost_1.0.0_20240815"
    log_path = tmp_path / "log.txt"

    make_container(repo_root, latest_container, "boost.py\nprediction.py\n")
    log_path.write_text(f"{latest_container} categories:quantitative imaging,\n")

    canonical = repo_root / "containers" / "modules" / "vesselboost" / "1.0.0"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "-- neurodesk-exposed-commands\n"
        'extensions("old-command/1.0.0")\n'
        "#%Module####################################################################\n"
        f"module-whatis  {latest_container}.simg\n"
        "prepend-path PATH /cvmfs/neurodesk.ardc.edu.au/containers/"
        f"{latest_container}\n"
    )

    changes = reconcile_module_files.plan_module_reconciliation(repo_root, log_path)
    reconcile_module_files.apply_changes(changes)

    text = canonical.read_text()
    assert text.startswith("#%Module")
    assert "# neurodesk-exposed-commands\n" in text
    assert "extensions" not in text
    assert 'module-whatis "Commands: boost.py, prediction.py"\n' in text
    assert "-- neurodesk-exposed-commands" not in text
    assert 'extensions("' not in text


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


def test_existing_freesurfer_module_gets_corrected_bind_paths(tmp_path):
    container = "freesurfer_8.2.0_20260818"
    make_container(tmp_path, container, "freeview\n")
    log = tmp_path / "log.txt"
    log.write_text(f"{container} categories:structural imaging,\n")
    module = tmp_path / "containers/modules/freesurfer/8.2.0.lua"
    module.parent.mkdir(parents=True)
    snippet = (ROOT / "neurodesk/transparent-singularity/manual_module_files/freesurfer").read_text()
    module.write_text(module_text(container) + snippet.replace("/tmp,/scratch", "/tmp:/scratch"))

    changes = reconcile_module_files.plan_module_reconciliation(tmp_path, log)
    reconcile_module_files.apply_changes(changes)

    assert 'local additional_bind_paths = "/tmp,/scratch"' in module.read_text()
    assert "/tmp:/scratch" not in module.read_text()


@pytest.mark.parametrize("active", [True, False])
def test_manual_snippet_changes_reach_existing_modules(tmp_path, active):
    repo_root = tmp_path / "cvmfs"
    snippets = tmp_path / "manual_module_files"
    snippets.mkdir()
    source = ROOT / "neurodesk/transparent-singularity/manual_module_files/freesurfer"
    current = source.read_text()
    (snippets / "freesurfer").write_text(current)
    container = "freesurfer_8.2.0_20260818"
    make_container(repo_root, container, "freeview\n")
    log = tmp_path / "log.txt"
    log.write_text(
        f"{container} categories:structural imaging,image segmentation,\n" if active else ""
    )
    paths = [
        repo_root / "containers/modules/freesurfer/8.2.0.lua",
        repo_root / "neurodesk-modules/structural_imaging/freesurfer/8.2.0.lua",
        repo_root / "neurodesk-modules/image_segmentation/freesurfer/8.2.0.lua",
    ]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_text(module_text(container) + current.replace("/tmp,/scratch", "/tmp:/scratch"))
    args = ["--repo-root", str(repo_root), "--log", str(log),
            "--manual-module-dir", str(snippets)]

    before = [path.read_text() for path in paths]
    assert reconcile_module_files.main(args + ["--check"]) == 1
    assert [path.read_text() for path in paths] == before
    assert reconcile_module_files.main(args) == 0
    for path in paths:
        assert 'local additional_bind_paths = "/tmp,/scratch"' in path.read_text()
        assert "/tmp:/scratch" not in path.read_text()
        assert path.read_text().count("local additional_bind_paths") == 1
    assert reconcile_module_files.main(args + ["--check"]) == 0

    (snippets / "freesurfer").write_text(
        'whatis("Custom description")\nsetenv("SNIPPET_VERSION", "toolVersion")\n'
    )
    assert reconcile_module_files.main(args + ["--check"]) == 1
    assert reconcile_module_files.main(args) == 0
    for path in paths:
        assert 'setenv("SNIPPET_VERSION", "8.2.0")' in path.read_text()
        assert "additional_bind_paths" not in path.read_text()
        assert 'setenv("DATALAD_HOME"' in path.read_text()
        if active:
            assert 'whatis("Commands: freeview")' in path.read_text()
    assert reconcile_module_files.main(args + ["--check"]) == 0
    (snippets / "freesurfer").unlink()
    assert reconcile_module_files.main(args + ["--check"]) == 1
    assert reconcile_module_files.main(args) == 0
    for path in paths:
        assert "SNIPPET_VERSION" not in path.read_text()
        assert 'setenv("DATALAD_HOME"' in path.read_text()
    assert reconcile_module_files.main(args + ["--check"]) == 0


def test_new_snippet_updates_orphan_public_lua_but_not_tcl(tmp_path):
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    log = tmp_path / "log.txt"
    log.write_text("")
    directory = tmp_path / "neurodesk-modules/programming/demo"
    directory.mkdir(parents=True)
    lua = directory / "1.0.lua"
    lua.write_text('whatis("demo")\n')
    tcl = directory / "1.0"
    tcl.write_text('#%Module\nmodule-whatis "demo"\n')
    (snippets / "demo").write_text('setenv("CUSTOM_VERSION", "toolVersion")')
    args = ["--repo-root", str(tmp_path), "--log", str(log),
            "--manual-module-dir", str(snippets)]

    assert reconcile_module_files.main(args) == 0
    assert 'setenv("CUSTOM_VERSION", "1.0")\n' in lua.read_text()
    assert tcl.read_text() == '#%Module\nmodule-whatis "demo"\n'
    assert reconcile_module_files.main(args + ["--check"]) == 0


def test_matlab_legacy_migration_preserves_help_and_license_mapping(tmp_path):
    snippet = (ROOT / "neurodesk/transparent-singularity/manual_module_files/matlab").read_text()
    help_text = 'help([===[\n' + snippet + ']===])\n'
    module = tmp_path / "containers/modules/matlab/2025b.lua"
    module.parent.mkdir(parents=True)
    module.write_text(help_text + 'whatis("MATLAB")\n' + snippet.replace("toolVersion", "2025b"))
    log = tmp_path / "log.txt"
    log.write_text("")

    changes = reconcile_module_files.plan_module_reconciliation(tmp_path, log)
    reconcile_module_files.apply_changes(changes)

    content = module.read_text()
    assert content.startswith(help_text + 'whatis("MATLAB")\n')
    assert content.count('local additional_bind_paths') == 2
    assert 'os.getenv("HOME") .. ":/opt/matlab/R2025b/licenses"' in content
    assert reconcile_module_files.plan_module_reconciliation(tmp_path, log) == []


@pytest.mark.parametrize("failure", ["missing-source", "unclosed-block", "duplicate-block"])
def test_invalid_snippet_inputs_do_not_partially_write_modules(tmp_path, failure):
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    (snippets / "demo").write_text('setenv("CUSTOM", "new")\n')
    log = tmp_path / "log.txt"
    log.write_text("")
    directory = tmp_path / "containers/modules/demo"
    directory.mkdir(parents=True)
    good = directory / "1.0.lua"
    bad = directory / "2.0.lua"
    good.write_text('whatis("unchanged until validation succeeds")\n')
    block = '-- neurodesk-manual-module-begin\nsetenv("CUSTOM", "old")\n'
    bad.write_text(
        (block + '-- neurodesk-manual-module-end\n') * 2
        if failure == "duplicate-block" else block
    )
    if failure == "missing-source":
        snippets = tmp_path / "absent"
    before = [good.read_text(), bad.read_text()]
    args = ["--repo-root", str(tmp_path), "--log", str(log),
            "--manual-module-dir", str(snippets)]

    assert reconcile_module_files.main(args) == 2
    assert [good.read_text(), bad.read_text()] == before
